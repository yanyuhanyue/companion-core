"""Check release archives and initialize a database using only the extracted wheel."""

import argparse
import os
import subprocess
import sys
import tarfile
import tempfile
import textwrap
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
    assert not any(part.endswith(".owner-images")
                   for name in archive.getnames() for part in Path(name).parts)
    assert not any("output/playwright/" in n for n in archive.getnames())
    assert not any(Path(n).suffix.lower() in {".node", ".dll", ".so", ".enc"}
                   for n in archive.getnames())


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
            "custom.db.owner-images/canary.raw",
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


def verify_meme_protocol(destination, env=None):
    """Import the extracted contract without either application's runtime dependencies."""
    script = textwrap.dedent("""
        import importlib.abc
        import sys
        from pathlib import Path

        destination = Path(sys.argv[1]).resolve()
        sys.path.insert(0, str(destination))
        blocked = {"companion", "companion_onebot_bridge", "sqlite3", "sqlalchemy",
                   "httpx", "fastapi", "fastembed", "uvicorn"}

        class RuntimeDependencyGuard(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname.split(".", 1)[0] in blocked:
                    raise AssertionError("Shared protocol imported runtime dependency: " + fullname)

        sys.meta_path.insert(0, RuntimeDependencyGuard())
        import companion_meme_protocol as shared
        assert Path(shared.__file__).resolve().is_relative_to(destination)
        assert shared.caption_model_schema()["title"] == "CaptionResult"
        blocked.remove("companion_onebot_bridge")
        from companion_onebot_bridge import meme_caption, meme_library, meme_semantic
        assert meme_caption.CaptionResult is shared.CaptionResult
        assert meme_caption.sample_frames is shared.sample_frames
        assert meme_semantic.caption_text is shared.caption_text
        assert meme_library.PreparedImage is shared.PreparedImage
        assert shared.validate_bytes(b"invalid image") is None
        assert "companion" not in sys.modules and "sqlite3" not in sys.modules
    """)
    subprocess.run([sys.executable, "-I", "-c", script, str(destination)], cwd=destination,
                   env=env, check=True, timeout=30)


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
        assert not any(part.endswith(".owner-images")
                       for name in archive.namelist() for part in Path(name).parts)
        assert not any(Path(n).suffix.lower() in {".node", ".dll", ".so", ".enc"}
                       for n in archive.namelist())
        assert {
            "companion/media_web/index.html",
            "companion/media_web/app.js",
            "companion/media_web/style.css",
            "source_reader/__main__.py",
            "source_reader/network.py",
            "source_reader/THIRD_PARTY_NOTICES.txt",
            "source_reader/accounts.py",
            "source_reader/heybox_source.py",
            "source_reader/bili_tools.py",
            "companion_onebot_bridge/__main__.py",
            "companion_onebot_bridge/spool.py",
            "companion_meme_protocol/__init__.py",
            "companion/telemetry.py",
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
            verify_meme_protocol(destination, env)
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
