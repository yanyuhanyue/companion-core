"""Real read-only source permissions, committed WAL, and private plaintext cleanup."""

import os
import re
import sqlite3
import stat
import subprocess
import sys
import tempfile
from contextlib import closing, contextmanager
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from companion import backup as backup_module
from companion import operator_config
from companion.config import Settings
from companion.contracts import CoreError


def windows_sid():
    result = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"],
                            capture_output=True, check=True, timeout=10)
    return re.search(rb"S-1-[0-9-]+", result.stdout).group().decode("ascii")


def icacls(*args):
    subprocess.run(["icacls", *map(str, args)], capture_output=True, check=True, timeout=10)


@contextmanager
def readonly_directory(path):
    """Deny new entries on this test's own source directory; always restore access."""
    if os.name == "nt":
        sid = "*" + windows_sid()
        icacls(path, "/deny", sid + ":(W)")
        try:
            yield
        finally:
            icacls(path, "/remove:d", sid)
            # This fresh fixture was mkdir(mode=0o700): restore its owner's full
            # inheritable grant, which /deny removes as well as adding a deny ACE.
            icacls(path, "/grant:r", sid + ":(OI)(CI)F")
    else:
        previous = stat.S_IMODE(path.stat().st_mode)
        path.chmod(0o500)
        try:
            yield
        finally:
            path.chmod(previous)


def assert_private_directory(path, acl_file):
    if os.name == "nt":
        # Python >=3.12.4 implements mkdir(mode=0o700) with a protected DACL.
        # Inspect SIDs rather than localized account names. SYSTEM/Admin retain OS access.
        icacls(path, "/save", acl_file)
        acl = acl_file.read_text("utf-16-le").lstrip("\ufeff")
        allowed = set(re.findall(r"\(A;[^()]*;;;([^)]+)\)", acl))
        # OW is the owner's rights SID used by Python's protected mkdir DACL.
        assert {windows_sid(), "OW"} & allowed
        assert allowed <= {windows_sid(), "OW", "SY", "BA"}
        assert re.search(r"^D:P", acl, flags=re.MULTILINE)
    else:
        assert stat.S_IMODE(path.stat().st_mode) == 0o700
        assert path.stat().st_uid == os.geteuid()


@pytest.mark.parametrize("failure", [None, "encrypt", "fsync"])
def test_readonly_source_committed_wal_and_private_plaintext_cleanup(
    tmp_path, monkeypatch, failure,
):
    source_dir, encrypted_dir, private_root = [tmp_path / name for name in
                                              ("source", "encrypted-only", "os-temp")]
    for directory in (source_dir, encrypted_dir, private_root):
        directory.mkdir(mode=0o700)
    for name in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(name, str(private_root))
    monkeypatch.setattr(tempfile, "tempdir", None)
    # Windows os.access ignores this DACL, so mkdtemp retries PermissionError
    # TMP_MAX times. Bound only this fixture's negative case; the real ACL stays enforced.
    monkeypatch.setattr(tempfile, "TMP_MAX", 1)
    created, plaintext_seen = [], []
    temporary_directory = tempfile.TemporaryDirectory

    @contextmanager
    def observe_directory(*args, **kwargs):
        with temporary_directory(*args, **kwargs) as directory:
            path = Path(directory)
            created.append(path)
            assert path.parent == private_root
            assert_private_directory(path, tmp_path / "private-acl.txt")
            yield directory

    monkeypatch.setattr(tempfile, "TemporaryDirectory", observe_directory)
    encrypt = Fernet.encrypt

    def observe_plaintext(cipher, value):
        snapshot = created[0] / "snapshot.sqlite3"
        assert snapshot.is_file() and snapshot.read_bytes() == value
        assert value.startswith(b"SQLite format 3")
        assert_private_directory(created[0], tmp_path / "plaintext-acl.txt")
        plaintext_seen.append(True)
        if failure == "encrypt":
            raise RuntimeError("synthetic encryption failure")
        return encrypt(cipher, value)

    monkeypatch.setattr(Fernet, "encrypt", observe_plaintext)
    if failure == "fsync":
        def fail_fsync(_):
            raise OSError("synthetic destination fsync failure")
        monkeypatch.setattr(backup_module.os, "fsync", fail_fsync)

    database, archive = source_dir / "core.sqlite3", encrypted_dir / "backup.enc"
    writer = sqlite3.connect(database)
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("CREATE TABLE facts (value TEXT)")
        writer.commit()
        writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        writer.execute("INSERT INTO facts VALUES ('committed-in-wal')")
        writer.commit()
        # Ignoring the live WAL proves the base DB does not contain the committed row.
        with closing(sqlite3.connect(f"file:{database.as_posix()}?immutable=1", uri=True)) as base:
            assert base.execute("SELECT value FROM facts").fetchall() == []
        writer.execute("INSERT INTO facts VALUES ('uncommitted')")
        wal = Path(str(database) + "-wal")
        before = {path: path.read_bytes() for path in (database, wal)}
        key = Fernet.generate_key()
        with readonly_directory(source_dir):
            if os.name != "nt" and os.access(source_dir, os.W_OK):
                pytest.skip("Read-only directory permissions are not enforced for this user")
            with pytest.raises(PermissionError):
                (source_dir / "write-must-fail").mkdir()
            with pytest.raises(CoreError, match="BACKUP_IMMUTABLE_SOURCE_HAS_SIDECARS"):
                backup_module.backup(database, archive, key, immutable_source=True)
            assert not archive.exists()
            if failure:
                expected = RuntimeError if failure == "encrypt" else OSError
                with pytest.raises(expected, match="synthetic"):
                    backup_module.backup(database, archive, key)
            else:
                backup_module.backup(database, archive, key)
                ciphertext = archive.read_bytes()
                with pytest.raises(CoreError, match="BACKUP_DESTINATION_EXISTS"):
                    backup_module.backup(database, archive, key)
                assert archive.read_bytes() == ciphertext
            assert {path: path.read_bytes() for path in before} == before
        assert len(created) == 1 and plaintext_seen == [True]
        assert not created[0].exists() and not list(private_root.iterdir())
        assert sorted(path.name for path in encrypted_dir.iterdir()) == ["backup.enc"]
        if failure == "encrypt":
            # Existing behavior: xb reserves the path before encryption; an empty
            # failed destination must still require a new backup path.
            assert archive.read_bytes() == b""
        if failure:
            with pytest.raises(CoreError, match="BACKUP_DESTINATION_EXISTS"):
                backup_module.backup(database, archive, key)
        if failure != "encrypt":
            ciphertext = archive.read_bytes()
            assert not ciphertext.startswith(b"SQLite")
            inspection = tmp_path / "decrypted-fixture.sqlite3"
            inspection.write_bytes(Fernet(key).decrypt(ciphertext))
            with closing(sqlite3.connect(inspection)) as snapshot:
                assert snapshot.execute("SELECT value FROM facts").fetchall() == [
                    ("committed-in-wal",)]
    finally:
        writer.rollback()
        writer.close()


def test_immutable_backup_of_stopped_readonly_wal_source(tmp_path):
    source_dir = tmp_path / "source"
    source_dir.mkdir(mode=0o700)
    database, archive = source_dir / "core.sqlite3", tmp_path / "backup.enc"
    with closing(sqlite3.connect(database)) as writer:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.execute("CREATE TABLE facts (value TEXT)")
        writer.execute("INSERT INTO facts VALUES ('committed-before-stop')")
        writer.commit()
    sidecars = [Path(str(database) + suffix) for suffix in ("-wal", "-shm", "-journal")]
    assert not any(path.exists() for path in sidecars)
    before = database.read_bytes()
    key = Fernet.generate_key()
    with readonly_directory(source_dir):
        if os.name != "nt" and os.access(source_dir, os.W_OK):
            pytest.skip("Read-only directory permissions are not enforced for this user")
        with pytest.raises(PermissionError):
            (source_dir / "write-must-fail").mkdir()
        backup_module.backup(database, archive, key, immutable_source=True)
        env = {name: value for name, value in os.environ.items()
               if not name.startswith("COMPANION_")}
        env.update(COMPANION_CONFIG=str(tmp_path / "absent.toml"),
                   COMPANION_DATABASE=str(database), COMPANION_MODE="demo",
                   COMPANION_OWNER_QQ_ID="10001", COMPANION_BOT_QQ_ID="10002",
                   COMPANION_BRIDGE_TOKEN="synthetic-stopped-backup-token",
                   COMPANION_BACKUP_KEY=key.decode())
        subprocess.run([sys.executable, "-B", "-m", "companion", "backup",
                        str(tmp_path / "cli-backup.enc"), "--immutable-source"],
                       env=env, capture_output=True, check=True, timeout=30)
        assert database.read_bytes() == before
        assert not any(path.exists() for path in sidecars)
    for backup_path in (archive, tmp_path / "cli-backup.enc"):
        inspection = tmp_path / (backup_path.stem + ".sqlite3")
        inspection.write_bytes(Fernet(key).decrypt(backup_path.read_bytes()))
        with closing(sqlite3.connect(inspection)) as snapshot:
            assert snapshot.execute("SELECT value FROM facts").fetchall() == [
                ("committed-before-stop",)]


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
@pytest.mark.parametrize("payload", [b"", b"synthetic-stale-sidecar"])
def test_immutable_backup_rejects_each_sidecar_before_reading(tmp_path, suffix, payload):
    database, archive = tmp_path / "core.sqlite3", tmp_path / "backup.enc"
    with closing(sqlite3.connect(database)) as writer:
        writer.execute("CREATE TABLE facts (value TEXT)")
        writer.commit()
    before = database.read_bytes()
    sidecar = Path(str(database) + suffix)
    sidecar.write_bytes(payload)
    with pytest.raises(CoreError, match="BACKUP_IMMUTABLE_SOURCE_HAS_SIDECARS"):
        backup_module.backup(database, archive, Fernet.generate_key(), immutable_source=True)
    assert not archive.exists()
    assert database.read_bytes() == before
    assert sidecar.read_bytes() == payload


def test_settings_load_keeps_runtime_overrides_and_deployment_custody(core, monkeypatch, tmp_path):
    password = "synthetic-configuration-layering-token"
    core.settings.control_token = SecretStr(password)
    operator_config.update(
        core.store, core.settings,
        operator_config.ConfigurationChange(
            expected_version=0, changes={"max_output_tokens": 1024},
        ),
        "o1-configuration-layering", core.clock(),
    )
    for name in tuple(os.environ):
        if name.startswith("COMPANION_"):
            monkeypatch.delenv(name)
    values = {
        "CONFIG": str(tmp_path / "absent.toml"), "DATABASE": core.settings.database,
        "ERASURE_LEDGER": core.settings.erasure_ledger, "MODE": "demo",
        "OWNER_QQ_ID": core.settings.owner_qq_id, "BOT_QQ_ID": core.settings.bot_qq_id,
        "BRIDGE_TOKEN": core.settings.bridge_token.get_secret_value(), "CONTROL_TOKEN": password,
        "MAX_OUTPUT_TOKENS": "768",
    }
    for name, value in values.items():
        monkeypatch.setenv("COMPANION_" + name, value)
    runtime = Settings.load()
    assert runtime.max_output_tokens == 1024 and runtime._operator_config_version == 1
    deployment = Settings.load(apply_operator_config=False)
    assert deployment.max_output_tokens == 768 and deployment._operator_config_version == 0
    assert runtime.database == deployment.database == core.settings.database
    assert runtime.erasure_ledger == deployment.erasure_ledger == core.settings.erasure_ledger
