<#
    Validates the PowerShell installation scripts.

    The script parses every .ps1 file of the packaging directory with the
    PowerShell parser and reports the syntax errors.  It is used by the
    workflow and can be run by hand:

        pwsh -File scripts/check_powershell.ps1

    The parser of the host is not enough.  The end user runs the scripts
    with "powershell", which is Windows PowerShell 5.1, while the workflow
    runs with "pwsh", which is PowerShell 7.  A script that uses a syntax
    of PowerShell 7 parses cleanly on the runner and fails on the machine
    of the user, so every available engine parses every file.
#>
[CmdletBinding()]
param(
    [string]$Directory = (Join-Path (Split-Path -Parent $PSScriptRoot) 'packaging')
)

$ErrorActionPreference = 'Stop'

$files = Get-ChildItem -Path $Directory -Filter '*.ps1' -Recurse -File
if (-not $files) {
    Write-Host "no PowerShell script was found in $Directory" -ForegroundColor Red
    exit 1
}

# The parser of the host is used first, because it reports the errors with
# the file and the line number.
$failed = $false
foreach ($file in $files) {
    $tokens = $null
    $errors = $null
    [System.Management.Automation.Language.Parser]::ParseFile(
        $file.FullName, [ref]$tokens, [ref]$errors) | Out-Null

    if ($errors -and $errors.Count -gt 0) {
        $failed = $true
        Write-Host "$($file.Name): $($errors.Count) syntax error(s)" -ForegroundColor Red
        foreach ($error in $errors) {
            Write-Host "  line $($error.Extent.StartLineNumber): $($error.Message)"
        }
    } else {
        Write-Host "$($file.Name): syntax ok" -ForegroundColor Green
    }
}

# Every other engine parses the files as well.  The file name is passed
# through the environment, so that a path with a space in it stays one
# argument.
$engines = @()
foreach ($name in @('powershell', 'pwsh')) {
    $command = Get-Command $name -ErrorAction SilentlyContinue
    if ($command -and $command.Source) { $engines += $command.Source }
}
$engines = $engines | Select-Object -Unique

foreach ($engine in $engines) {
    foreach ($file in $files) {
        $env:DONTNEED_DNS_PS1_FILE = $file.FullName
        $previous = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        $global:LASTEXITCODE = 0
        try {
            & $engine -NoProfile -NonInteractive -Command @'
$tokens = $null
$errors = $null
[void][System.Management.Automation.Language.Parser]::ParseFile(
    $env:DONTNEED_DNS_PS1_FILE, [ref]$tokens, [ref]$errors)
if ($errors -and $errors.Count -gt 0) {
    foreach ($error in $errors) {
        Write-Host ("  line {0}: {1}" -f $error.Extent.StartLineNumber, $error.Message)
    }
    exit 1
}
exit 0
'@
            $code = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $previous
            Remove-Item Env:\DONTNEED_DNS_PS1_FILE -ErrorAction SilentlyContinue
        }
        if ($code -ne 0) {
            $failed = $true
            Write-Host "$($file.Name): $($engine) cannot parse the script" -ForegroundColor Red
        } else {
            Write-Host "$($file.Name): ok in $($engine)" -ForegroundColor Green
        }
    }
}

if ($failed) {
    exit 1
}
Write-Host ""
Write-Host "every PowerShell script parses" -ForegroundColor Green
exit 0
