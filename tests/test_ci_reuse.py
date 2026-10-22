"""Only a complete, current, same-repository PR run can replace main's full suite."""

import io
import json
import subprocess
import sys
import zipfile
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from scripts import ci_policy, ci_reuse
from tests import test_ci_policy as policy_tests
from tests.test_ci_policy import commit

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def repository(tmp_path):
    return policy_tests.repository.__wrapped__(tmp_path)


@pytest.fixture
def evidence(repository, monkeypatch, tmp_path):
    target = ci_reuse.checkout(repository)
    repo, head = "synthetic/repository", "a" * 40
    event = {"after": target["commit"], "repository": {
        "full_name": repo, "default_branch": "main",
    }}
    record = {"version": 1, "profile": "full", "repository": repo, "event": "pull_request",
              "tree": target["tree"], "commit": "b" * 40, "run_id": 101, "attempt": 2}
    # The recorded checkout is deliberately different from the PR head and main
    # commit. Reuse must bind the actual tested tree, not infer it from head_sha.
    run = {"id": 101, "run_attempt": 2, "event": "pull_request", "path": ci_reuse.WORKFLOW,
           "status": "completed", "conclusion": "success", "head_sha": head,
           "repository": {"full_name": repo}, "head_repository": {"full_name": repo},
           "updated_at": datetime.now(UTC).isoformat()}
    pr = {"state": "closed", "merged_at": "2026-10-22T00:00:00Z",
          "merge_commit_sha": target["commit"], "base": {"ref": "main", "repo": {
              "full_name": repo}}, "head": {"sha": head, "repo": {"full_name": repo}}}
    artifact = {"id": 300, "name": "ci-full-2", "expired": False, "size_in_bytes": 500}
    jobs = {"total_count": 10, "jobs": [
        {"name": name, "status": "completed", "conclusion": "success"}
        for name in ci_reuse.REQUIRED_JOBS
    ]}
    state = {"run": run, "pr": pr, "record": record, "artifact": artifact, "jobs": jobs,
             "calls": [], "additional_runs": [], "additional_artifacts": []}

    def read(path, *, binary=False):
        state["calls"].append(path)
        if state.get("timeout"):
            raise subprocess.TimeoutExpired(["gh", "api", path], 30)
        if state.get("unavailable"):
            raise OSError("synthetic API failure")
        if "/commits/" in path:
            return [deepcopy(state["pr"])]
        if "/workflows/ci.yml/runs?" in path:
            return {"workflow_runs": [deepcopy(state["run"]), *state["additional_runs"]]}
        if path.endswith("/artifacts?per_page=100"):
            return {"artifacts": [deepcopy(state["artifact"]), *state["additional_artifacts"]]}
        if path.endswith("/zip"):
            assert binary
            if state.get("bad_zip"):
                return b"not an archive"
            data = io.BytesIO()
            with zipfile.ZipFile(data, "w") as archive:
                archive.writestr(state.get("filename", ci_reuse.RECORD),
                                 json.dumps(state["record"]))
            return data.getvalue()
        assert path.endswith("/attempts/2/jobs?per_page=100"), path
        return deepcopy(state["jobs"])

    original = ci_reuse.find_verified_run
    monkeypatch.setattr(ci_reuse, "find_verified_run", lambda name, ref, data: original(
        name, ref, data, root=repository, read=read,
    ))
    event_path, output = tmp_path / "event.json", tmp_path / "output"
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setattr(ci_policy, "changed_files", lambda *_: ["companion/core.py"])
    monkeypatch.setattr(sys, "argv", ["ci_policy.py", "plan"])

    def plan():
        event_path.write_text(json.dumps(event), encoding="utf-8")
        output.write_text("", encoding="utf-8")
        ci_policy.main()
        values = dict(line.split("=", 1) for line in output.read_text().splitlines())
        return values["profile"], json.loads(values["reuse"])

    state.update(event=event, plan=plan, root=repository)
    return state


def test_identical_actual_tree_reuses_full_success_and_validates_again(evidence):
    profile, proof = evidence["plan"]()
    assert profile == "reuse"
    assert proof == {"run_id": 101, "attempt": 2, "tree": evidence["record"]["tree"]}
    assert len(evidence["calls"]) == 5
    assert ci_reuse.validate_reuse({"CI_REUSE": json.dumps(proof), "GITHUB_EVENT_NAME": "push"},
                                  root=evidence["root"]) == proof


@pytest.mark.parametrize("section,field,value", [
    ("run", "conclusion", "failure"), ("run", "conclusion", "cancelled"),
    ("run", "status", "in_progress"), ("run", "event", "pull_request_target"),
    ("run", "path", ".github/workflows/other.yml"), ("run", "head_sha", "c" * 40),
    ("run", "head_repository", {"full_name": "fork/repository"}),
    ("run", "repository", {"full_name": "other/repository"}),
    ("run", "updated_at", "2020-01-01T00:00:00Z"),
    ("run", "updated_at", "2099-01-01T00:00:00Z"),
    ("run", "run_attempt", 3),
    ("record", "tree", "c" * 40), ("record", "profile", "docs"),
    ("record", "profile", "reuse"), ("record", "repository", "other/repository"),
    ("record", "run_id", 100), ("record", "attempt", 1),
    ("record", "event", "push"), ("record", "version", 2),
    ("record", "version", True),
    ("artifact", "expired", True), ("artifact", "name", "ci-full-1"),
    ("artifact", "size_in_bytes", 16385), ("artifact", "id", -1),
    ("pr", "merge_commit_sha", "c" * 40), ("pr", "state", "open"),
    ("pr", "merged_at", None),
])
def test_incomplete_or_untrusted_evidence_falls_back_to_full(evidence, section, field, value):
    evidence[section][field] = value
    assert evidence["plan"]() == ("full", None)


@pytest.mark.parametrize("fault", [
    "unavailable", "timeout", "bad_zip", "filename", "jobs", "duplicate",
])
def test_lookup_faults_and_partial_jobs_do_not_greenwash_main(evidence, fault):
    if fault == "filename":
        evidence["filename"] = "../verified-tree.json"
    elif fault == "jobs":
        evidence["jobs"]["jobs"][0]["conclusion"] = "skipped"
    elif fault == "duplicate":
        evidence["additional_artifacts"] = [deepcopy(evidence["artifact"])]
    else:
        evidence[fault] = True
    assert evidence["plan"]() == ("full", None)


def test_newer_failed_run_cannot_be_hidden_by_older_green(evidence):
    evidence["additional_runs"] = [{**evidence["run"], "id": 102, "conclusion": "failure"}]
    assert evidence["plan"]() == ("full", None)


def test_actual_merge_changes_require_full_even_when_pr_was_green(evidence):
    (evidence["root"] / "merged-change.txt").write_text("new integrated content", encoding="utf-8")
    commit(evidence["root"])
    changed = ci_reuse.checkout(evidence["root"])
    evidence["event"]["after"] = evidence["pr"]["merge_commit_sha"] = changed["commit"]
    assert evidence["plan"]() == ("full", None)


@pytest.mark.parametrize("name,ref", [
    ("pull_request", "refs/pull/1/merge"), ("merge_group", "refs/heads/queue"),
    ("workflow_dispatch", "refs/heads/main"), ("push", "refs/tags/v1"),
    ("push", "refs/heads/feature"),
])
def test_pr_tags_manual_and_queue_never_query_for_reuse(evidence, monkeypatch, name, ref):
    monkeypatch.setenv("GITHUB_EVENT_NAME", name)
    monkeypatch.setenv("GITHUB_REF", ref)
    assert evidence["plan"]() == ("full", None)
    assert evidence["calls"] == []


def test_lightweight_check_rejects_a_changed_checkout_or_missing_binding(evidence):
    proof = evidence["plan"]()[1]
    for env in ({}, {"CI_REUSE": "null"}, {"CI_REUSE": json.dumps({**proof, "tree": "c" * 40}),
                                          "GITHUB_EVENT_NAME": "push"}):
        with pytest.raises((ValueError, KeyError)):
            ci_reuse.validate_reuse(env, root=evidence["root"])


def test_reuse_workflow_keeps_only_lightweight_validation_and_full_only_artifact():
    workflow = yaml.load((ROOT / ci_reuse.WORKFLOW).read_text("utf-8"), Loader=yaml.BaseLoader)
    jobs = workflow["jobs"]
    assert jobs["preflight"]["permissions"] == {
        "contents": "read", "actions": "read", "pull-requests": "read",
    }
    quality = jobs["quality"]["steps"]
    for step in quality:
        if step.get("uses", "").startswith(("actions/setup-python", "astral-sh/setup-uv")) or (
            "uv sync" in step.get("run", "")
        ):
            assert step["if"] == "${{ needs.preflight.outputs.profile != 'reuse' }}"
    validate = next(step for step in quality
                    if step.get("run") == "python scripts/ci_reuse.py validate")
    assert validate["if"] == "${{ needs.preflight.outputs.profile == 'reuse' }}"
    steps = jobs["ci"]["steps"]
    gate = next(i for i, step in enumerate(steps) if " gate " in step.get("run", ""))
    record = next(i for i, step in enumerate(steps)
                  if step.get("run") == "python scripts/ci_reuse.py record")
    upload = next(i for i, step in enumerate(steps)
                  if step.get("uses") == "actions/upload-artifact@v4")
    assert gate < record < upload
    assert (steps[record]["if"] == steps[upload]["if"]
            == "${{ needs.preflight.outputs.profile == 'full' }}")
    assert steps[upload]["with"]["name"] == "ci-full-${{ github.run_attempt }}"
    assert steps[upload]["with"]["path"] == ".local/ci/verified-tree.json"
    assert steps[upload]["with"]["include-hidden-files"] == "true"
    assert ci_reuse.REQUIRED_JOBS == {
        "preflight", "quality", "ci", "container", "browser-reader", "plugin-native",
        "test (ubuntu-latest, 0)", "test (ubuntu-latest, 1)",
        "test (windows-latest, 0)", "test (windows-latest, 1)",
    }


def test_record_cli_captures_real_tree_only_for_full_success(repository, monkeypatch):
    target = ci_reuse.checkout(repository)
    monkeypatch.setattr(ci_reuse, "ROOT", repository)
    monkeypatch.setattr(ci_reuse, "checkout", lambda: target)
    for name, value in {"CI_PROFILE": "full", "GITHUB_SHA": target["commit"],
                        "GITHUB_REPOSITORY": "synthetic/repository", "GITHUB_RUN_ID": "101",
                        "GITHUB_RUN_ATTEMPT": "2", "GITHUB_EVENT_NAME": "pull_request"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(sys, "argv", ["ci_reuse.py", "record"])
    ci_reuse.main()
    record = json.loads((repository / ".local/ci/verified-tree.json").read_text())
    assert record["profile"] == "full" and record["attempt"] == 2
    assert record["tree"] == target["tree"] and record["commit"] == target["commit"]
    monkeypatch.setenv("CI_PROFILE", "reuse")
    with pytest.raises(ValueError, match="full run"):
        ci_reuse.main()


def test_api_only_uses_read_only_cli_and_does_not_print_errors(monkeypatch):
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, stdout=b'{"ok":true}')

    monkeypatch.setattr(ci_reuse.subprocess, "run", run)
    assert ci_reuse.api("repos/synthetic/repository/actions/runs") == {"ok": True}
    assert calls[0][0][:4] == ["gh", "api", "--hostname", "github.com"]
    assert calls[0][1]["timeout"] == 30
