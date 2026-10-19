"""Initialize the real SDK in an isolated container without running a model task."""

import os
import tempfile
from pathlib import Path


def main():
    if os.name != "posix" or os.geteuid() != 10002 or not Path("/.dockerenv").exists():
        raise RuntimeError("Run this check in the dedicated unprivileged executor container")
    from deepseek_harness import DeepSeekHarness

    with tempfile.TemporaryDirectory(dir="/scratch", prefix="sdk-check-") as directory:
        root = Path(directory)
        (root / "work").mkdir()
        os.environ.update(HOME=directory, TMPDIR=directory)
        with DeepSeekHarness(
            profile="sdk-minimal",
            provider="deepseek-official",
            model="verification-only",
            max_tokens=128,
            cwd=str(root / "work"),
            dsh_home=str(root / "home"),
            base_url="http://127.0.0.1:9",
            api_key="not-a-provider-credential",
            initialize_timeout_seconds=20,
            request_timeout_seconds=5,
            shutdown_timeout_seconds=1,
        ):
            print("PASS: isolated native SDK initialization; no model task")


if __name__ == "__main__":
    main()
