$ErrorActionPreference = 'Stop'
$taskRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $taskRoot
New-Item -ItemType Directory -Force -Path '.local/tmp' | Out-Null
$env:TEMP = (Resolve-Path '.local/tmp').Path
$env:TMP = $env:TEMP
$env:UV_CACHE_DIR = Join-Path $taskRoot '.local/uv-cache'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONIOENCODING = 'utf-8'
try {
    & .venv/Scripts/python.exe -m ruff check companion migrations astrbot_plugin_companion_bridge tests scripts
    if ($LASTEXITCODE -ne 0) { throw 'Lint failed' }
    & .venv/Scripts/python.exe -m pytest -q --basetemp=.local/test-temp
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed' }
} finally {
    foreach ($relative in @('.pytest_cache', '.ruff_cache', '.local/test-temp', '.local/tmp')) {
        $target = [System.IO.Path]::GetFullPath((Join-Path $taskRoot $relative))
        if (-not $target.StartsWith($taskRoot + [System.IO.Path]::DirectorySeparatorChar)) {
            throw 'Cleanup target outside workspace'
        }
        if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Recurse -Force }
    }
}
