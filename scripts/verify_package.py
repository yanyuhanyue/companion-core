"""Check release archives and initialize a database using only the extracted wheel."""

import argparse
import os
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from companion import __version__  # noqa: E402

FORBIDDEN = {
    ".local",
    ".venv",
    ".env",
    "config.toml",
    "data",
    "privacy",
    "backups",
    ".codex-remote-attachments",
    "__pycache__",
    ".playwright-cli",
}


def verify_source(archive):
    assert not any(FORBIDDEN.intersection(Path(n).parts) for n in archive.getnames())
    assert not any("output/playwright/" in n for n in archive.getnames())


def verify_export_rebuild(source, temporary):
    # Git ignore rules are absent in user source exports and may differ in worktrees.
    with tempfile.TemporaryDirectory(prefix="export-package-check-", dir=temporary) as folder:
        destination = Path(folder).resolve()
        with tarfile.open(source) as archive:
            archive.extractall(destination, filter="data")
        exported = destination / f"companion_core-{__version__}"
        for name in (
            ".env",
            "config.toml",
            ".local/canary.txt",
            "data/canary.sqlite3",
            "privacy/canary.jsonl",
            "backups/canary.enc",
        ):
            target = exported / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("SYNTHETIC_PACKAGING_CANARY_NOT_A_SECRET", encoding="utf-8")
        rebuilt = destination / "rebuilt"
        subprocess.run(
            ["uv", "build", "--sdist", "--project", str(exported), "--out-dir", str(rebuilt)],
            cwd=destination,
            check=True,
            timeout=120,
        )
        with tarfile.open(rebuilt / source.name) as archive:
            verify_source(archive)
    print("PASS: exported source rebuild excludes private inputs without Git metadata")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist-dir", type=Path, default=ROOT / "dist")
    distribution = parser.parse_args().dist_dir.resolve()
    wheel = distribution / f"companion_core-{__version__}-py3-none-any.whl"
    source = distribution / f"companion_core-{__version__}.tar.gz"
    temporary = ROOT / ".local"
    temporary.mkdir(exist_ok=True)
    with tarfile.open(source) as archive:
        verify_source(archive)
    verify_export_rebuild(source, temporary)
    with zipfile.ZipFile(wheel) as archive:
        assert not any(FORBIDDEN.intersection(Path(n).parts) for n in archive.namelist())
        assert {
            "companion/media_web/index.html",
            "companion/media_web/app.js",
            "companion/media_web/style.css",
            "source_reader/__main__.py",
            "source_reader/network.py",
        } <= set(archive.namelist())
        with tempfile.TemporaryDirectory(prefix="package-check-", dir=temporary) as folder:
            destination = Path(folder).resolve()
            assert destination.is_relative_to(temporary.resolve())
            archive.extractall(destination)
            env = {k: v for k, v in os.environ.items() if not k.startswith("COMPANION_")}
            env.update(
                COMPANION_OWNER_QQ_ID="10001",
                COMPANION_BOT_QQ_ID="10002",
                COMPANION_BRIDGE_TOKEN="package-verification-token-32-chars",
                COMPANION_DATABASE=str(destination / "check.db"),
                COMPANION_ERASURE_LEDGER=str(destination / "erasures.jsonl"),
                PYTHONDONTWRITEBYTECODE="1",
                PYTHONPATH=str(destination),
            )
            subprocess.run(
                [sys.executable, "-m", "companion", "init"],
                cwd=destination,
                env=env,
                check=True,
                timeout=30,
            )
            subprocess.run(
                [sys.executable, "-m", "source_reader", "--help"],
                cwd=destination,
                env=env,
                check=True,
                timeout=30,
            )
    print("PASS: archives exclude private inputs; installed wheel initializes its migrations")


if __name__ == "__main__":
    main()
