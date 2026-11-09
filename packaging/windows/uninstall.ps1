<#
.SYNOPSIS
    Remove dontneed-dns from Windows.

.DESCRIPTION
    Removes the scheduled tasks, the executable and the log files.  Only
    resources that belong to this project are touched.  The configuration file
    is kept unless -Purge is given.

    The scheduled tasks are removed by their name and not through the
    installed program.  The installation can also live in a virtual
    environment, and a task that points at a program that no longer exists
    would fail at every logon.

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

function Invoke-Native {
    <#
        Runs a native command and returns its exit code.

        The error preference is lowered for the call: the output of a native
        command on the error stream would otherwise stop the script before
        the exit code could be looked at.  A command that writes to the
        error stream has not failed; the exit code decides.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @()
    )
    # A program that cannot be started at all leaves the exit code of an
    # earlier command in place, so it is looked for before it is run.
    if (-not (Test-Path -LiteralPath $FilePath) -and
        -not (Get-Command $FilePath -ErrorAction SilentlyContinue)) {
        return 127
    }
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $global:LASTEXITCODE = 0
    try {
        $output = & $FilePath @Arguments 2>&1
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
    foreach ($line in $output) {
        Write-Host "    $line"
    }
    return $code
}

# The names of the scheduled tasks of this project.  They are the namespace
# of the project, so removing them cannot touch a task of somebody else.
$TaskNames = @('dontneed-dns-dns', 'dontneed-dns-proxy')

$LocalAppData = $env:LOCALAPPDATA
if (-not $LocalAppData) {
    $LocalAppData = Join-Path $env:USERPROFILE 'AppData\Local'
}
$InstallDir = Join-Path $LocalAppData 'dontneed-dns'
$Target = Join-Path $InstallDir 'dontneed-dns.exe'

if (Test-Path $Target) {
    Write-Step "stopping the components"
    $code = Invoke-Native -FilePath $Target -Arguments @('uninstall', '--scope', 'user', '--yes')
    if ($code -ne 0) {
        Write-Host "warning: the components could not be stopped completely." -ForegroundColor Yellow
    }
}

Write-Step "removing the scheduled tasks"
if (Get-Command schtasks -ErrorAction SilentlyContinue) {
    foreach ($task in $TaskNames) {
        # A task that does not exist is not a problem; it is the wanted state.
        Invoke-Native -FilePath 'schtasks' -Arguments @('/Delete', '/TN', $task, '/F') | Out-Null
    }
} else {
    Write-Host "    schtasks was not found; the scheduled tasks could not be removed." -ForegroundColor Yellow
}

if (Test-Path $InstallDir) {
    Write-Step "removing $InstallDir"
    # A running program keeps its file locked on Windows, so the removal is
    # repeated until it worked.  The parent of the one file executable is
    # still holding it for a moment after it was terminated.
    for ($attempt = 0; $attempt -lt 20 -and (Test-Path $InstallDir); $attempt++) {
        Remove-Item -Path $InstallDir -Recurse -Force -ErrorAction SilentlyContinue
        if (Test-Path $InstallDir) { Start-Sleep -Milliseconds 250 }
    }
    if (Test-Path $InstallDir) {
        Write-Host "warning: $InstallDir could not be removed completely; close the program and try again." -ForegroundColor Yellow
    }
}

$RoamingAppData = $env:APPDATA
if (-not $RoamingAppData) {
    $RoamingAppData = Join-Path $env:USERPROFILE 'AppData\Roaming'
}
$ConfigDir = Join-Path $RoamingAppData 'dontneed-dns'
if ($Purge) {
    if (Test-Path $ConfigDir) {
        Write-Step "removing the configuration in $ConfigDir"
        Remove-Item -Path $ConfigDir -Recurse -Force -ErrorAction SilentlyContinue
    }
} else {
    Write-Host "the configuration in $ConfigDir was kept (use -Purge to remove it)" -ForegroundColor Yellow
}

Write-Host "dontneed-dns was removed" -ForegroundColor Green
