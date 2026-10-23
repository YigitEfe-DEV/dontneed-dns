# Linux edition

The Linux edition manages the two components with **systemd**.  The default
scope is the *user* scope (`systemctl --user`), which needs no privileges at
all and only touches files inside your home directory.

## Supported distributions

| Distribution | Status | Notes |
| --- | --- | --- |
| CachyOS | Verified | The primary development target |
| Arch Linux | Supported | Same systemd units as CachyOS |
| Ubuntu | Supported | Verified on the GitHub Actions runner |
| Debian | Supported | Verified on the GitHub Actions runner |
| Fedora | Supported | Verified on the GitHub Actions runner |
| openSUSE | Expected | Uses systemd, the units are portable |
| Other distributions | Unknown | The distribution is detected and reported by `dontneed-dns status` |

`dontneed-dns status` prints the detected distribution and whether systemd
was found.  The detection lives in `platforms/linux/distro.py` and is based
on `/etc/os-release`.

## Prerequisites

* Python 3.11 or newer
* systemd (the `systemctl` command)
* `journalctl` for the log output

No compiler and no development headers are needed.

## Installation from the release archive

```bash
unzip dontneed-dns-linux-x86_64.zip
cd dontneed-dns-linux-x86_64
./install.sh
```

The script creates a virtual environment in
`${XDG_DATA_HOME:-$HOME/.local/share}/dontneed-dns/venv`, installs the
project from the bundled wheelhouse (no network access needed) and
registers the systemd user services.

Add the virtual environment to your `PATH` to use the short command name:

```bash
export PATH="$HOME/.local/share/dontneed-dns/venv/bin:$PATH"
```

## Installation from the source tree

```bash
git clone https://github.com/YigitEfe-DEV/dontneed-dns
cd dontneed-dns
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/dontneed-dns install --scope user --yes
```

## The systemd units

The installation writes two units into `~/.config/systemd/user`:

| Unit | What it runs |
| --- | --- |
| `dontneed-dns-dns.service` | The DNS-over-HTTPS split resolver on `127.0.0.1:5354` |
| `dontneed-dns-proxy.service` | The proxy on `127.0.0.1:10808` |

Both units run as your own user, restart on failure and are protected with
`NoNewPrivileges`, `PrivateTmp` and `ProtectSystem=strict`.  The DNS unit
may only write into the state directory.

### The system scope

```bash
sudo dontneed-dns install --scope system --yes
```

This writes the units to `/etc/systemd/system`, puts the configuration into
`/etc/dontneed-dns/config.toml` and runs the components as `nobody`.  The
system scope exists for the case where the components have to run for every
user on the machine.  It is never selected automatically.

## Everyday commands

```bash
dontneed-dns status                 # the state of both components
dontneed-dns dns start
dontneed-dns dns stop
dontneed-dns dns restart
dontneed-dns proxy start
dontneed-dns proxy stop
dontneed-dns proxy restart
dontneed-dns dns enable              # start automatically at login
dontneed-dns dns disable
dontneed-dns check                   # the health checks
dontneed-dns logs -n 100
dontneed-dns config validate
dontneed-dns config path
dontneed-dns tui
dontneed-dns doctor -o report.json
```

## Pointing an application at the proxy

The command only affects the process it starts:

```bash
dontneed-dns launch discord
```

The proxy variables (`http_proxy`, `https_proxy`, `all_proxy`) are exported
into the environment of the launched process and nowhere else.  No system
wide setting is changed.

Chromium based applications such as Discord also accept the proxy directly:

```bash
discord --proxy-server="socks5://127.0.0.1:10808"
```

## Split DNS

The DNS server answers the configured domains over DNS-over-HTTPS and
forwards everything else to the nameservers of `/etc/resolv.conf`.  It only
listens on the loopback interface and does not change the system resolver
on its own.

To use it, point the application at `127.0.0.1:5354`.  Chromium based
applications can do that with a host resolver rule:

```bash
discord --host-resolver-rules="MAP discord.com 127.0.0.1:5354"
```

`dontneed-dns` never edits `/etc/resolv.conf` and never touches the firewall
or the routing table by itself.

## Logs

```bash
journalctl --user -u dontneed-dns-dns.service -f
journalctl --user -u dontneed-dns-proxy.service -f
dontneed-dns logs -n 100
```

## Uninstall

```bash
dontneed-dns uninstall --scope user --yes
```

The command stops and disables the units and removes the unit files.  The
configuration file in `~/.config/dontneed-dns/` is kept; add `--purge` to
remove it as well.

From the release archive:

```bash
./uninstall.sh          # keeps the configuration
./uninstall.sh --purge  # removes the configuration as well
```

Nothing outside of the files listed above is touched.

## Troubleshooting

See [troubleshooting.md](troubleshooting.md).
