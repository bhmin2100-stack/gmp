[CmdletBinding()]
param(
    [string]$MetadataUrl = "https://github.com/bhmin2100-stack/gmp/releases/download/windows-latest/company-build.json",
    [string]$ExeUrl = "https://github.com/bhmin2100-stack/gmp/releases/download/windows-latest/GMP-Scheduler-company.exe",
    [string]$MetadataFile,
    [string]$ExeFile,
    [string]$OutputPath,
    [string]$BuildRef = "refs/remotes/origin/company-build"
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

function Find-GitExecutable {
    $gitCommand = Get-Command git -ErrorAction SilentlyContinue
    if ($gitCommand) {
        return $gitCommand.Source
    }

    $desktopRoot = Join-Path $env:LOCALAPPDATA "GitHubDesktop"
    $desktopGit = Get-ChildItem -Path (Join-Path $desktopRoot "app-*\resources\app\git\cmd\git.exe") -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending |
        Select-Object -First 1
    if ($desktopGit) {
        return $desktopGit.FullName
    }
    return $null
}

function Copy-CompanyBuildFromGitRef {
    param([string]$GitRef)

    $gitExe = Find-GitExecutable
    if (-not $gitExe) {
        throw "GitHub Release download was blocked and GitHub Desktop Git could not be found."
    }

    & $gitExe -C $root rev-parse --verify --quiet $GitRef 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "The company-build branch is not available locally. In GitHub Desktop, click Fetch origin after GitHub Actions finishes, then run the BAT again."
    }

    $archivePath = Join-Path $tempRoot "company-build.zip"
    $extractPath = Join-Path $tempRoot "from-git"
    & $gitExe -C $root archive --format=zip "--output=$archivePath" $GitRef
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $archivePath)) {
        throw "Could not read the company-build branch downloaded by GitHub Desktop."
    }
    Expand-Archive -LiteralPath $archivePath -DestinationPath $extractPath -Force
    Copy-Item -LiteralPath (Join-Path $extractPath "company-build.json") -Destination $tempMetadata -Force
    Copy-Item -LiteralPath (Join-Path $extractPath "GMP-Scheduler-company.exe") -Destination $tempExe -Force
}

try {
    New-Item -ItemType Directory -Path $tempRoot -Force | Out-Null
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

    if ($MetadataFile -and $ExeFile) {
        Copy-Item -LiteralPath $MetadataFile -Destination $tempMetadata
        Copy-Item -LiteralPath $ExeFile -Destination $tempExe
    } else {
        try {
            Write-Host "Downloading company build metadata..."
            Invoke-WebRequest -UseBasicParsing -Uri $MetadataUrl -OutFile $tempMetadata
            Write-Host "Downloading transition-compatible company EXE..."
            Invoke-WebRequest -UseBasicParsing -Uri $ExeUrl -OutFile $tempExe
        } catch {
            Write-Host "Direct GitHub Release download was blocked. Using the build fetched by GitHub Desktop..."
            Copy-CompanyBuildFromGitRef -GitRef $BuildRef
        }
    }

    $metadata = Get-Content -LiteralPath $tempMetadata -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($metadata.version -ne $sourceVersion) {
        throw "The company build version is $($metadata.version), but this source is $sourceVersion. Wait for GitHub Actions to finish, click Fetch origin in GitHub Desktop, then run the BAT again."
    }
    if ($metadata.commit -ne $sourceCommit) {
        throw "The company build is from commit $($metadata.commit), but this source is $sourceCommit. Wait for GitHub Actions to finish, click Fetch origin in GitHub Desktop, then run the BAT again."
    }
    if ($metadata.update_channel -ne "company") {
        throw "The selected build is not configured for the company update channel."
    }
    if (-not $metadata.sha256) {
        throw "The company build metadata does not contain SHA-256."
    }

    $actualHash = (Get-FileHash -LiteralPath $tempExe -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -ne ([string]$metadata.sha256).ToLowerInvariant()) {
        throw "Downloaded company EXE SHA-256 verification failed."
    }

    $outputDirectory = Split-Path -Parent $OutputPath
    New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
    Move-Item -LiteralPath $tempExe -Destination $OutputPath -Force
    $releaseNotesOutput = Join-Path $outputDirectory "RELEASE-NOTES.txt"
    if ($metadata.notes) {
        [System.IO.File]::WriteAllText(
            $releaseNotesOutput,
            ([string]$metadata.notes).Trim(),
            [System.Text.UTF8Encoding]::new($false)
        )
    } elseif (Test-Path $releaseNotesOutput) {
        [System.IO.File]::Delete($releaseNotesOutput)
    }
    Write-Host "Company EXE: $OutputPath"
    Write-Host "Version: $sourceVersion"
    Write-Host "Commit: $sourceCommit"
    Write-Host "SHA-256: $actualHash"
} finally {
    Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
}
