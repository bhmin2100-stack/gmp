[CmdletBinding()]
param([switch]$Clean)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSCommandPath
Set-Location $root
$python = if (Test-Path ".venv\\Scripts\\python.exe") { ".venv\\Scripts\\python.exe" } else { "python" }
function Test-PythonImport([string]$ImportCode) {
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "SilentlyContinue"
        & $python -c $ImportCode 2>$null
        return $LASTEXITCODE -eq 0
    } finally {
        $ErrorActionPreference = $previousPreference
    }
}
if (-not (Test-PythonImport "import PySide6, openpyxl, holidays")) {
    & $python -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "Application dependency installation failed." }
}
if (-not (Test-PythonImport "import PyInstaller")) {
    & $python -m pip install pyinstaller
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller installation failed." }
}
$version = (& $python -c "import gmp_scheduler; print(gmp_scheduler.__version__)").Trim()
if (-not $version) { throw "Could not read gmp_scheduler.__version__." }
$buildInfo = Join-Path $root "gmp_scheduler\\build_info.py"
$iconPath = Join-Path $root "assets\\gmp-scheduler.ico"
$iconData = (Join-Path $root "assets\\gmp-scheduler.png") + ";assets"
$originalBuildInfo = [System.IO.File]::ReadAllBytes($buildInfo)
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
    if ($Clean) {
        Remove-Item -Recurse -Force build-company -ErrorAction SilentlyContinue
        Remove-Item -Recurse -Force dist -ErrorAction SilentlyContinue
    }
    $distPath = Join-Path $root "dist"
    $defaultExe = Join-Path $distPath "GMP-Scheduler.exe"
    if (Test-Path $defaultExe) {
        try {
            Remove-Item -LiteralPath $defaultExe -Force -ErrorAction Stop
        } catch {
            $distPath = Join-Path $distPath $version
            Remove-Item -Recurse -Force $distPath -ErrorAction SilentlyContinue
            Write-Host "Existing EXE is in use. Building to: $distPath"
        }
    }
    & $python -m PyInstaller --noconsole --onefile --name "GMP-Scheduler" --icon $iconPath --add-data $iconData --distpath $distPath --workpath build-company --specpath build-company main.py
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }
    $builtExe = Join-Path $distPath "GMP-Scheduler.exe"
    if (-not (Test-Path $builtExe)) { throw "The built EXE was not found." }
    Write-Host "Company EXE: $builtExe"
    Write-Host "Version: $version"
} finally {
    [System.IO.File]::WriteAllBytes($buildInfo, $originalBuildInfo)
}
