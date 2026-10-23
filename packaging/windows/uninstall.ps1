<#
.SYNOPSIS
    Remove dontneed-dns from Windows.

.DESCRIPTION
    Removes the scheduled tasks, the executable and the log files.  Only
    resources that belong to this project are touched.  The configuration file
    is kept unless -Purge is given.

.PARAMETER Purge
    Also delete the configuration file.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File uninstall.ps1 -Purge
#>
[CmdletBinding()]
param(
    [switch]$Purge
)

$ErrorActionPreference = 'Stop'

function Write-Step { param([string]$Message) Write-Host "==> $Message" -ForegroundColor Cyan }

$InstallDir = Join-Path $env:LOCALAPPDATA 'dontneed-dns'
$Target = Join-Path $InstallDir 'dontneed-dns.exe'

if (Test-Path $Target) {
    Write-Step "removing the scheduled tasks"
    & $Target uninstall --scope user --yes
    if ($LASTEXITCODE -ne 0) {
        Write-Host "warning: the scheduled tasks could not be removed completely." -ForegroundColor Yellow
    }
} else {
    Write-Step "no installation was found in $InstallDir"
}

if (Test-Path $InstallDir) {
    Write-Step "removing $InstallDir"
    Remove-Item -Path $InstallDir -Recurse -Force -ErrorAction SilentlyContinue
}

$ConfigDir = Join-Path $env:APPDATA 'dontneed-dns'
if ($Purge) {
    if (Test-Path $ConfigDir) {
        Write-Step "removing the configuration in $ConfigDir"
        Remove-Item -Path $ConfigDir -Recurse -Force -ErrorAction SilentlyContinue
    }
} else {
    Write-Host "the configuration in $ConfigDir was kept (use -Purge to remove it)" -ForegroundColor Yellow
}

Write-Host "dontneed-dns was removed" -ForegroundColor Green
