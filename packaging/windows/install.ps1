<#
.SYNOPSIS
    Install dontneed-dns on Windows.

.DESCRIPTION
    Copies the portable executable to the local application data directory and
    registers the scheduled tasks that run the components in the background of
    the current user.  No administrator rights are required and no system wide
    setting is changed.

    The output of every step is written to a log file, because the reason of
    a failure of the Task Scheduler is often the only useful information and
    it would otherwise be lost.

.PARAMETER Start
    Start the components right after the installation.

.PARAMETER LogFile
    Where the output of the installation is written.  The default is a file
    next to the installed executable.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File install.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File install.ps1 -Start
#>
[CmdletBinding()]
param(
    [switch]$Start,
    [string]$LogFile
)

$ErrorActionPreference = 'Stop'

$Transcript = New-Object System.Collections.Generic.List[string]

function Write-Step {
    param([string]$Message)
    Write-Host "==> $Message" -ForegroundColor Cyan
    $Transcript.Add("==> $Message")
}

function Write-Note {
    param([string]$Message)
    Write-Host $Message
    $Transcript.Add($Message)
}

function Write-Fail {
    param([string]$Message)
    Write-Host "error: $Message" -ForegroundColor Red
    $Transcript.Add("error: $Message")
    Save-Transcript
    exit 1
}

function Save-Transcript {
    if (-not $LogFile) { return }
    try {
        $directory = Split-Path -Parent $LogFile
        if ($directory -and -not (Test-Path $directory)) {
            New-Item -ItemType Directory -Force -Path $directory | Out-Null
        }
        Set-Content -Path $LogFile -Value $Transcript -Encoding UTF8
    } catch {
        Write-Host "warning: the log file could not be written: $_" -ForegroundColor Yellow
    }
}

function Invoke-Native {
    <#
        Runs a native command and returns its output.

        The error preference is lowered for the call, because the output of
        a native command on the error stream would otherwise stop the script
        before the exit code could be looked at.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @()
    )
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = & $FilePath @Arguments 2>&1
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
    foreach ($line in $output) {
        Write-Note "    $line"
    }
    return @{ Output = ($output | Out-String).Trim(); ExitCode = $code }
}

$Here = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ExecutableName = 'dontneed-dns.exe'
$Source = Join-Path $Here $ExecutableName
$Wheelhouse = Join-Path $Here 'wheels'

$InstallDir = Join-Path $env:LOCALAPPDATA 'dontneed-dns'
$Target = Join-Path $InstallDir $ExecutableName
if (-not $LogFile) {
    $LogFile = Join-Path $InstallDir 'install-output.txt'
}

Write-Step "installing dontneed-dns"

if (Test-Path $Source) {
    Write-Step "copying the executable to $InstallDir"
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    Copy-Item -Path $Source -Destination $Target -Force
} elseif (Test-Path $Wheelhouse) {
    Write-Step "the executable is missing; installing from the bundled wheelhouse"
    $Python = (Get-Command python -ErrorAction SilentlyContinue).Source
    if (-not $Python) {
        $Python = (Get-Command python3 -ErrorAction SilentlyContinue).Source
    }
    if (-not $Python) {
        Write-Fail "python was not found on the PATH and the executable is missing."
    }
    $VenvDir = Join-Path $InstallDir 'venv'
    & $Python -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "the virtual environment could not be created."
    }
    $Target = Join-Path $VenvDir 'Scripts\dontneed-dns.exe'
    $pip = Join-Path $VenvDir 'Scripts\pip.exe'
    $result = Invoke-Native -FilePath $pip -Arguments @('install', '--no-index', '--find-links', $Wheelhouse, 'dontneed-dns')
    if ($result.ExitCode -ne 0) {
        Write-Fail "the installation from the wheelhouse failed."
    }
    Write-Step "the command was installed to $VenvDir"
} else {
    Write-Fail "$ExecutableName was not found next to this script and there is no wheelhouse."
}

Write-Step "checking the installation"
$result = Invoke-Native -FilePath $Target -Arguments @('--version')
if ($result.ExitCode -ne 0) {
    Write-Fail "the installation did not produce a working command."
}

Write-Step "registering the scheduled tasks"
$result = Invoke-Native -FilePath $Target -Arguments @('install', '--scope', 'user', '--yes')
if ($result.ExitCode -ne 0) {
    Write-Fail @"
the scheduled tasks could not be registered (exit code $($result.ExitCode)).

The output above is the reason.  The most common cause is a terminal that
is not allowed to create a scheduled task; running this script from an
elevated PowerShell window usually solves it.

The configuration in $env:APPDATA\dontneed-dns was kept, so nothing has to
be set up again after the problem is solved.
"@
}

# The tasks are read back, so that a task that exists but does not do the
# right thing is noticed here and not later.
if (Get-Command schtasks -ErrorAction SilentlyContinue) {
    foreach ($task in @('dontneed-dns-dns', 'dontneed-dns-proxy')) {
        $query = Invoke-Native -FilePath 'schtasks' -Arguments @('/Query', '/TN', $task)
        if ($query.ExitCode -ne 0) {
            Write-Fail "the task $task does not exist although the installation reported no error."
        }
    }
} else {
    Write-Note "    schtasks was not found; the tasks could not be verified."
}

if ($Start) {
    Write-Step "starting the components"
    foreach ($component in @('dns', 'proxy')) {
        $started = Invoke-Native -FilePath $Target -Arguments @($component, 'start')
        if ($started.ExitCode -ne 0) {
            Write-Fail "the $component component could not be started."
        }
    }
}

Save-Transcript

Write-Host ""
Write-Host "dontneed-dns is installed." -ForegroundColor Green
Write-Host ""
Write-Host "  command    $Target"
Write-Host "  interface  $Target tui"
Write-Host "  status     $Target status"
Write-Host "  logs       $Target logs"
Write-Host "  log file   $LogFile"
Write-Host ""
Write-Host "Add $InstallDir to your PATH to use the short command name." -ForegroundColor Yellow
Write-Host ""
