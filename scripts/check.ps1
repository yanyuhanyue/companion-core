param([string[]]$TestFiles = @())

$ErrorActionPreference = 'Stop'
$taskRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $taskRoot
$roundTemp = '.local/test-temp/check-' + [guid]::NewGuid().ToString('N')
New-Item -ItemType Directory -Force -Path "$roundTemp/tmp" | Out-Null
$env:TEMP = (Resolve-Path "$roundTemp/tmp").Path
$env:TMP = $env:TEMP
$env:UV_CACHE_DIR = Join-Path $taskRoot '.local/uv-cache'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONIOENCODING = 'utf-8'
try {
    & .venv/Scripts/python.exe -m ruff check companion companion_onebot_bridge source_reader migrations astrbot_plugin_companion_bridge tests scripts "--cache-dir=$roundTemp/ruff-cache"
    if ($LASTEXITCODE -ne 0) { throw 'Lint failed' }
    & .venv/Scripts/python.exe -m pytest -q @TestFiles "--basetemp=$roundTemp/pytest" -o "cache_dir=$roundTemp/pytest-cache"
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed' }
} finally {
    # Each verification owns one directory; unrelated or retained diagnostics are untouched.
    foreach ($relative in @($roundTemp)) {
        $target = [System.IO.Path]::GetFullPath((Join-Path $taskRoot $relative))
        if (-not $target.StartsWith($taskRoot + [System.IO.Path]::DirectorySeparatorChar)) {
            throw 'Cleanup target outside workspace'
        }
        if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Recurse -Force }
    }
}
