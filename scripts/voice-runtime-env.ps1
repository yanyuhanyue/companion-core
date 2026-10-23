#Requires -Version 7.0
param(
    [Parameter(Mandatory = $true)][string]$RuntimeRoot,
    [Parameter(Mandatory = $true)][string]$Workspace
)
$ErrorActionPreference = 'Stop'
$voiceRoot = (Resolve-Path -LiteralPath $RuntimeRoot).Path
$voiceWorkspace = (Resolve-Path -LiteralPath $Workspace).Path
$voiceRepository = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
if ($voiceWorkspace -ne $voiceRepository) { throw 'Workspace must be this source checkout' }
# RuntimeRoot may be a sibling worktree handoff, but must remain in the project workspace.
if (-not $voiceRoot.StartsWith($voiceWorkspace + [IO.Path]::DirectorySeparatorChar,
        [StringComparison]::OrdinalIgnoreCase)) {
    $voiceProject = (git -C $voiceRepository rev-parse --path-format=absolute --git-common-dir).Trim()
    if ($LASTEXITCODE -ne 0) { throw 'RuntimeRoot must be inside this source workspace' }
    $voiceProject = Split-Path -Parent $voiceProject
    if (-not $voiceRoot.StartsWith($voiceProject + [IO.Path]::DirectorySeparatorChar,
            [StringComparison]::OrdinalIgnoreCase)) { throw 'RuntimeRoot must be inside this project' }
}
foreach ($sub in @('tmp','cache','logs','samples','bin')) {
    New-Item -ItemType Directory -Force -Path (Join-Path $voiceRoot $sub) | Out-Null
}
$env:TEMP = Join-Path $voiceRoot 'tmp'
$env:TMP = $env:TEMP
$env:TMPDIR = $env:TEMP
$env:UV_CACHE_DIR = Join-Path $voiceRoot 'cache/uv'
$env:HF_HOME = Join-Path $voiceRoot 'cache/huggingface'
$env:HF_HUB_CACHE = Join-Path $env:HF_HOME 'hub'
$env:HF_XET_CACHE = Join-Path $env:HF_HOME 'xet'
$env:XDG_CACHE_HOME = Join-Path $voiceRoot 'cache'
$env:TORCH_HOME = Join-Path $voiceRoot 'cache/torch'
$env:NUMBA_CACHE_DIR = Join-Path $voiceRoot 'cache/numba'
$env:NLTK_DATA = Join-Path $voiceRoot 'cache/nltk'
$env:MPLCONFIGDIR = Join-Path $voiceRoot 'cache/matplotlib'
$env:HF_HUB_DISABLE_TELEMETRY = '1'
$env:GRADIO_ANALYTICS_ENABLED = 'False'
$env:HF_HUB_DISABLE_XET = '1'
$env:HF_HUB_OFFLINE = '1'
$env:HF_DATASETS_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
$env:PYTHONUNBUFFERED = '1'
$env:PYTHONPATH = $voiceWorkspace
$env:PATH = (Join-Path $voiceRoot 'bin') + ';' +
    (Join-Path $voiceRoot 'venv/Lib/site-packages/torch/lib') + ';' + $env:PATH
