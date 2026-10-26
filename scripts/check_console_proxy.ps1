[CmdletBinding()]
param([string]$Python = '.venv/Scripts/python.exe', [int]$Workers = 2, [switch]$PrepareOnly)
$ErrorActionPreference = 'Stop'
$taskRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $taskRoot
$previous = @{}
foreach ($name in @('COMPANION_TEST_NGINX', 'COMPANION_TEST_BROWSER', 'COMPANION_TEST_CONSOLE_PROXY')) {
    $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
try {
    if (-not $env:COMPANION_TEST_NGINX) {
        if ($IsWindows) {
            $directory = Join-Path $taskRoot '.local/tools'
            $archive = Join-Path $directory 'nginx-1.31.8.zip'
            New-Item -ItemType Directory -Force -Path $directory | Out-Null
            if (-not (Test-Path -LiteralPath $archive)) {
                Invoke-WebRequest 'https://nginx.org/download/nginx-1.31.8.zip' -OutFile $archive
            }
            if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne
                'fbb1501009be34655cc238d460714cf20391b639013f4c73859813d7938783fd') {
                throw 'Official Windows Nginx archive hash mismatch'
            }
            Expand-Archive -LiteralPath $archive -DestinationPath $directory -Force
            $env:COMPANION_TEST_NGINX = Join-Path $directory 'nginx-1.31.8/nginx.exe'
        } else {
            $env:COMPANION_TEST_NGINX = (Get-Command nginx -ErrorAction Stop).Source
        }
    }
    if ($PrepareOnly) {
        Write-Output $env:COMPANION_TEST_NGINX
        return
    }
    $env:COMPANION_TEST_BROWSER = '1'
    $env:COMPANION_TEST_CONSOLE_PROXY = '1'
    ./scripts/check.ps1 -Python $Python -Workers $Workers -TestFiles @(
        'tests/test_console_static.py', 'tests/test_console_proxy.py',
        'tests/test_console_frontend.py', 'tests/test_owner_console.py',
        'tests/test_console_operation_failures.py', 'tests/test_console_browser.py',
        'tests/test_console_supplement_browser.py'
    ) -JunitXml '.local/test-results/console-proxy.xml'
} finally {
    foreach ($name in $previous.Keys) {
        [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process')
    }
}
