#Requires -Version 7.0
param(
    [Parameter(Mandatory = $true)][string]$RuntimeRoot,
    [string]$Python = 'python'
)
$ErrorActionPreference = 'Stop'
$workspace = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$candidate = [IO.Path]::GetFullPath($RuntimeRoot)
$projectRoot = $workspace
if (-not $candidate.StartsWith($workspace + [IO.Path]::DirectorySeparatorChar,
        [StringComparison]::OrdinalIgnoreCase)) {
    $gitDirectory = (git -C $workspace rev-parse --path-format=absolute --git-common-dir).Trim()
    if ($LASTEXITCODE -ne 0) { throw 'RuntimeRoot must remain inside this workspace' }
    $projectRoot = Split-Path -Parent $gitDirectory
}
if (-not $candidate.StartsWith($projectRoot + [IO.Path]::DirectorySeparatorChar,
        [StringComparison]::OrdinalIgnoreCase)) { throw 'RuntimeRoot must remain inside the project' }
New-Item -ItemType Directory -Force -Path $candidate | Out-Null
. (Join-Path $PSScriptRoot 'voice-runtime-env.ps1') -RuntimeRoot $candidate -Workspace $workspace
$null = Get-Command uv -ErrorAction Stop
$runtimePython = Join-Path $voiceRoot 'venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $runtimePython)) {
    uv venv --python $Python (Join-Path $voiceRoot 'venv')
    if ($LASTEXITCODE -ne 0) { throw 'Voice virtual environment creation failed' }
}
& $runtimePython -B -c 'import sys; assert sys.version_info[:2] == (3, 12), "Python 3.12 required"'
if ($LASTEXITCODE -ne 0) { throw 'Unsupported voice Python version' }
uv pip install --python $runtimePython -r (Join-Path $workspace 'deploy/voice-node-requirements.lock') `
    --extra-index-url https://download.pytorch.org/whl/cu124 --index-strategy unsafe-best-match
if ($LASTEXITCODE -ne 0) { throw 'Pinned voice dependency installation failed' }
$env:HF_HUB_OFFLINE = '0'
$env:TRANSFORMERS_OFFLINE = '0'
& $runtimePython -B (Join-Path $PSScriptRoot 'setup-voice-runtime.py') --runtime-root $voiceRoot `
    --specification (Join-Path $workspace 'deploy/voice-node-sources.json')
if ($LASTEXITCODE -ne 0) { throw 'Pinned voice asset setup failed' }
uv pip check --python $runtimePython
if ($LASTEXITCODE -ne 0) { throw 'Voice dependency consistency check failed' }
