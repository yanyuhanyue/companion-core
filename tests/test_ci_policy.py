"""CI can reduce work only when it can prove which validation remains required."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from scripts.ci_policy import (
    CRITICAL_FILES,
    FULL_JOBS,
    changed_files,
    check_results,
    choose_profile,
    preflight_tests,
    run_preflight,
)

ROOT = Path(__file__).resolve().parents[1]
EVENT = {"repository": {"default_branch": "main"}}


@pytest.mark.parametrize("name,ref,files,expected", [
    ("push", "refs/heads/topic", ["companion/expression.py"], "fast"),
    ("push", "refs/heads/topic", ["tests/test_expression.py"], "fast"),
    ("push", "refs/heads/topic", ["docs/guide.md", "README.md"], "docs"),
    ("pull_request", "refs/pull/1/merge", ["docs/guide.md"], "docs"),
    ("pull_request", "refs/pull/1/merge", ["companion/expression.py"], "full"),
    ("push", "refs/heads/main", ["docs/guide.md"], "full"),
    ("push", "refs/tags/v1", ["docs/guide.md"], "full"),
    ("workflow_dispatch", "refs/heads/topic", ["docs/guide.md"], "full"),
    ("merge_group", "refs/heads/queue", ["docs/guide.md"], "full"),
    ("unknown", "refs/heads/topic", ["docs/guide.md"], "full"),
    ("push", "refs/heads/topic", None, "full"),
    ("push", "refs/heads/topic", [], "full"),
    ("push", "refs/heads/topic", ["new_runtime/data.bin"], "full"),
    ("push", "refs/heads/topic", ["docs/guide.md", "new_runtime/data.bin"], "full"),
    ("push", "refs/heads/topic", ["docs/../contracts/schema.md"], "full"),
    ("push", "refs/heads/topic", ["docs/guide.md\nprofile=docs"], "full"),
])
def test_profiles_keep_integration_refs_full_and_only_reduce_known_changes(
    name, ref, files, expected,
):
    assert choose_profile(name, ref, EVENT, files)[0] == expected


@pytest.mark.parametrize("name", sorted(CRITICAL_FILES) + [
    "contracts/event.v1.schema.json", "migrations/versions/new.py",
    "deploy/source-reader.Dockerfile", "docs/design-v0.6/upstream-native-p3.lock",
    ".github/workflows/ci.yml", "companion_onebot_bridge/outbound.py",
])
def test_critical_changes_cannot_use_development_or_docs_shortcut(name):
    assert choose_profile("push", "refs/heads/topic", EVENT, [name])[0] == "full"


@pytest.mark.parametrize("name", [
    "companion/api.py", "companion/policy.py", "companion/backup.py",
    "companion/model.py", "companion/memory.py", "companion/core.py",
    "companion/runtime.py", "companion/observation.py", "companion/content.py",
    "companion/live_text.py",
    "companion/executor_gateway.py", "companion/executor_budget_proxy.py",
    "companion/dsh_job.py", "source_reader/worker.py",
    "astrbot_plugin_companion_bridge/bridge.py",
])
def test_auth_consent_restore_paid_io_and_evidence_paths_always_require_full(name):
    # Independent risk examples: removing a path from the policy must break this test.
    assert choose_profile("push", "refs/heads/topic", EVENT, [name])[0] == "full"


def needs(profile):
    return {"preflight": {"result": "success"}, **{
        name: {"result": "success" if profile == "full" else "skipped"} for name in FULL_JOBS
    }}


@pytest.mark.parametrize("profile", ["full", "fast", "docs"])
def test_gate_accepts_only_the_expected_complete_result_set(profile):
    assert check_results(profile, needs(profile))
    assert not check_results("unknown", needs(profile))
    assert not check_results(profile, {})


@pytest.mark.parametrize("job", ["preflight", *FULL_JOBS])
@pytest.mark.parametrize("state", ["failure", "cancelled", "skipped", ""])
def test_full_gate_rejects_failed_cancelled_missing_or_skipped_required_job(job, state):
    results = needs("full")
    results[job]["result"] = state
    assert not check_results("full", results)


@pytest.mark.parametrize("profile", ["fast", "docs"])
def test_short_gate_cannot_hide_a_failed_attempted_job(profile):
    results = needs(profile)
    results["container"]["result"] = "failure"
    assert not check_results(profile, results)
    results = needs(profile)
    del results["container"]
    assert not check_results(profile, results)


def git(path, *args):
    return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()


@pytest.fixture
def repository(tmp_path):
    git(tmp_path, "init", "-q")
    (tmp_path / "README.md").write_text("fixture\n", encoding="utf-8")
    commit(tmp_path)
    return tmp_path


def commit(path):
    git(path, "add", "--all")
    git(path, "-c", "user.name=Synthetic CI", "-c", "user.email=ci@example.invalid",
        "commit", "-qm", "fixture")
    return git(path, "rev-parse", "HEAD")


def test_actual_git_diff_retains_old_code_path_when_it_is_renamed_to_markdown(repository):
    source = repository / "companion/expression.py"
    source.parent.mkdir()
    source.write_text("text\n", encoding="utf-8")
    base = commit(repository)
    destination = repository / "docs/renamed.md"
    destination.parent.mkdir()
    source.rename(destination)
    head = commit(repository)
    event = {**EVENT, "before": base, "after": head}
    files = changed_files("push", event, repository)
    assert set(files) == {"companion/expression.py", "docs/renamed.md"}
    assert choose_profile("push", "refs/heads/topic", event, files)[0] == "full"


def test_large_diff_has_no_api_page_limit_and_new_branch_uses_main_merge_base(repository):
    base = git(repository, "rev-parse", "HEAD")
    git(repository, "update-ref", "refs/remotes/origin/main", base)
    directory = repository / "docs"
    directory.mkdir()
    for number in range(320):
        (directory / f"guide-{number}.md").write_text("fixture\n", encoding="utf-8")
    (repository / "uv.lock").write_text("fixture\n", encoding="utf-8")
    event = {**EVENT, "before": "0" * 40, "after": commit(repository)}
    files = changed_files("push", event, repository)
    assert len(files) == 321 and "uv.lock" in files
    assert choose_profile("push", "refs/heads/topic", event, files)[0] == "full"


def test_pr_comparison_excludes_unrelated_target_branch_changes(repository):
    base = git(repository, "rev-parse", "HEAD")
    (repository / "topic.py").write_text("fixture\n", encoding="utf-8")
    head = commit(repository)
    git(repository, "checkout", "-q", "--detach", base)
    (repository / "uv.lock").write_text("fixture\n", encoding="utf-8")
    target = commit(repository)
    event = {**EVENT, "pull_request": {"base": {"sha": target}, "head": {"sha": head}}}
    assert changed_files("pull_request", event, repository) == ["topic.py"]
    assert changed_files("push", {**EVENT, "before": "f" * 40, "after": head}, repository) is None


def test_cli_event_failure_chooses_full_and_gate_failure_returns_nonzero(tmp_path):
    output = tmp_path / "outputs"
    env = {**os.environ, "GITHUB_OUTPUT": str(output),
           "GITHUB_EVENT_PATH": str(tmp_path / "missing.json"), "GITHUB_EVENT_NAME": "push",
           "GITHUB_REF": "refs/heads/topic"}
    result = subprocess.run([sys.executable, "scripts/ci_policy.py", "plan"],
                            cwd=ROOT, env=env, capture_output=True, text=True)
    assert result.returncode == 0
    assert output.read_text("utf-8") == "profile=full\n"
    env["NEEDS_JSON"] = json.dumps(needs("fast"))
    result = subprocess.run([sys.executable, "scripts/ci_policy.py", "gate", "--profile", "full"],
                            cwd=ROOT, env=env, capture_output=True, text=True)
    assert result.returncode != 0


def test_preflight_files_exist_and_optional_new_workers_are_not_silently_missed(tmp_path):
    assert all((ROOT / node.split("::")[0]).is_file() for node in preflight_tests("fast"))
    assert "tests/test_source_reader_image.py" not in preflight_tests("docs")
    assert "tests/test_p8_readiness.py" not in preflight_tests("docs")
    assert set(preflight_tests("docs")) < set(preflight_tests("fast"))
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_worker_composition.py").touch()
    assert "tests/test_worker_composition.py" in preflight_tests("full", tmp_path)


@pytest.mark.parametrize("result", [0, 1])
def test_preflight_bootstraps_fresh_scratch_and_cleans_only_its_own_round(
    tmp_path, monkeypatch, result,
):
    scratch = tmp_path / ".local/test-temp"
    assert not scratch.exists()
    rounds = []

    def pytest_process(command, cwd):
        assert cwd == tmp_path and scratch.is_dir()
        directory = Path(next(arg for arg in command if arg.startswith("--basetemp="))[11:])
        assert directory.parent.is_dir() and directory.parent.parent == scratch
        rounds.append(directory.parent)
        # An unrelated retained diagnostic must survive a later successful/failed run.
        (scratch / "retained.txt").write_text("synthetic diagnostic", encoding="utf-8")
        return result

    monkeypatch.setattr("scripts.ci_policy.subprocess.call", pytest_process)
    assert run_preflight("fast", tmp_path) == result
    assert not rounds[0].exists()
    assert (scratch / "retained.txt").read_text("utf-8") == "synthetic diagnostic"


def test_workflow_keeps_full_platform_checks_and_final_gate_after_preflight():
    workflow = yaml.load((ROOT / ".github/workflows/ci.yml").read_text("utf-8"),
                         Loader=yaml.BaseLoader)
    jobs = workflow["jobs"]
    assert set(jobs) == {"preflight", "ci", *FULL_JOBS}
    assert jobs["test"]["strategy"]["matrix"]["os"] == ["ubuntu-latest", "windows-latest"]
    for name in FULL_JOBS:
        assert jobs[name]["needs"] == "preflight"
        assert jobs[name]["if"] == "${{ needs.preflight.outputs.profile == 'full' }}"
    assert jobs["ci"]["if"] == "${{ always() }}"
    assert set(jobs["ci"]["needs"]) == {"preflight", *FULL_JOBS}
    assert set(workflow["on"]) == {"push", "pull_request", "merge_group", "workflow_dispatch"}
    concurrency = workflow["concurrency"]
    assert "github.workflow" in concurrency["group"] and "github.ref" in concurrency["group"]
    assert "default_branch" in concurrency["cancel-in-progress"]
