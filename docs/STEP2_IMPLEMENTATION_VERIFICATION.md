# STEP 2 — Implementation Verification

Repository: `https://github.com/YigitEfe-DEV/dontneed-dns`
Branch: `main` · HEAD `69b196d` (2026-11-09) · version `0.1.2`
Verification host: Fedora Linux 45 (KDE Plasma Desktop Edition), x86_64, systemd 262, SELinux Enforcing, firewalld active, Python 3.15.0
Date: 2026-11-20

This document is the result of an investigation only. No source file was modified, no service was started, stopped or reconfigured, no system DNS, NetworkManager, nftables or firewalld setting was touched. The only file added by this step is this report.

---

## 1. Test suite result

The repository was tested with an isolated interpreter created outside the repository (`/tmp/dnd-venv`, Python 3.15.0). The repository tree itself was not modified (`git status` clean before and after; pytest was run with `-p no:cacheprovider` and `PYTHONDONTWRITEBYTECODE=1` so that no caches were written into the tree).

```
$ PYTHONPATH=src python -m pytest tests/ -p no:cacheprovider -q --tb=short -ra
356 passed, 6 skipped in 17.42s
```

| Item | Result |
| --- | --- |
| Passed | 356 |
| Failed | 0 |
| Skipped | 6 — all in `tests/unit/test_powershell_scripts.py` (`PowerShell is not available`); `pwsh`/`powershell` are not installed on this host |
| Errors | 0 |
| Warnings | none emitted by pytest |
| Static checks | `ruff format --check src/ tests/ scripts/` → "74 files already formatted"; `ruff check src/ tests/ scripts/` → "All checks passed!"; `mypy` → "Success: no issues found in 38 source files" |

Notes on the environment (does not change any conclusion):

* The system interpreter has **no `pip` module** (`python3 -m pip` → "No module named pip") and no `pytest`, `typer`, `rich`, `textual`. `ensurepip` and `venv` are present, so `python3 -m venv` produces a working pip (`pip 26.1.2`). `packaging/linux/install.sh` therefore works on this host, but a source installation with the system interpreter (`pip install -e .`) does not, until `python3-pip` is installed.
* The pre-existing installation at `~/.local/share/dontneed-dns/venv` is an **editable install pointing at the working tree** (`dontneed_dns.__file__ == ~/dontneed-dns/src/dontneed_dns/__init__.py`). The live services therefore run the repository code, not the release archive code. This matters for interpreting the live behaviour observed below.
* The live user configuration (`~/.config/dontneed-dns/config.toml`) has been hand-extended with Roblox domains (`roblox.com`, `rbxcdn.com`, `robloxlabs.com`, `rbx.com`, `rbxapi.com`, `rbxinfra.com`). Those are **not** in the repository (`grep -ri roblox` → 0 hits).

---

## 2. Environment inventory (read-only)

| Check | Value |
| --- | --- |
| Fedora version | 45 (`VERSION_ID=45`, `ID=fedora`, KDE Plasma Desktop Edition) |
| Python | 3.15.0 at `/usr/bin/python3`; no system pip; `ensurepip` + `venv` present |
| systemd | 262 (262-3.fc45), `+SELINUX +PAM +AUDIT` |
| NetworkManager | `active` |
| systemd-resolved | `active` and `enabled`; `resolv.conf mode: stub` |
| `/etc/resolv.conf` | symlink → `/run/systemd/resolve/stub-resolv.conf`; single entry `nameserver 127.0.0.53`, plus `options edns0 trust-ad` and `search .` |
| Stub listener | `127.0.0.53%lo:53` UDP **and** TCP (`ss -ltn 'sport = :53'`) |
| Uplink DNS (from `resolvectl`) | `wlp3s0`: `192.168.1.1`; `enp2s0`: no DNS |
| nftables | binary present, `nftables.service` **inactive**, `nft list ruleset` → "Operation not permitted (you must be root)" |
| firewalld | `active`, `firewall-cmd --state` → `running` |
| SELinux | `Enforcing` |
| Project state | both user units `enabled` (symlinked in `~/.config/systemd/user/default.target.wants/`), both `inactive (dead)` since 14:38 (clean SIGTERM shutdown, exit 0) |

---

## 3. Confirmed defects

### D-01 — MEDIUM — The bundled `quad9` DoH provider cannot work with this client

* **File / symbol:** `src/dontneed_dns/core/doh.py` → `_https_post()`, `DEFAULT_PROVIDERS`
* **Current behaviour:** the client always speaks HTTP/1.1 over TLS and never negotiates ALPN. `dns.quad9.net` answers HTTP/1.1 requests with `505 HTTP Version Not Supported`, so every attempt against the `quad9` provider fails. The provider is one of the three defaults in `DEFAULT_SPLIT_DOMAINS`-adjacent configuration (`dns.doh_providers` defaults to all three).
* **Evidence:**
  ```
  cloudflare ok rcode=0 ancount=5 addrs=['162.159.128.233', ...] (0.55s)
  google     ok rcode=0 ancount=5 addrs=['162.159.137.232', ...] (0.35s)
  quad9      FAILED: all DNS-over-HTTPS providers failed (last error: quad9: quad9: upstream returned HTTP 505) (0.32s)

  $ curl --http1.1 -X POST https://dns.quad9.net/dns-query   -> 505
  $ curl --http2   -X POST https://dns.quad9.net/dns-query   -> 400   (request reaches the application)
  ```
* **Expected behaviour:** every provider the project offers by default must be usable. Either the client negotiates HTTP/2 via ALPN, or the provider is not offered.
* **Minimal fix:** set `context.set_alpn_protocols(["h2", "http/1.1"])` and use an h2-capable transport, **or** remove `quad9` from `DEFAULT_PROVIDERS` and document the supported set. The smaller, lower-risk change is to drop `quad9` from the defaults and add a clear error message when a provider answers 505.
* **Regression test:** `tests/unit/test_doh.py` — a fake transport that returns HTTP 505 must produce an error naming the provider and the status; plus an assertion over `DEFAULT_PROVIDERS` that every bundled provider is reachable with the client's protocol (network-marked test) or an explicit exclusion list.

### D-02 — MEDIUM — `logging.level` is parsed and validated but never applied

* **File / symbol:** `src/dontneed_dns/core/logging_setup.py` → `setup_logging()`; caller `src/dontneed_dns/service_runner.py:133`
* **Current behaviour:** `setup_logging()` derives the level only from its own `verbose`/`quiet` keyword arguments and never reads `config.logging.level`. `service_runner` calls `setup_logging(config)` without those flags, so the effective level is always `INFO`.
* **Evidence:**
  ```
  config.logging.level = debug    -> logger.level after setup_logging = 20   (DEBUG = 10)
  config.logging.level = critical -> logger.level after setup_logging = 20   (ERROR = 40)
  ```
* **Expected behaviour:** the configured level is honoured; `debug`/`critical` in the configuration must change the verbosity of the running service.
* **Minimal fix:** map `config.logging.level` to the matching `logging` constant in `setup_logging()`, keeping `verbose`/`quiet` as overrides.
* **Regression test:** `tests/unit/test_logging_setup.py` — for every value in `VALID_LEVELS`, assert `logger.level` matches, and that `verbose`/`quiet` still win.

### D-03 — MEDIUM — The system-scope systemd unit has no `User=`, so it runs as root

* **File / symbol:** `src/dontneed_dns/platforms/linux/adapter.py` → `_UNIT_TEMPLATE`, `_SYSTEM_HARDENING`, `_render_unit()`
* **Current behaviour:** the rendered system-scope unit contains no `User=`/`Group=` directive, so the service runs with full root privileges, including the DNS server and the TLS-fragmenting proxy that open outbound sockets and read a configuration file.
* **Evidence:** rendered system unit (verified via `LinuxPlatformAdapter._render_unit(Component.DNS, "system")`):
  ```
  [Service]
  Type=simple
  ExecStart=... -m dontneed_dns.service_runner dns --config /etc/dontneed-dns/config.toml
  ...
  NoNewPrivileges=yes
  PrivateTmp=yes
  ProtectSystem=strict
  ProtectHome=yes
          -> contains 'User=' ?  False
  ```
  `docs/linux.md` states the system-scope service "runs as `nobody`" — that is not what the code produces.
* **Expected behaviour:** the system-scope service runs as a dedicated unprivileged account, matching the documentation and the principle of least privilege.
* **Minimal fix:** add `User=`/`Group=` (and the matching `ReadWritePaths=` for the state directory) to `_SYSTEM_HARDENING`, create the account in `install`, and align `docs/linux.md`.
* **Regression test:** `tests/linux/test_linux_adapter.py` — assert that the rendered system-scope unit contains a `User=` directive and that the user-scope unit does not.

### D-04 — MEDIUM — The local resolver forwards upstream answers that do not answer the question

* **File / symbol:** `src/dontneed_dns/core/resolver.py` → `SplitResolver.resolve_wire()`; `src/dontneed_dns/dns/server.py` → `DnsServer._build_response()`
* **Current behaviour:** the answer returned by the upstream provider is forwarded to the client after only the transaction ID is replaced (`response = data[:2] + response[2:]`). Neither the resolver nor the server checks that the answer's question section matches the request's question section.
* **Evidence:** a fake DoH transport that answers `evil.example` for a `discord.com` query produced this at the client socket:
  ```
  client asked 'discord.com', server answered question = evil.example rcode = 0
  answer addresses = ['203.0.113.9']
  ```
* **Expected behaviour:** an answer whose question does not match the request is discarded and reported as a failure (`SERVFAIL`), so that a malfunctioning or hostile upstream cannot substitute an unrelated answer under the client's transaction ID.
* **Minimal fix:** in `SplitResolver.resolve_wire()`, parse the response and compare `question.name` (case-insensitively) and `question.qtype` with the request; treat a mismatch as a failed resolution.
* **Regression test:** `tests/unit/test_resolver.py` — a transport returning an answer for a different name must yield `ResolutionResult(ok=False)`; `tests/integration/test_dns_server.py` — the client must receive `SERVFAIL`.

### D-05 — MEDIUM — Listener addresses are not restricted to the loopback interface

* **File / symbol:** `src/dontneed_dns/core/config.py` → `_validate_host()`, `validate_config()`; consumers `src/dontneed_dns/dns/server.py`, `src/dontneed_dns/proxy/server.py`
* **Current behaviour:** `dns.listen_host` and `proxy.listen_host` accept any syntactically valid address, including `0.0.0.0` and a LAN address. The SOCKS5/CONNECT proxy has no authentication, and the DNS server has no rate limiting, so such a configuration turns the host into an open proxy and an open resolver reachable from the network.
* **Evidence:**
  ```
  listen_host=0.0.0.0        ACCEPTED
  listen_host=192.168.1.10   ACCEPTED
  listen_host=::             ACCEPTED
  ```
  The README and `docs/security.md` describe the listeners as loopback-only.
* **Expected behaviour:** a non-loopback listener address is either rejected or requires an explicit, documented opt-in.
* **Minimal fix:** extend `validate_config()` to reject non-loopback addresses, or to accept them only when a new explicit option (for example `service.expose_lan = true`) is set.
* **Regression test:** `tests/unit/test_config.py` — a configuration with `listen_host = "0.0.0.0"` must be rejected (or accepted only with the opt-in set).

### D-06 — LOW — The proxy listener is IPv4-only and fails with a misleading error

* **File / symbol:** `src/dontneed_dns/proxy/server.py` → `ProxyServer.bind()`
* **Current behaviour:** the listening socket is hardcoded to `socket.AF_INET`, so `proxy.listen_host = "::1"` cannot work. The DNS server, in contrast, opens one socket per address family (`dns/server.py::_bind_family`).
* **Evidence:**
  ```
  IPv6 bind FAILED: gaierror [Errno -9] Address family for hostname not supported
  ```
  The error names a DNS failure although the cause is the address family.
* **Expected behaviour:** either bind per address family like the DNS server does, or refuse an IPv6 host with an explicit message.
* **Minimal fix:** select the family from the host (`":" in host`) as `dns/server.py` already does, and raise a clear `OSError` for an unsupported family.
* **Regression test:** `tests/integration/test_proxy_server.py` — binding `::1` succeeds and a SOCKS5 handshake over IPv6 is answered.

### D-07 — LOW — SOCKS5 UDP ASSOCIATE and BIND are unsupported and this is not documented

* **File / symbol:** `src/dontneed_dns/proxy/socks5.py` → `read_request()`
* **Current behaviour:** only `CMD_CONNECT` (0x01) is accepted; `BIND` (0x02) and `UDP ASSOCIATE` (0x03) are refused with SOCKS5 reply code 7 ("command not supported"). This is correct RFC 1928 behaviour for a CONNECT-only proxy, but the capability matrix and README advertise a plain "SOCKS5 proxy: Supported".
* **Evidence (live probe against a running proxy):**
  ```
  cmd 0x01 (CONNECT      ) -> greeting=0500 reply code=0
  cmd 0x02 (BIND         ) -> greeting=0500 reply code=7
  cmd 0x03 (UDP ASSOCIATE) -> greeting=0500 reply code=7
  ```
* **Expected behaviour:** the limitation is stated, because Discord/Roblox voice and chat traffic can require UDP through a proxy.
* **Minimal fix:** document the limitation in `README.md`, `docs/linux.md` and the `PROXY_SOCKS5` entry of `LINUX_MATRIX`/`WINDOWS_MATRIX`; no code change required.
* **Regression test:** `tests/unit/test_socks5.py` already covers the refusal; add a documentation-consistency check in `tests/unit/test_capability.py` if the note is added to the matrix.

### D-08 — LOW — No DNS cache and the upstream TTL is forwarded unmodified

* **File / symbol:** `src/dontneed_dns/core/resolver.py`, `src/dontneed_dns/core/doh.py`
* **Current behaviour:** every query is sent upstream; there is no cache and no TTL accounting.
  ```
  dontneed_dns.core.resolver: cache-related names -> none
  dontneed_dns.core.doh:      cache-related names -> none
  answer TTL as delivered by the local server: [300]   (upstream value, not decremented)
  ```
* **Expected behaviour:** either a documented absence of caching, or a cache that respects and decrements the TTL. A non-caching forwarder that forwards the original TTL causes downstream caches to hold the record longer than the upstream intended.
* **Minimal fix (documentation first):** state the absence of a cache in `docs/architecture.md`; if a cache is added later, decrement the TTL by the age of the cached entry.
* **Regression test:** none required for the documentation change; a cache would need TTL-expiry and decrement tests.

### D-09 — LOW — The Linux installer does not verify the integrity of the bundled wheelhouse

* **File / symbol:** `packaging/linux/install.sh` (line 43), `packaging/linux/uninstall.sh`
* **Current behaviour:** the installer performs `pip install --no-index --find-links "$HERE/wheels" dontneed-dns` without hash verification, even though the release publishes `SHA256SUMS` for every file.
  ```
  install.sh mentions --no-index       : True
  install.sh mentions --find-links     : True
  install.sh mentions --require-hashes : False
  install.sh mentions sha256 / SHA256SUMS : False
  ```
* **Expected behaviour:** a tampered archive is rejected before anything is installed.
* **Minimal fix:** verify `SHA256SUMS` with `sha256sum -c` at the start of `install.sh` and pass `--require-hashes` to pip.
* **Regression test:** `tests/unit/test_packaging.py` — assert that `install.sh` contains a checksum verification step.

### D-10 — LOW — `service.autostart` is a dead configuration key

* **File / symbol:** `src/dontneed_dns/core/config.py` → `ServiceConfig.autostart` (line 99), `validate_config()` (line 279)
* **Current behaviour:** the option is defined, validated and listed in `examples/config.example.toml`, but no code reads it. Automatic start is controlled exclusively by `systemctl --user enable`, i.e. by the `enable`/`disable` commands.
* **Evidence:** `grep -rn autostart src/` returns only the definition, the validation, the capability enum member, and two unrelated local variables in `cli/main.py`/`tui/app.py` that report the *actual* unit state.
* **Expected behaviour:** the key either enables the unit during `install`, or it is removed from the configuration schema and the example file.
* **Minimal fix:** honour it in `LinuxPlatformAdapter.install()` (enable when true, disable when false), or delete the key.
* **Regression test:** `tests/unit/test_config.py` (round-trip) plus `tests/linux/test_linux_adapter.py` (install with `autostart = true` enables the unit).

### D-11 — LOW — The capability matrix advertises Linux features that do not exist

* **File / symbol:** `src/dontneed_dns/core/capability.py` → `LINUX_MATRIX`, entries `DNS_INTERCEPTION` and `NETWORK_CONFIGURATION`
* **Current behaviour:** both are classified `SUPPORTED_WITH_LIMITATIONS` with the notes "Requires an explicit opt-in; scoped to a single interface or user." and "systemd-resolved split DNS or an owner matched firewall rule; always reversible." No implementation exists anywhere in the tree.
* **Evidence:**
  ```
  $ grep -rniE "nftables|firewalld|systemd-resolved|split_dns|rp_filter" src/ --include='*.py'
  src/dontneed_dns/core/capability.py:149:  "systemd-resolved split DNS or an owner matched firewall rule; always reversible.",
  ```
  (single hit — the capability note itself). `dontneed-dns doctor` prints this matrix to the user as "Supported with limitations".
* **Expected behaviour:** the matrix reflects reality. The project's own rule (CONTRIBUTING.md) says unsupported features must be marked `UNSUPPORTED`, never emulated.
* **Minimal fix:** downgrade both entries to `UNSUPPORTED` with an explanatory note until the feature exists, and update the README feature matrix accordingly.
* **Regression test:** `tests/unit/test_capability.py` — assert that no matrix entry claims a feature for which no implementation module exists (a curated allow-list).

### D-12 — LOW — Configuration and log files are created without restrictive permissions

* **File / symbol:** `src/dontneed_dns/core/config.py` → `save_config()`; `src/dontneed_dns/core/logging_setup.py` → `setup_logging()` (rotating file handler); `src/dontneed_dns/core/paths.py`
* **Current behaviour:** neither the configuration file nor the log file is created with an explicit mode; the process umask decides. The system-scope configuration is the only file that receives an explicit `chmod(0o644)`.
* **Evidence (live host):**
  ```
  ~/.config/dontneed-dns/config.toml            -rw-rw-rw- 666
  ~/.local/state/dontneed-dns/logs/dontneed-dns.log  -rw-r--r-- 644
  ~/.local/state/dontneed-dns                   drwxr-xr-x 755
  ```
  The 0666 mode of the configuration came from the manual edit; the project itself never tightens or sets the mode.
* **Expected behaviour:** a configuration that controls proxy routing is not writable by other local users; the log that contains the queried domains is not world-readable.
* **Minimal fix:** `chmod 0600` the configuration and `chmod 0640` the log file after creation, and create the directories with mode 0700/0750.
* **Regression test:** `tests/unit/test_config.py` — assert the mode of a configuration written by `save_config()`; `tests/unit/test_logging_setup.py` — assert the mode of the created log file.

### D-13 — INFO — Dead code in the portable core

* **File / symbol:** `src/dontneed_dns/core/dnsmessage.py` (`has_opt_record`, `DNS_TYPE_CNAME`, `RCODE_NXDOMAIN`, `RCODE_REFUSED`), `src/dontneed_dns/core/constants.py` (`MAX_REQUEST_SIZE`, `DEFAULT_LISTEN_BACKLOG`, `DEFAULT_CONNECT_TIMEOUT`), `src/dontneed_dns/core/errors.py` (`PermissionDeniedError`, `ServiceError`, `NetworkConfigError`, `ProxyError`), `src/dontneed_dns/core/config.py` (`describe_options`), `src/dontneed_dns/platforms/windows/paths.py` (`python_executable`)
* **Evidence:** each symbol has exactly one occurrence in `src/` (its definition).
* **Expected behaviour:** unused API surface is removed or the intent is documented.
* **Minimal fix:** delete the unused symbols, or wire `has_opt_record` into the EDNS size computation (see D-15).
* **Regression test:** none; `ruff` does not flag unused module-level definitions.

### D-14 — INFO — The version is declared twice and the release tag is missing

* **File / symbol:** `pyproject.toml` (`version = "0.1.2"`) and `src/dontneed_dns/__init__.py` (`__version__ = "0.1.2"`); `scripts/build_release.py` reads the pyproject copy only
* **Current behaviour:** the two copies can drift; the repository has tags `v0.1.0`, `v0.1.1` but the working tree is at `0.1.2`, which has never been tagged or released.
* **Minimal fix:** derive `__version__` from the installed package metadata (`importlib.metadata.version`), or add a test asserting the two values are equal.
* **Regression test:** `tests/unit/test_cli.py` — assert `dontneed_dns.__version__` equals the `pyproject.toml` version.

### D-15 — INFO — The EDNS payload size is a heuristic

* **File / symbol:** `src/dontneed_dns/core/dnsmessage.py` → `ParsedMessage.edns_udp_size` (line 113), `has_opt_record` (unused)
* **Current behaviour:** `edns_udp_size` returns 4096 whenever the additional section is non-empty, instead of parsing the OPT record's advertised payload size. `dns/server.py` uses it to decide when to truncate a response.
* **Expected behaviour:** the client's advertised UDP size is honoured, or the constant is documented as an approximation.
* **Minimal fix:** parse the OPT record (the helper `has_opt_record` already walks that section) and return the advertised size, falling back to 4096.
* **Regression test:** `tests/unit/test_dnsmessage.py` — an OPT record with a 1232-byte payload must yield 1232.

### D-16 — INFO — The fragmentation health check can never fail

* **File / symbol:** `src/dontneed_dns/core/health.py` (line 245, `HealthCheck("TLS fragmentation", True, ...)`)
* **Current behaviour:** the check always reports `ok`, regardless of the configuration; only the wording changes.
* **Minimal fix:** keep it as an informational entry but move it to `Severity.INFO` semantics in the output, or report it as a warning when fragmentation is enabled on a platform where it is not supported.
* **Regression test:** `tests/unit/test_health.py` — assert the severity of that entry.

---

## 4. Potential risks (not confirmed defects)

### R-01 — Reading `/etc/resolv.conf` is sufficient on this Fedora host, with one failure mode

* **Assessment:** on Fedora 45 the file is a symlink to `/run/systemd/resolve/stub-resolv.conf` and contains exactly one entry, `nameserver 127.0.0.53`. `read_resolv_conf_nameservers()` returns `['127.0.0.53']`, `create_system_resolver()` therefore selects `UdpSystemResolver`, and the stub answers **all record types** over **both UDP and TCP**:
  ```
  UDP qtype 15  gmail.com    rcode=0 ancount=5
  UDP qtype 16  google.com   rcode=0 ancount=17
  UDP qtype  2  google.com   rcode=0 ancount=4
  UDP qtype  1  example.com  rcode=0 ancount=2
  TCP stub answer rcode=0 ancount=1
  ```
  The live health check confirms it: `System resolver  ok  nameservers: 127.0.0.53`.
* **Risk:** the fallback depends on the systemd-resolved stub being alive. The stub file lives under `/run/systemd/resolve/`, so if `systemd-resolved` is stopped or masked while `/etc/resolv.conf` keeps pointing at it, the system fallback fails silently — the resolver would answer `SERVFAIL` for every non-split domain. There is no fallback to `resolvectl` output or to the NetworkManager data.
* **Risk:** a host without `systemd-resolved` (NetworkManager writing a static `/etc/resolv.conf`) still works, because the real nameserver address is then listed directly.
* **Risk:** if `/etc/resolv.conf` contains no `nameserver` line at all, `create_system_resolver()` falls back to `GetAddrInfoResolver`, which synthesises **A and AAAA only** — the Windows limitation would silently apply to Linux.
* **Recommendation for a future step:** resolve the upstream nameservers through the resolved D-Bus API (or `resolvectl`) before falling back to the stub, and detect the "stub present but dead" case.

### R-02 — SELinux is enforcing and the project ships no policy

* **Assessment:** the running services worked under SELinux Enforcing because the listeners use unprivileged ports (5354, 10808) and no confinement is requested. But there is no `file_contexts`, no policy module and no `.te`/`.fc` file in the repository, and `systemd --user` units are not confined by the shipped policy either.
* **Impact:** for a "production-ready" Fedora edition this means no confinement of the DNS/proxy processes beyond the systemd unit hardening (`NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=strict`, `ProtectHome=read-only`). Whether the project should ship an SELinux policy is a decision for a later step.

### R-03 — firewalld owns the packet filter; the project owns nothing

* **Assessment:** `firewalld` is active and running, `nftables.service` is inactive, and `nft list ruleset` requires root. The project currently creates no firewall rules at all, so there is nothing to clean up — but the capability matrix claims a "owner matched firewall rule" (see D-11). Any future transparent-routing work must integrate with firewalld's ruleset rather than writing raw nftables rules, or the rules will be lost on `firewall-cmd --reload` and on firewalld restarts. This is the "firewall ownership and cleanup" assumption the task asked about: **currently there is no ownership and no cleanup obligation**.

### R-04 — The proxy is an unauthenticated forwarder to arbitrary destinations

* **Assessment:** by design the proxy accepts CONNECT to any host and port, including RFC1918 addresses and link-local ones, and the DNS server has no rate limiting. Both are reachable only from loopback by default (unless D-05 is exploited).
* **Impact:** any local process can use it as a relay into the local network. This is the intended design of a local proxy but should be documented explicitly.

### R-05 — The proxy can still leak a plain-text DNS lookup

* **Assessment:** `ProxyServer._resolve_target()` falls back to `socket.getaddrinfo()` and finally to letting the operating system resolve the name when `allow_system_dns_fallback` is true (the default). When the DoH path fails, the very lookup the project exists to prevent happens anyway, silently.
* **Impact:** relevant for the Discord/Roblox goal, where a plain-text lookup is what triggers blocking in the first place. The failure is only visible in the log, not in the client's behaviour.

### R-06 — No TTL handling and no EDNS cookie / no 0x20 query randomisation

* **Assessment:** the resolver forwards the client's message verbatim; there is no EDNS cookie, no 0x20 case randomisation and no query-ID randomisation beyond the client's own. Together with the absence of a cache (D-08), every lookup is a fresh upstream query.
* **Impact:** more observable DNS traffic than necessary; the upstream provider sees the full query stream.

### R-07 — The service fails hard if the configuration file disappears

* **Assessment:** the unit runs `… service_runner dns --config <path>`; `service_runner.main()` returns exit code 2 on a configuration error, and the unit has `Restart=on-failure` with `RestartSec=5`. A deleted or moved configuration therefore produces a restart loop every five seconds, logging into the journal and the log file.
* **Impact:** noisy failure mode; a `RestartSec` of 5 s with no start-rate limiting (`StartLimitBurst`) is unfriendly.

### R-08 — The health check for DNS-over-HTTPS depends on the first split domain

* **Assessment:** `_doh_probe()` resolves `config.dns.split_domains[0]`. With the live configuration that is `discord.com`, which is answered over DoH correctly here. If the first split domain were blocked by the network itself, the check would fail even though DoH works.

### R-09 — The release pipeline ships no Fedora artifact

* **Assessment:** `build_release.py` supports `linux` and `windows` only; the Linux archive contains sources plus a wheelhouse and no binary. There is no `.spec` file, no RPM, no COPR. CI runs on `ubuntu-latest` and `windows-latest` only, so the Fedora path is exercised by nobody in automation, even though the README lists Fedora as "Verified".

---

## 5. Verified working behaviour (no defect)

| Area | Result |
| --- | --- |
| DNS server, UDP on IPv4 | answer returned, `rcode=0`, `ancount=5` for `discord.com` |
| DNS server, UDP on IPv6 | answer returned over `::1`, same port as IPv4 |
| DNS server, TCP | answer returned, same port as UDP |
| DNS server, dual family | `bound ports: [('127.0.0.1', 44787), ('::1', 44787)]` — one socket per family, one shared port |
| DNS server, busy port | clean refusal: `OSError: the port 44787 on 127.0.0.1 is already in use; another instance of the resolver is probably running` |
| DNS server, shutdown | port released immediately; the socket stops answering (`TimeoutError` afterwards) |
| DoH, TLS | `minimum_version=771` (TLS 1.2), `verify_mode=2` (`CERT_REQUIRED`), `check_hostname=True`; the client connects by provider IP but verifies the provider hostname; there is no switch to disable verification |
| DoH, retry bound | with all providers failing, the call returns within the `timeout*(retries+1)` budget; `max_attempts=6` for 3 providers and `retries=1` |
| DoH, provider fallback | cloudflare and google answer from this host; the resolver cycles providers |
| Split routing decision | `cdn.discord.com → True`, `mydiscord.com → False`, `example.org → False` (label-aware matching) |
| Split routing end to end | `discord.com → source=doh (0.41s)`, `example.org → source=system (0.04s)` |
| System resolver fallback | works through the resolved stub for A, AAAA, MX, TXT, NS over UDP and TCP |
| SOCKS5 CONNECT | relay confirmed against a local echo target: reply code 0, payload echoed |
| SOCKS5 handshake | `0500` (version 5, no authentication) |
| SOCKS5 BIND / UDP ASSOCIATE | refused with reply code 7 (see D-07) |
| HTTP CONNECT | `HTTP/1.1 200 Connection Established`, payload relayed |
| Proxy name resolution | the proxy asked the `SplitResolver` for `discord.com` itself — the client never resolves the name |
| Proxy connect timeout | reply code 4 (host unreachable) after exactly 1.00 s with `connect_timeout=1.0` |
| TLS fragmentation on the wire | target received 7 chunks — `(0.0, 5)`, `(0.05, 15)`, `(0.06, 16)`, `(0.07, 16)`, `(0.081, 16)`, `(0.091, 16)`, `(0.101, 1)` — i.e. 5-byte header, 15-byte preamble after 50 ms, then 16-byte chunks every 10 ms |
| Linux adapter | `is_available=True`, `describe()` → "Fedora Linux 45 … (fedora), systemd", both units detected as `enabled`, `status=stopped` |
| Unit rendering, user scope | hardening present: `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=strict`, `ProtectHome=read-only`, `ReadWritePaths=<state dir>`, `WantedBy=default.target` |
| Boot persistence | `~/.config/systemd/user/default.target.wants/` contains both unit symlinks |
| Logs | `journalctl --user -u dontneed-dns-dns -n 3` returns the service lines; the log file mirrors them |
| PID handling | PID file `~/.local/state/dontneed-dns/dontneed-dns-dns.pid` is written on start and removed on exit; `running_pid()` validates it with `process_alive()` |
| Clean shutdown | SIGTERM → "received signal 15, shutting down", exit status 0/SUCCESS, port released |
| CLI | `status`, `config validate`, `check`, `doctor -o file` all work; `check` reports "discord.com was answered over DNS-over-HTTPS" |
| Subprocess handling | no `shell=True` anywhere in `src/`; `run_command()` always passes an argument list, always applies a timeout and always captures the output |
| Redaction | `doctor` output is redacted at the value level (home directory, user, host, tokens, globally routable addresses) |
| Static analysis | `ruff format --check`, `ruff check` and `mypy` all clean |

---

## 6. Not tested

| Item | Reason |
| --- | --- |
| Windows edition (scheduled task, SCM, PowerShell installers) | no Windows host; the PowerShell tests were skipped |
| `install` / `uninstall` / `start` / `stop` / `restart` / `enable` / `disable` mutations | forbidden by the task rules; verified by code reading, by the existing unit tests and by the already-installed state on this host |
| PyInstaller build, `build_release.py`, `verify_release.py`, GitHub Actions | not executed; the release pipeline is out of scope for a Fedora runtime change |
| RPM / COPR packaging | does not exist in the repository |
| Bypass of a specific DPI/middlebox by the fragmentation | not verifiable from inside the project (already stated in the README) |
| IPv6 connectivity towards the internet | only loopback IPv6 was exercised; the host has no IPv6 default route |
| Per-application DNS interception | not implemented (see D-11) |
| Behaviour of the resolver when `systemd-resolved` is stopped | would require changing system services, which the task forbids; assessed analytically in R-01 |

---

## 7. Summary

| Severity | Count | IDs |
| --- | --- | --- |
| CRITICAL | 0 | — |
| HIGH | 0 | — |
| MEDIUM | 5 | D-01, D-02, D-03, D-04, D-05 |
| LOW | 7 | D-06 … D-12 |
| INFO | 4 | D-13 … D-16 |
| Risk items | 9 | R-01 … R-09 |

The protocol core (DNS message handling, DoH client, split routing, SOCKS5, HTTP CONNECT, fragmentation) is sound and behaves as documented on Fedora 45. The defects are concentrated in the default provider list, the configuration handling, the answer validation, the system-scope privilege model, and in the gap between the advertised capability matrix and the implemented feature set.

No fixes were applied. Awaiting approval before any implementation work.
