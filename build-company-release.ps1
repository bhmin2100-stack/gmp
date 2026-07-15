[CmdletBinding()]
param([switch]$Clean)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSCommandPath
Set-Location $root
$python = if (Test-Path ".venv\\Scripts\\python.exe") { ".venv\\Scripts\\python.exe" } else { "python" }
$null = & $python -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    & $python -m pip install pyinstaller
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller installation failed." }
}
$version = (& $python -c "import gmp_scheduler; print(gmp_scheduler.__version__)").Trim()
if (-not $version) { throw "Could not read gmp_scheduler.__version__." }
$buildInfo = Join-Path $root "gmp_scheduler\\build_info.py"
$originalBuildInfo = Get-Content -LiteralPath $buildInfo -Raw
$buildDate = (Get-Date).ToUniversalTime().ToString("o")
$commit = (git rev-parse HEAD).Trim()
$buildId = "company-local-" + (Get-Date -Format "yyyyMMddHHmmss")
try {
    @"
from __future__ import annotations

APP_VERSION = "$version"
BUILD_COMMIT = "$commit"
BUILD_ID = "$buildId"
BUILD_DATE = "$buildDate"
UPDATE_CHANNEL = "company"
"@ | Set-Content -LiteralPath $buildInfo -Encoding utf8
    $embeddedVersion = (& $python -c "from gmp_scheduler import __version__; from gmp_scheduler.build_info import APP_VERSION, UPDATE_CHANNEL; print(f'{__version__}|{APP_VERSION}|{UPDATE_CHANNEL}')").Trim()
    if ($embeddedVersion -ne "$version|$version|company") { throw "Build version validation failed: $embeddedVersion" }
    if ($Clean) { Remove-Item -Recurse -Force build-company, dist -ErrorAction SilentlyContinue }
    & $python -m PyInstaller --noconsole --onefile --name "GMP-Scheduler" --distpath dist --workpath build-company --specpath build-company main.py
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }
    if (-not (Test-Path "dist\\GMP-Scheduler.exe")) { throw "The built EXE was not found." }
    Write-Host "Company EXE: $root\\dist\\GMP-Scheduler.exe"
    Write-Host "Version: $version"
} finally {
    $originalBuildInfo | Set-Content -LiteralPath $buildInfo -Encoding utf8
}
