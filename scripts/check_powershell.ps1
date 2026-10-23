<#
    Validates the PowerShell installation scripts.

    The script parses every .ps1 file of the packaging directory with the
    PowerShell parser and reports the syntax errors.  It is used by the
    workflow and can be run by hand:

        pwsh -File scripts/check_powershell.ps1
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

if ($failed) {
    exit 1
}
Write-Host ""
Write-Host "every PowerShell script parses" -ForegroundColor Green
exit 0
