"""Conservative CI selection and the final required-result check (stdlib only)."""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
FULL_JOBS = ("test", "container", "browser-reader", "plugin-native")
# Each fast-path evidence file names the checks that validate its actual contents.
# Unknown JSON and changes to these checks still require full CI.
DOC_EVIDENCE = {
    "docs/design-v0.6/p4-gates.json": ("tests/test_p4_gates.py",),
    "docs/qq-beta/p8b-gates.json": ("tests/test_p8b_evidence.py",),
    "docs/qq-beta/p8c-readiness.json": ("tests/test_p8c_readiness.py",),
    "docs/qq-beta/p8c1-gates.json": ("tests/test_p8c1_gates.py",),
    "docs/qq-beta/p8c1-contracts-gates.json": ("tests/test_p8c1_contracts_gates.py",),
    "docs/qq-beta/p10-contracts-evidence.json": ("tests/test_p10_contracts_evidence.py",),
    "docs/qq-beta/p12-gates.json": (
        "tests/test_p12_beta_operations.py::"
        "test_private_beta_configuration_and_baseline_never_approve_runtime",
    ),
    "docs/qq-beta/p13-gates.json": ("tests/test_p13_gates.py",),
}
# Docs mode stops on privacy failure before later validators can echo document values.
DOC_TESTS = tuple(dict.fromkeys((
    "tests/test_p8c_public_materials.py", "tests/test_ci_policy.py",
    *(check for checks in DOC_EVIDENCE.values() for check in checks),
)))
CRITICAL_FILES = {
    "Dockerfile", "pyproject.toml", "uv.lock", "compose.yaml", "config.example.toml",
    "scripts/ci_policy.py", "tests/test_ci_policy.py", "AGENTS.md",
    "companion/config.py", "companion/contracts.py", "companion/source_policy.py",
    "companion/sources.py", "companion/privacy.py", "companion/erasure.py",
    "companion/budget.py", "companion/paid_budget.py", "companion/store.py",
    "companion/transport.py", "companion/source_grants.py", "companion/worker_lock.py",
    "companion/api.py", "companion/policy.py", "companion/backup.py", "companion/core.py",
    "companion/runtime.py", "companion/model.py", "companion/memory.py",
    "companion/live_text.py",
    "companion/retrieval.py", "companion/observation.py", "companion/content.py",
    "companion/executive.py", "companion/executive_protocol.py", "companion/executor_gateway.py",
    "companion/executor_budget_proxy.py", "companion/executor_files.py", "companion/dsh_job.py",
    "source_reader/accounts.py", "source_reader/network.py", "source_reader/worker.py",
}
CRITICAL_DIRS = (".github/", "contracts/", "migrations/", "db/", "deploy/",
                 "companion_onebot_bridge/", "companion_meme_protocol/", "companion_voice_node/",
                 "companion_delivery_protocol/",
                 "astrbot_plugin_companion_bridge/")


def safe_path(name):
    return (
        isinstance(name, str) and bool(name)
        and not any(ord(char) < 32 or ord(char) == 127 for char in name)
        and "\\" not in name and not name.startswith("/")
        and ".." not in PurePosixPath(name).parts and PurePosixPath(name).as_posix() == name
    )


def choose_profile(event_name, ref, event, files):
    default = event.get("repository", {}).get("default_branch")
    if event_name in {"workflow_dispatch", "merge_group"}:
        return "full", "integration-or-manual"
    if not default or ref.startswith("refs/tags/"):
        return "full", "protected-ref"
    if event_name not in {"push", "pull_request"} or not files:
        return "full", "unknown-change-set"
    if any(not safe_path(name) for name in files):
        return "full", "unrecognized-path"
    if any(name in CRITICAL_FILES or name.startswith(CRITICAL_DIRS)
           or name.endswith((".lock", ".Dockerfile")) for name in files):
        return "full", "critical-change"
    if all(name in DOC_EVIDENCE or (
        name.endswith(".md") and (name == "README.md" or name.startswith("docs/"))
    ) for name in files):
        return "docs", "documentation-only"
    if ref == "refs/heads/" + default:
        return "full", "protected-ref"
    if event_name == "pull_request":
        return "full", "pr-merge-preview"
    return "full", "unrecognized-change"


def changed_files(event_name, event, root=ROOT):
    """Use the complete Git diff, including deletions and both sides of renames."""
    try:
        if event_name == "pull_request":
            base = event["pull_request"]["base"]["sha"]
            head = event["pull_request"]["head"]["sha"]
            separator = "..."
        elif event_name == "push":
            base, head, separator = event["before"], event["after"], ".."
            if base == "0" * 40:
                default = event["repository"]["default_branch"]
                if not re.fullmatch(r"[A-Za-z0-9_./-]+", default) or ".." in default:
                    return None
                base, separator = "refs/remotes/origin/" + default, "..."
        else:
            return None
        if not re.fullmatch(r"[0-9a-f]{40}", head):
            return None
        if not base.startswith("refs/remotes/origin/") and not re.fullmatch(r"[0-9a-f]{40}", base):
            return None
        result = subprocess.run(
            ["git", "diff", "--no-renames", "--name-only", "-z", base + separator + head, "--"],
            cwd=root, capture_output=True, check=True, timeout=30,
        )
        return result.stdout.decode("utf-8").rstrip("\0").split("\0") if result.stdout else []
    except (KeyError, TypeError, ValueError, OSError, subprocess.SubprocessError):
        return None


def check_results(profile, needs):
    expected = "success" if profile == "full" else "skipped"
    return (
        profile in {"full", "docs", "reuse"}
        and needs.get("preflight", {}).get("result") == "success"
        and needs.get("quality", {}).get("result") == "success"
        and all(needs.get(name, {}).get("result") == expected for name in FULL_JOBS)
    )


def run_docs(root=ROOT):
    scratch = root / ".local/test-temp"
    scratch.mkdir(parents=True, exist_ok=True)
    # One owned directory per invocation; existing diagnostics are never cleared.
    with tempfile.TemporaryDirectory(prefix="ci-docs-", dir=scratch) as directory:
        temporary = Path(directory)
        if temporary.resolve().parent != scratch.resolve():
            raise RuntimeError("CI temporary directory outside workspace scratch")
        return subprocess.call([
            sys.executable, "-m", "pytest", "-q", "-x", *DOC_TESTS,
            "--basetemp=" + str(temporary / "pytest"),
            "-o", "cache_dir=" + str(temporary / "cache"),
        ], cwd=root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["plan", "docs", "gate"])
    parser.add_argument("--profile", choices=["full", "docs", "reuse"])
    args = parser.parse_args()
    if args.operation == "plan":
        reuse = None
        name, ref, event = "", "", {}
        try:
            event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text("utf-8"))
            name, ref = os.environ["GITHUB_EVENT_NAME"], os.environ["GITHUB_REF"]
            profile, reason = choose_profile(name, ref, event, changed_files(name, event))
        except (KeyError, TypeError, ValueError, OSError, AttributeError):
            profile, reason = "full", "unreadable-event"
        if profile == "full":
            try:
                if __package__:
                    from .ci_reuse import find_verified_run
                else:
                    from ci_reuse import find_verified_run

                reuse = find_verified_run(name, ref, event)
            except Exception as error:
                # Reuse is optional; unavailable or malformed evidence runs full CI.
                print(f"CI reuse unavailable ({type(error).__name__}); running full CI")
                reuse = None
            if reuse:
                profile, reason = "reuse", "verified-identical-pr-tree"
        with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
            output.write(f"profile={profile}\n")
            output.write("reuse=" + json.dumps(reuse, separators=(",", ":")) + "\n")
        print(json.dumps({"profile": profile, "reason": reason}))
    elif args.operation == "gate":
        needs = json.loads(os.environ["NEEDS_JSON"])
        if not check_results(args.profile, needs):
            raise SystemExit("Required CI validation did not complete successfully")
        print(f"CI passed: {args.profile}")
        if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
            # This receipt describes this run; full-tree reuse still requires its
            # separately authenticated artifact and all successful job results.
            commit, tree = subprocess.check_output(
                ["git", "rev-parse", "HEAD", "HEAD^{tree}"], cwd=ROOT, text=True, timeout=10,
            ).splitlines()
            run = (f"{os.environ['GITHUB_SERVER_URL']}/{os.environ['GITHUB_REPOSITORY']}"
                   f"/actions/runs/{os.environ['GITHUB_RUN_ID']}")
            with Path(summary).open("a", encoding="utf-8") as receipt:
                receipt.write(
                    f"### Validation receipt\n\nProfile: `{args.profile}`\n\n"
                    f"Commit: `{commit}`\n\nTree: `{tree}`\n\n[CI run]({run})\n\n"
                    "| Check | Result |\n| --- | --- |\n"
                )
                for job in ("preflight", "quality", *FULL_JOBS):
                    receipt.write(f"| {job} | {needs[job]['result']} |\n")
    else:
        raise SystemExit(run_docs())


if __name__ == "__main__":
    main()
