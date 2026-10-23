<#
.SYNOPSIS
    Install dontneed-dns on Windows.

.DESCRIPTION
    Copies the portable executable to the local application data directory and
    registers the scheduled tasks that run the components in the background of
    the current user.  No administrator rights are required and no system wide
    setting is changed.

.PARAMETER Start
    Start the components right after the installation.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File install.ps1
#>
[CmdletBinding()]
param(
    [switch]$Start
)

$ErrorActionPreference = 'Stop'

function Write-Step { param([string]$Message) Write-Host "==> $Message" -ForegroundColor Cyan }
function Write-Fail { param([string]$Message) Write-Host "error: $Message" -ForegroundColor Red; exit 1 }

$Here = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ExecutableName = 'dontneed-dns.exe'
$Source = Join-Path $Here $ExecutableName
$Wheelhouse = Join-Path $Here 'wheels'

$InstallDir = Join-Path $env:LOCALAPPDATA 'dontneed-dns'
$Target = Join-Path $InstallDir $ExecutableName

if (Test-Path $Source) {
    Write-Step "copying the executable to $InstallDir"
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    Copy-Item -Path $Source -Destination $Target -Force
} elseif (Test-Path $Wheelhouse) {
    Write-Step "the executable is missing; installing from the bundled wheelhouse"
    $Python = (Get-Command python -ErrorAction SilentlyContinue).Source
    if (-not $Python) {
        Write-Fail "python was not found on the PATH and the executable is missing."
    }
    $VenvDir = Join-Path $InstallDir 'venv'
    & $Python -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "the virtual environment could not be created."
    }
    $Script = Join-Path $VenvDir 'Scripts\dontneed-dns.exe'
    & (Join-Path $VenvDir 'Scripts\pip.exe') install --no-index --find-links $Wheelhouse dontneed-dns
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "the installation from the wheelhouse failed."
    }
    $Target = $Script
    Write-Step "the command was installed to $VenvDir"
} else {
    Write-Fail "$ExecutableName was not found next to this script and there is no wheelhouse."
}

Write-Step "checking the installation"
& $Target --version
if ($LASTEXITCODE -ne 0) {
    Write-Fail "the installation did not produce a working command."
}

Write-Step "registering the scheduled tasks"
& $Target install --scope user --yes
if ($LASTEXITCODE -ne 0) {
    Write-Fail "the scheduled tasks could not be registered."
}

if ($Start) {
    Write-Step "starting the components"
    & $Target dns start
    & $Target proxy start
}

Write-Host ""
Write-Host "dontneed-dns is installed." -ForegroundColor Green
Write-Host ""
Write-Host "  command    $Target"
Write-Host "  interface  $Target tui"
Write-Host "  status     $Target status"
Write-Host "  logs       $Target logs"
Write-Host ""
Write-Host "Add $InstallDir to your PATH to use the short command name." -ForegroundColor Yellow
Write-Host ""
