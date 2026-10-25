#Requires -Version 7.0
param(
    [Parameter(Mandatory = $true)][string]$RuntimeRoot,
    [Parameter(Mandatory = $true)][string]$NodeConfig
)
$ErrorActionPreference = 'Stop'
$workspace = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $PSScriptRoot 'voice-runtime-env.ps1') -RuntimeRoot $RuntimeRoot -Workspace $workspace
$config = (Resolve-Path -LiteralPath $NodeConfig).Path
if (-not $config.StartsWith($voiceRoot + [IO.Path]::DirectorySeparatorChar,
        [StringComparison]::OrdinalIgnoreCase)) { throw 'Private node config must stay in RuntimeRoot' }
$python = Join-Path $voiceRoot 'venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw 'Voice runtime Python is missing' }
# Keep native JSON stdout and exit status; no manifest/model preflight or service stop.
$PSNativeCommandUseErrorActionPreference = $false
& $python -B -m companion_voice_node.worker --config $config --cleanup-only
exit $LASTEXITCODE
