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
$manifest = & $python -B -m companion_voice_node.worker --config $config --print-manifest
if ($LASTEXITCODE -ne 0) { throw 'Voice node configuration or local resources are invalid' }
$record = Join-Path $voiceRoot 'node-process.json'
if (Test-Path -LiteralPath $record) {
    $previous = Get-Content -LiteralPath $record -Raw | ConvertFrom-Json
    $existing = Get-Process -Id $previous.pid -ErrorAction SilentlyContinue
    if ($null -ne $existing -and
        $existing.StartTime.ToUniversalTime().Ticks -eq $previous.created_utc_ticks) {
        throw 'This runtime already has a live node process'
    }
}
$arguments = @('-B', '-m', 'companion_voice_node.worker', '--config', ('"' + $config + '"'))
$process = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $workspace `
    -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $voiceRoot 'logs/node-stdout.log') `
    -RedirectStandardError (Join-Path $voiceRoot 'logs/node-stderr.log')
$identity = [ordered]@{ pid=$process.Id; created_utc_ticks=$process.StartTime.ToUniversalTime().Ticks;
    executable=$python; manifest_sha256=$manifest.Trim(); state='STARTED' }
$identity | ConvertTo-Json | Set-Content -LiteralPath $record -Encoding utf8
Write-Output ('Node process started: ' + $process.Id + '; manifest ' + $manifest.Trim())
Write-Output 'Core authentication and task eligibility are confirmed by Core after polling.'
