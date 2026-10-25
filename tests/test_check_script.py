"""The local check entry point selects work and restores its caller on failure."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("pwsh")


@pytest.mark.skipif(not POWERSHELL, reason="PowerShell is unavailable")
@pytest.mark.parametrize("mode", ["Docs", "Tests", "Impact", "Full"])
@pytest.mark.parametrize("exit_code", [0, 7])
@pytest.mark.parametrize("initial_environment", ["present", "absent"])
def test_check_selects_work_and_restores_environment(
    tmp_path, mode, exit_code, initial_environment,
):
    calls = tmp_path / "calls.jsonl"
    stub = tmp_path / "python.ps1"
    stub.write_text(
        "ConvertTo-Json -InputObject @($args) -Compress | "
        "Add-Content -LiteralPath $env:CHECK_STUB_CALLS\nexit $env:CHECK_STUB_EXIT\n",
        encoding="utf-8",
    )
    runner = tmp_path / "verify.ps1"
    runner.write_text("""
param([string]$Check, [string]$PythonStub, [string]$Mode)
$ErrorActionPreference = 'Stop'
$names = @('TEMP', 'TMP', 'TMPDIR', 'UV_CACHE_DIR', 'PYTHONDONTWRITEBYTECODE',
           'PYTHONIOENCODING', 'PYTHONPATH')
if ($env:CHECK_STUB_ENVIRONMENT -eq 'absent') {
    foreach ($name in $names) { Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue }
} else {
    $env:TEMP = Split-Path -Parent $PythonStub
    $env:TMP = $env:TEMP
    $env:TMPDIR = $env:TEMP
    $env:UV_CACHE_DIR = $env:TEMP
    $env:PYTHONDONTWRITEBYTECODE = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    $env:PYTHONPATH = $env:TEMP
}
$before = @{}
foreach ($name in $names) {
    $before[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
$failed = $false
try {
    if ($Mode -eq 'Docs') { & $Check -Docs -Python $PythonStub }
    elseif ($Mode -eq 'Impact') {
        & $Check -TestFiles tests/synthetic.py -Impact -Workers 3 -SkipModelPreparation `
            -Durations 2 -Python $PythonStub
    }
    elseif ($Mode -eq 'Full') {
        & $Check -Full -Impact -Workers 0 -SkipModelPreparation -Python $PythonStub
    }
    else { & $Check -TestFiles tests/synthetic.py -Python $PythonStub }
} catch { $failed = $true }
if ($failed -ne ($env:CHECK_STUB_EXIT -ne '0')) { throw 'Unexpected check result' }
foreach ($name in $names) {
    if ([Environment]::GetEnvironmentVariable($name, 'Process') -cne $before[$name]) {
        throw "Caller environment changed: $name"
    }
}
""", encoding="utf-8")
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-File", str(runner),
         "-Check", str(ROOT / "scripts/check.ps1"), "-PythonStub", str(stub), "-Mode", mode],
        cwd=ROOT, env={**os.environ, "CHECK_STUB_CALLS": str(calls),
                       "CHECK_STUB_EXIT": str(exit_code),
                       "CHECK_STUB_ENVIRONMENT": initial_environment},
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    commands = [json.loads(line) for line in calls.read_text("utf-8-sig").splitlines()]
    if mode == "Docs":
        assert commands == [["scripts/ci_policy.py", "docs"]]
    else:
        assert commands[0][:4] == ["-m", "ruff", "check", "companion"]
        assert len(commands) == (1 if exit_code else 2)
        if not exit_code:
            feedback = list(map(str, commands[1]))
            assert feedback[:3] == ["-B", "-m", "scripts.test_feedback"]
            assert feedback[3:5] == ["--workers", {"Tests": "2", "Impact": "3", "Full": "0"}[mode]]
            assert ("--impact" in feedback) == (mode != "Tests")
            assert ("--full" in feedback) == (mode == "Full")
            assert ("--skip-model-preparation" in feedback) == (mode != "Tests")
            assert ("tests/synthetic.py" in feedback) == (mode != "Full")
            assert ("--durations=2" in feedback) == (mode == "Impact")
            # PowerShell consumes `--` when the stub is another PS script; the
            # real Python entry point receives it (covered by native runner tests).
            assert any(arg.startswith("--basetemp=") for arg in feedback)
        temporary = next(arg.split("=", 1)[1] for arg in commands[0]
                         if arg.startswith("--cache-dir="))
        assert not (ROOT / temporary).parent.exists()


@pytest.mark.skipif(not POWERSHELL, reason="PowerShell is unavailable")
@pytest.mark.parametrize("exit_code", [0, 5, 7])
def test_feedback_exit_status_is_not_hidden_by_successful_lint(tmp_path, exit_code):
    calls = tmp_path / "calls.jsonl"
    stub = tmp_path / "python.ps1"
    stub.write_text(
        "ConvertTo-Json -InputObject @($args) -Compress | "
        "Add-Content -LiteralPath $env:CHECK_STUB_CALLS\n"
        "if ($args -contains 'scripts.test_feedback') { exit $env:CHECK_FEEDBACK_EXIT }\n"
        "exit 0\n", encoding="utf-8",
    )
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-File", str(ROOT / "scripts/check.ps1"),
         "-TestFiles", "tests/synthetic.py", "-Python", str(stub)],
        cwd=ROOT, env={**os.environ, "CHECK_STUB_CALLS": str(calls),
                       "CHECK_FEEDBACK_EXIT": str(exit_code)},
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
    )
    assert (result.returncode == 0) == (exit_code == 0), result.stdout + result.stderr
    commands = [json.loads(line) for line in calls.read_text("utf-8-sig").splitlines()]
    assert len(commands) == 2
    assert commands[1][:3] == ["-B", "-m", "scripts.test_feedback"]
    if exit_code:
        assert "Tests failed" in result.stderr and "Lint failed" not in result.stderr
    temporary = next(arg.split("=", 1)[1] for arg in commands[0]
                     if arg.startswith("--cache-dir="))
    assert not (ROOT / temporary).parent.exists()


@pytest.mark.skipif(not POWERSHELL, reason="PowerShell is unavailable")
def test_full_cannot_silently_narrow_to_explicit_test_files(tmp_path):
    stub = tmp_path / "must-not-run.ps1"
    marker = tmp_path / "called"
    stub.write_text("New-Item -ItemType File -Path $env:CHECK_MARKER\n", encoding="utf-8")
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-File", str(ROOT / "scripts/check.ps1"),
         "-Full", "-TestFiles", "tests/synthetic.py", "-Python", str(stub)],
        cwd=ROOT, env={**os.environ, "CHECK_MARKER": str(marker)},
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
    )
    assert result.returncode != 0 and "cannot be combined" in result.stderr
    assert not marker.exists()
