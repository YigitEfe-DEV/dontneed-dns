# Windows edition

The Windows edition is a native implementation.  It does not port the Linux
mechanisms; the ones that cannot exist on Windows are documented as
unsupported instead of being emulated.

## Supported systems

| System | Status |
| --- | --- |
| Windows 11 x64 | Verified on the GitHub Actions runner |
| Windows 10 x64 | Expected; the same code path is used |

The Windows tests are executed on a real Windows runner on every push.

## What is different from Linux

| Mechanism | Linux | Windows |
| --- | --- | --- |
| Background process | `systemd --user` unit | Scheduled task |
| Service with its own identity | `systemd` system unit | Windows service (needs `pywin32` and administrator rights) |
| Log | `journalctl` | A log file in `%LOCALAPPDATA%` |
| DNS interception | Possible with an explicit, reversible rule | **Unsupported** (would need a driver) |
| `/etc/resolv.conf` | Read only | Not applicable; the Windows resolver is used |
| LD_PRELOAD | Possible | Does not exist |

The most important consequence: **there is no per application DNS
interception on Windows.**  Redirecting the DNS of one application without a
kernel driver is not possible.  The proxy does not need it, because it
resolves the requested host itself over DNS-over-HTTPS before it opens the
connection.  Point the application at the proxy and the plain text DNS
lookup never happens.

## Prerequisites

Nothing.  The release archive contains a self contained executable that
bundles the Python interpreter; Git, a compiler and a Python installation are
not needed.

If the executable is missing, the archive also contains the sources and the
wheels and a Python 3.11 or newer installation can be used instead.

## Installation from the release archive

```powershell
Expand-Archive dontneed-dns-windows-x86_64.zip
cd dontneed-dns-windows-x86_64
powershell -ExecutionPolicy Bypass -File install.ps1
```

The script copies the executable to `%LOCALAPPDATA%\dontneed-dns`, registers
the scheduled tasks and prints the paths.  Add the directory to your `PATH`
to use the short command name:

```powershell
$env:PATH += ";$env:LOCALAPPDATA\dontneed-dns"
```

To start the components right away:

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1 -Start
```

## The scheduled tasks

| Task | What it runs |
| --- | --- |
| `dontneed-dns-dns` | The DNS-over-HTTPS split resolver on `127.0.0.1:5354` |
| `dontneed-dns-proxy` | The proxy on `127.0.0.1:10808` |

The tasks are registered for the current user with the trigger `ONLOGON` and
the lowest run level.  No administrator rights are needed, and the removal
is complete: `schtasks /Delete` removes everything the task created.

Inspect them with:

```powershell
schtasks /Query /TN dontneed-dns-proxy /XML
```

## Windows services (optional)

A component can also be installed as a real Windows service:

```powershell
dontneed-dns install --scope system --yes
```

This needs **both** administrator rights and the `pywin32` package:

```powershell
pip install "dontneed-dns[windows-service]"
```

Without `pywin32` the command reports that the backend is unavailable
instead of failing with an obscure error.

## Everyday commands

```powershell
dontneed-dns status
dontneed-dns dns start
dontneed-dns dns stop
dontneed-dns dns restart
dontneed-dns proxy start
dontneed-dns proxy stop
dontneed-dns proxy restart
dontneed-dns dns enable
dontneed-dns dns disable
dontneed-dns check
dontneed-dns logs -n 100
dontneed-dns config validate
dontneed-dns tui
dontneed-dns doctor -o report.json
```

## Pointing an application at the proxy

```powershell
dontneed-dns launch discord
```

The proxy variables are exported into the environment of the launched process
only.  Chromium based applications also accept the proxy directly:

```powershell
discord --proxy-server="socks5://127.0.0.1:10808"
```

The Windows proxy settings of the system are never changed.

## The local DNS server

The DNS server listens on `127.0.0.1:5354` and answers the configured
domains over DNS-over-HTTPS.  Everything else is resolved by the Windows
resolver; because the Windows resolver cannot be queried with a raw DNS
message from Python, only A and AAAA records are synthesised for the system
fallback.  Every other record type is answered over DNS-over-HTTPS.

## Logs

```powershell
dontneed-dns logs -n 100
Get-Content "$env:LOCALAPPDATA\dontneed-dns\logs\dontneed-dns.log" -Tail 100
```

## Uninstall

```powershell
dontneed-dns uninstall --scope user --yes
powershell -ExecutionPolicy Bypass -File uninstall.ps1
powershell -ExecutionPolicy Bypass -File uninstall.ps1 -Purge
```

The uninstall removes the scheduled tasks, the executable and the log files.
The configuration in `%APPDATA%\dontneed-dns` is kept unless `-Purge` is
given.

## The digital signature

The executable is **not signed**.  Windows shows a warning the first time
it is started.  The archive contains the SHA-256 checksum of every file;
compare it with the `SHA256SUMS` file of the release.

## Troubleshooting

See [troubleshooting.md](troubleshooting.md).
