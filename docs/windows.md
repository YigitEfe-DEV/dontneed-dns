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

The tasks are created from an XML definition, not from the command line form
of `schtasks`.  Two reasons:

* `schtasks /Create /TR "<command>"` strips the outer quotes of the value,
  which breaks the command as soon as the path of the executable or one of
  its arguments contains a space.
* The XML definition uses a logon trigger for the current user with the
  `InteractiveToken` logon type.  The Task Scheduler creates that without
  administrator rights, while the "at log on" trigger of the command line
  form is rejected with `Access is denied` in a terminal that is not
  elevated.

The task has no execution time limit, so the proxy is not stopped after the
default of 72 hours.

The task starts in the directory of the package.  `python -m` puts the
current directory at the beginning of `sys.path`, so the package is found
even when it is not installed in the interpreter that runs the task.  A
scheduled task runs no shell, so the module path cannot be set with a
shell operator.

Every task is read back after it was created.  A task that exists but runs
another command or belongs to another account is reported as a failed
installation instead of a successful one.

Inspect them with:

```powershell
schtasks /Query /TN dontneed-dns-proxy /XML
```

### When a task cannot be created

The installation does not pretend to have succeeded.  It reports the exact
output of `schtasks` and writes it to
`%LOCALAPPDATA%\dontneed-dns\install-output.txt`.

The most common reason is a terminal that is not allowed to create a
scheduled task.  Running the installer from an **elevated** PowerShell
window solves it:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

The configuration in `%APPDATA%\dontneed-dns` is kept when the installation
fails, so nothing has to be set up again.

### A component that runs but is not registered

When a process runs but no task exists for it, `dontneed-dns status` shows
`Running (not registered)`:

```text
DNS resolver │ Running (not registered) │ no  │ 127.0.0.1:5354
```

The process was started by hand or the registration failed.  It works, but
it will not start again after a reboot or a new login.  The installation
registers it:

```powershell
dontneed-dns install --scope user --yes
```

`dontneed-dns uninstall` stops such a process as well, because it belongs
to this project and would otherwise keep the port.

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
