"""Check release archives and initialize a database using only the extracted wheel."""

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


def main():
    wheel = ROOT / f"dist/companion_core-{__version__}-py3-none-any.whl"
    source = ROOT / f"dist/companion_core-{__version__}.tar.gz"
    forbidden = {
        ".local",
        ".env",
        "config.toml",
        ".codex-remote-attachments",
        "__pycache__",
        ".playwright-cli",
    }
    with tarfile.open(source) as archive:
        assert not any(forbidden.intersection(Path(n).parts) for n in archive.getnames())
        assert not any("output/playwright/" in n for n in archive.getnames())
    with zipfile.ZipFile(wheel) as archive:
        assert not any(forbidden.intersection(Path(n).parts) for n in archive.namelist())
        assert {
            "companion/media_web/index.html",
            "companion/media_web/app.js",
            "companion/media_web/style.css",
        } <= set(archive.namelist())
        temporary = ROOT / ".local"
        temporary.mkdir(exist_ok=True)
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
    print("PASS: archives exclude private inputs; installed wheel initializes its migrations")


if __name__ == "__main__":
    main()
