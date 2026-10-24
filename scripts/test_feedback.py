"""Local pytest-xdist/testmon entry point; the plugins own scheduling and impact data."""

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from filelock import FileLock, Timeout

from scripts.ci_policy import CRITICAL_DIRS, CRITICAL_FILES, safe_path

ROOT = Path(__file__).resolve().parents[1]


def impact_environment(root, workers):
    """Native testmon environment invalidation for inputs coverage cannot observe."""
    names = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root, timeout=30,
    ).decode("utf-8").rstrip("\0").split("\0")
    if not names or any(not safe_path(name) for name in names):
        raise ValueError("Incomplete impact input inventory")
    names = sorted(set(names))
    inventory = [(name, (root / name).is_file()) for name in names]
    digest = hashlib.sha256(json.dumps([sys.version, sys.platform, workers, inventory]).encode())
    for name in names:
        # New/deleted paths change the inventory. Non-Python data, fixtures and
        # native subprocess boundaries do not rely on in-process coverage edges.
        if (not name.endswith(".py") or name in CRITICAL_FILES
            or name.startswith(CRITICAL_DIRS + ("scripts/", "source_reader/"))
            or name.endswith("conftest.py")
            or name.startswith("tests/") and not Path(name).name.startswith("test_")
            or name.startswith("companion/") and (
                "worker" in Path(name).stem or Path(name).name == "__main__.py")):
            digest.update(name.encode())
            path = root / name
            digest.update(path.read_bytes() if path.is_file() else b"<deleted>")
    return digest.hexdigest()


def cache_readable(path):
    if not path.exists():
        return True
    try:
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as database:
            return database.execute("PRAGMA quick_check").fetchone() == ("ok",)
    except sqlite3.Error:
        return False


def prepare_model(root):
    """Download/check pinned files once in the parent; do not create an ONNX session."""
    os.environ["HF_HUB_DISABLE_XET"] = "1"
    os.environ["HF_XET_CACHE"] = str(root / ".local/huggingface-xet")
    from huggingface_hub import snapshot_download
    from huggingface_hub.errors import LocalEntryNotFoundError

    from companion.retrieval import REPOSITORY, REVISION

    cache = root / "data/embedding-cache"
    options = dict(repo_id=REPOSITORY, revision=REVISION, cache_dir=str(cache),
                   allow_patterns=["*.json", "*.txt", "model_optimized.onnx"], max_workers=2)
    try:
        path = Path(snapshot_download(**options, local_files_only=True))
        if not (path / "model_optimized.onnx").is_file():
            raise LocalEntryNotFoundError("Incomplete model snapshot")
    except LocalEntryNotFoundError:
        path = Path(snapshot_download(**options))
    if not (path / "model_optimized.onnx").is_file():
        raise RuntimeError("Pinned embedding snapshot is incomplete")
    return str(path)


def run(args, *, root=ROOT):
    started = time.monotonic()
    command = [sys.executable, "-m", "pytest", "-q", "-n", str(args.workers)]
    if args.workers:
        command += ["--dist=loadfile"]
    env = dict(os.environ)
    env["HF_HUB_DISABLE_XET"] = "1"
    env["HF_XET_CACHE"] = str(root / ".local/huggingface-xet")
    reason, data = "explicit-tests-or-full", None
    if args.impact:
        try:
            environment = impact_environment(root, args.workers)
            directory = root / ".local/testmon"
            directory.mkdir(parents=True, exist_ok=True)
            if not directory.resolve().is_relative_to(root.resolve()):
                raise ValueError("Impact cache must remain in workspace")
            data = directory / (environment + ".sqlite")
            if not cache_readable(data):
                raise ValueError("Unreadable impact cache")
            env["TESTMON_DATAFILE"] = str(data)
            command += ["--testmon", "--testmon-env=" + environment]
            if args.full:
                command += ["--testmon-noselect"]
            reason = ("testmon-full-refresh" if args.full else "testmon-observed-impact"
                      if data.exists() else "testmon-cold-full-scope")
        except (OSError, ValueError, subprocess.SubprocessError):
            data = None
            reason = "full-scope-fallback-unavailable-impact-cache-or-inputs"
            command += ["--no-testmon"]
    else:
        command += ["--no-testmon"]
    command += args.pytest_args

    def execute():
        if not args.skip_model_preparation:
            prepare_model(root)
        print(json.dumps({"selection": reason, "workers": args.workers,
                          "scope": args.pytest_args, "impact_data": str(data) if data else None}),
              flush=True)
        return subprocess.call(command, cwd=root, env=env)

    try:
        # Different controllers cannot rewrite the same testmon database together.
        # Workers within this controller use testmon's supported xdist sync.
        if data is not None:
            with FileLock(str(data) + ".lock", timeout=0):
                code = execute()
        else:
            code = execute()
    except Timeout:
        print("Impact cache is busy; retry later or run without -Impact", file=sys.stderr)
        return 2
    print(json.dumps({"selection": reason, "workers": args.workers, "exit_code": code,
                      "elapsed_seconds": round(time.monotonic() - started, 3)}), flush=True)
    return code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=2, choices=range(0, 33))
    parser.add_argument("--impact", action="store_true")
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--skip-model-preparation", action="store_true")
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.pytest_args[:1] == ["--"]:
        args.pytest_args.pop(0)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
