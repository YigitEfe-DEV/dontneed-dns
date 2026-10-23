# Troubleshooting

Start with the health checks; they are read only and never change anything:

```bash
dontneed-dns check
dontneed-dns status
dontneed-dns logs -n 100
```

## The DNS resolver does not answer

```bash
dig @127.0.0.1 -p 5354 discord.com
```

* **"connection refused"** - the component is not running.
  `dontneed-dns dns start`, then look at `journalctl --user -u dontneed-dns-dns.service`.
* **"connection timed out"** - the firewall blocks the loopback interface,
  which is unusual.  Check `sudo nft list ruleset` or the firewall settings.
* **SERVFAIL** - the upstream providers could not be reached.  Test them
  directly:

  ```bash
  curl -s -o /dev/null -w '%{http_code}\n' https://1.1.1.1/dns-query
  ```

  A timeout here means the network blocks the provider, not that the
  project is broken.

## The proxy does not connect

```bash
curl -x socks5h://127.0.0.1:10808 -s https://ifconfig.me
```

* **"connection refused"** - the proxy is not running.
  `dontneed-dns proxy start`.
* **A timeout** - the target could not be reached.  The proxy log names the
  host and the error.
* **The answer is the wrong address** - the resolver returned a cached or
  poisoned record.  The proxy resolves the host itself over DNS-over-HTTPS;
  if `proxy.resolve_via_doh` is `false` in the configuration, the system
  resolver is used instead.

## The application does not use the proxy

Electron and Chromium applications need the proxy to be given explicitly:

```bash
dontneed-dns launch discord
discord --proxy-server="socks5://127.0.0.1:10808"
```

Some applications ignore the `http_proxy` variables.  The `--proxy-server`
switch is the reliable way.

## The service does not start on Linux

```bash
systemctl --user status dontneed-dns-dns.service
journalctl --user -u dontneed-dns-dns.service -n 50
```

The most common causes:

* **The unit file is outdated** after an update:
  `systemctl --user daemon-reload && dontneed-dns dns restart`.
* **The port is already taken** by the previous installation or by another
  program:

  ```bash
  ss -tulpn | grep -E '5354|10808'
  ```

  Stop the other program or change the port in the configuration.
* **The interpreter of the unit no longer exists** - the unit points at the
  Python interpreter that was used during the installation.  Reinstall with
  `dontneed-dns install --scope user --yes`.

## The service does not start on Windows

```powershell
schtasks /Query /TN dontneed-dns-proxy /V
schtasks /Run /TN dontneed-dns-proxy
```

* **"Access is denied"** while the task is created - the terminal is not
  allowed to create a scheduled task.  Run the installer from an elevated
  PowerShell window:

  ```powershell
  powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
  ```

  The output of the installation is written to
  `%LOCALAPPDATA%\dontneed-dns\install-output.txt`.
* **The task was registered for a different user** - remove it with
  `schtasks /Delete /TN dontneed-dns-proxy /F` and register it again with
  `dontneed-dns install --scope user --yes`.
* **The task is "Ready" but nothing happens** - the task has the trigger
  `ONLOGON`, so it starts at the next login.  `dontneed-dns proxy start`
  starts it immediately.
* **The executable is blocked** - Windows may quarantine an unsigned
  executable.  Compare the SHA-256 checksum of the file with the release
  before disabling any protection.
* **The status shows "Running (not registered)"** - the process runs, but
  no task exists for it.  It works, but it does not start again after a
  reboot.  `dontneed-dns install --scope user --yes` registers it.

## The DNS resolver does not answer over TCP

```powershell
Resolve-DnsName -Server 127.0.0.1 -Port 5354 -Type A discord.com
```

* **"nothing is listening on 127.0.0.1:5354"** - the TCP listener is
  missing.  `dontneed-dns check` uses a TCP connection, so this message
  means the TCP listener could not be opened.  The reason is in the log:

  ```powershell
  dontneed-dns logs -n 20
  ```

  A port that is already taken is reported as such.  Change
  `dns.listen_port` in the configuration or stop the other program.
* **Only the UDP listener works** - this was a defect of the releases up to
  0.1.1: the IPv6 listener replaced the IPv4 TCP listener and closed its
  port.  Update to 0.1.2 or newer.

## The port is in use

```bash
# Linux
ss -tulpn | grep 5354
# Windows
netstat -ano | findstr :5354
```

Another instance of the project, or an older installation that was never
removed, is the usual cause.  Stop it first:

```bash
sudo systemctl stop discord-dns-proxy discord-dns-proxy 2>/dev/null
dontneed-dns proxy start
```

## The configuration is rejected

```bash
dontneed-dns config validate
```

The error names the option.  The most common mistakes:

* `dns.listen_port` outside of 1 to 65535,
* `service.scope` other than `user` or `system`,
* `dns.doh_providers` with an unknown name; the known names are
  `cloudflare`, `google` and `quad9`,
* an unknown option - the configuration is validated strictly so that a
  typo cannot silently disable a feature.

## The interface does not start

```bash
dontneed-dns tui
```

* **"the interface could not be started"** - the terminal does not support
  the required capabilities.  Try a different terminal, or use the plain
  commands: `dontneed-dns status` and `dontneed-dns check`.
* **Nothing is displayed** - the terminal is too small.  The interface needs
  at least 80 columns and 24 rows.

## Collecting a report for a bug report

```bash
dontneed-dns doctor -o report.json
```

The report is redacted: the home directory, the user name, the host name,
mail addresses, tokens and globally routable addresses are removed.  Attach
it to the issue.

## Collecting the raw logs on Linux

```bash
journalctl --user -u dontneed-dns-dns.service -n 200 --no-pager > dns.log
journalctl --user -u dontneed-dns-proxy.service -n 200 --no-pager > proxy.log
```

Review the files before attaching them: the log contains the domain names
that were queried.
