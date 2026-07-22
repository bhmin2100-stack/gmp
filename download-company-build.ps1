[CmdletBinding()]
param(
    [string]$MetadataUrl = "https://github.com/bhmin2100-stack/gmp/releases/download/windows-latest/company-build.json",
    [string]$ExeUrl = "https://github.com/bhmin2100-stack/gmp/releases/download/windows-latest/GMP-Scheduler-company.exe",
    [string]$MetadataFile,
    [string]$ExeFile,
    [string]$OutputPath
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSCommandPath
Set-Location $root

if (-not $OutputPath) {
    $OutputPath = Join-Path $root "dist\GMP-Scheduler.exe"
}

$versionSource = [System.IO.File]::ReadAllText((Join-Path $root "gmp_scheduler\__init__.py"))
$versionMatch = [regex]::Match($versionSource, '__version__\s*=\s*["'']([^"'']+)["'']')
if (-not $versionMatch.Success) {
    throw "Could not read gmp_scheduler.__version__."
}
$sourceVersion = $versionMatch.Groups[1].Value

$sourceCommit = ""
if (Get-Command git -ErrorAction SilentlyContinue) {
    $sourceCommit = (& git rev-parse HEAD 2>$null).Trim()
}
if (-not $sourceCommit) {
    $gitDirectory = Join-Path $root ".git"
    $headPath = Join-Path $gitDirectory "HEAD"
    if (Test-Path $headPath) {
        $head = [System.IO.File]::ReadAllText($headPath).Trim()
        if ($head.StartsWith("ref: ")) {
            $refName = $head.Substring(5)
            $looseRefPath = Join-Path $gitDirectory $refName
            if (Test-Path $looseRefPath) {
                $sourceCommit = [System.IO.File]::ReadAllText($looseRefPath).Trim()
            } else {
                $packedRefsPath = Join-Path $gitDirectory "packed-refs"
                if (Test-Path $packedRefsPath) {
                    $packedMatch = Select-String -LiteralPath $packedRefsPath -Pattern ("^([0-9a-f]{40}) " + [regex]::Escape($refName) + "$") | Select-Object -First 1
                    if ($packedMatch) {
                        $sourceCommit = $packedMatch.Matches[0].Groups[1].Value
                    }
                }
            }
        } elseif ($head -match "^[0-9a-f]{40}$") {
            $sourceCommit = $head
        }
    }
}
if ($sourceCommit -notmatch "^[0-9a-f]{40}$") {
    throw "Could not read the current Git commit. Pull the repository with GitHub Desktop first."
}

$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("gmp-company-build-" + [guid]::NewGuid().ToString("N"))
$tempMetadata = Join-Path $tempRoot "company-build.json"
$tempExe = Join-Path $tempRoot "GMP-Scheduler-company.exe"

try {
    New-Item -ItemType Directory -Path $tempRoot -Force | Out-Null
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

    if ($MetadataFile) {
        Copy-Item -LiteralPath $MetadataFile -Destination $tempMetadata
    } else {
        Write-Host "Downloading company build metadata..."
        Invoke-WebRequest -UseBasicParsing -Uri $MetadataUrl -OutFile $tempMetadata
    }

    $metadata = Get-Content -LiteralPath $tempMetadata -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($metadata.version -ne $sourceVersion) {
        throw "The GitHub company build version is $($metadata.version), but this source is $sourceVersion. Wait for GitHub Actions to finish, then run the BAT again."
    }
    if ($metadata.commit -ne $sourceCommit) {
        throw "The GitHub company build is from commit $($metadata.commit), but this source is $sourceCommit. Wait for GitHub Actions to finish, then run the BAT again."
    }
    if ($metadata.update_channel -ne "company") {
        throw "The downloaded build is not configured for the company update channel."
    }
    if (-not $metadata.sha256) {
        throw "The company build metadata does not contain SHA-256."
    }

    if ($ExeFile) {
        Copy-Item -LiteralPath $ExeFile -Destination $tempExe
    } else {
        Write-Host "Downloading transition-compatible company EXE..."
        Invoke-WebRequest -UseBasicParsing -Uri $ExeUrl -OutFile $tempExe
    }

    $actualHash = (Get-FileHash -LiteralPath $tempExe -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -ne ([string]$metadata.sha256).ToLowerInvariant()) {
        throw "Downloaded company EXE SHA-256 verification failed."
    }

    $outputDirectory = Split-Path -Parent $OutputPath
    New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
    Move-Item -LiteralPath $tempExe -Destination $OutputPath -Force
    Write-Host "Company EXE: $OutputPath"
    Write-Host "Version: $sourceVersion"
    Write-Host "Commit: $sourceCommit"
    Write-Host "SHA-256: $actualHash"
} finally {
    Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
}
