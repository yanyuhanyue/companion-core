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
@pytest.mark.parametrize("mode", ["Docs", "Tests"])
@pytest.mark.parametrize("exit_code", [0, 7])
def test_check_selects_work_and_restores_environment(tmp_path, mode, exit_code):
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
$names = @('TEMP', 'TMP', 'UV_CACHE_DIR', 'PYTHONDONTWRITEBYTECODE',
           'PYTHONIOENCODING', 'PYTHONPATH')
$before = @{}
foreach ($name in $names) {
    $before[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
$failed = $false
try {
    if ($Mode -eq 'Docs') { & $Check -Docs -Python $PythonStub }
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
                       "CHECK_STUB_EXIT": str(exit_code)},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    commands = [json.loads(line) for line in calls.read_text("utf-8-sig").splitlines()]
    if mode == "Docs":
        assert commands == [["scripts/ci_policy.py", "docs"]]
    else:
        assert commands[0][:4] == ["-m", "ruff", "check", "companion"]
        assert len(commands) == (1 if exit_code else 2)
        if not exit_code:
            assert commands[1][:4] == ["-m", "pytest", "-q", "tests/synthetic.py"]
        temporary = next(arg.split("=", 1)[1] for arg in commands[0]
                         if arg.startswith("--cache-dir="))
        assert not (ROOT / temporary).parent.exists()
