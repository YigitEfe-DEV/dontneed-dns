# Example of a Windows launcher that starts Discord through the local proxy.
#
# The proxy variables only exist in the environment of the started process.
# Copy it next to your Discord shortcut and adjust the paths.

$Proxy = 'http://127.0.0.1:10808'

$env:http_proxy = $Proxy
$env:https_proxy = $Proxy
$env:all_proxy = $Proxy
$env:HTTP_PROXY = $Proxy
$env:HTTPS_PROXY = $Proxy
$env:ALL_PROXY = $Proxy

$Discord = "$env:LOCALAPPDATA\Programs\Discord\Discord.exe"

if (-not (Test-Path $Discord)) {
    Write-Host "Discord was not found at $Discord" -ForegroundColor Red
    exit 1
}

Start-Process -FilePath $Discord -ArgumentList '--proxy-server=socks5://127.0.0.1:10808'
