[CmdletBinding(DefaultParameterSetName = 'Tests')]
param(
    [Parameter(ParameterSetName = 'Docs', Mandatory = $true)][switch]$Docs,
    [Parameter(ParameterSetName = 'Tests')]
    [string[]]$TestFiles = @(),
    [Parameter(ParameterSetName = 'Tests')]
    [ValidateRange(0, 2147483647)][int]$Durations = 0,
    [Parameter(ParameterSetName = 'Tests')][string]$JunitXml = '',
    [string]$Python = '.venv/Scripts/python.exe'
)

$ErrorActionPreference = 'Stop'
$taskRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $taskRoot
$pytestArguments = @()
if ($Durations -gt 0) { $pytestArguments += "--durations=$Durations" }
if ($JunitXml) {
    $report = if ([IO.Path]::IsPathRooted($JunitXml)) {
        [IO.Path]::GetFullPath($JunitXml)
    } else { [IO.Path]::GetFullPath((Join-Path $taskRoot $JunitXml)) }
    if (-not $report.StartsWith($taskRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'JUnit report must stay inside the workspace'
    }
    $pytestArguments += "--junitxml=$report"
}
$roundTemp = '.local/test-temp/check-' + [guid]::NewGuid().ToString('N')
New-Item -ItemType Directory -Force -Path "$roundTemp/tmp" | Out-Null
$previousEnvironment = @{}
foreach ($name in @('TEMP', 'TMP', 'UV_CACHE_DIR', 'PYTHONDONTWRITEBYTECODE', 'PYTHONIOENCODING', 'PYTHONPATH')) {
    $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
$env:TEMP = (Resolve-Path "$roundTemp/tmp").Path
$env:TMP = $env:TEMP
$env:UV_CACHE_DIR = Join-Path $taskRoot '.local/uv-cache'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONIOENCODING = 'utf-8'
# Child scripts must import this checkout even when a worktree borrows a virtualenv.
$env:PYTHONPATH = if ($env:PYTHONPATH) {
    $taskRoot + [IO.Path]::PathSeparator + $env:PYTHONPATH
} else { $taskRoot }
try {
    if ($Docs) {
        & $Python scripts/ci_policy.py docs
        if ($LASTEXITCODE -ne 0) { throw 'Documentation checks failed' }
    } else {
        & $Python -m ruff check companion companion_onebot_bridge source_reader migrations astrbot_plugin_companion_bridge tests scripts "--cache-dir=$roundTemp/ruff-cache"
        if ($LASTEXITCODE -ne 0) { throw 'Lint failed' }
        & $Python -m pytest -q @TestFiles @pytestArguments "--basetemp=$roundTemp/pytest" -o "cache_dir=$roundTemp/pytest-cache"
        if ($LASTEXITCODE -ne 0) { throw 'Tests failed' }
    }
} finally {
    foreach ($name in $previousEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $previousEnvironment[$name], 'Process')
    }
    # Each verification owns one directory; unrelated or retained diagnostics are untouched.
    foreach ($relative in @($roundTemp)) {
        $target = [System.IO.Path]::GetFullPath((Join-Path $taskRoot $relative))
        if (-not $target.StartsWith($taskRoot + [System.IO.Path]::DirectorySeparatorChar)) {
            throw 'Cleanup target outside workspace'
        }
        if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Recurse -Force }
    }
}
