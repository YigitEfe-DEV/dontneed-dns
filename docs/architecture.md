# Architecture

This document describes how dontneed-dns is built and why the boundaries
between the parts are where they are.

## Layers

```
                     ┌──────────────────────────────────────┐
                     │            cli / tui                 │
                     │   (shared command surface, typer +   │
                     │            textual)                  │
                     └──────────────────┬───────────────────┘
                                        │  PlatformAdapter
                     ┌──────────────────▼───────────────────┐
                     │        platforms.linux  /  .windows  │
                     │  systemd units / scheduled tasks,    │
                     │  installation, privileges, logs      │
                     └──────────────────────────────────────┘

                     ┌──────────────────────────────────────┐
                     │                 core                  │
                     │  config, DNS messages, DoH client,   │
                     │  split resolver, redaction, health   │
                     └──────────────────┬───────────────────┘
                                        │
                     ┌──────────────────▼───────────────────┐
                     │      dns.server  /  proxy.server      │
                     │  UDP+TCP DNS, SOCKS5, HTTP CONNECT   │
                     └──────────────────────────────────────┘
```

Shared code never calls `systemctl`, `schtasks` or `sc.exe`.  It asks a
`PlatformAdapter` for what it needs, and every adapter returns a structured
`OperationResult` instead of raising when an operation is impossible on the
current platform.  That is what makes the "a shared command must never run
a platform incompatible administration command" rule enforceable rather than
aspirational.

## The modules

| Module | Responsibility |
| --- | --- |
| `core/dnsmessage.py` | Strict DNS wire format: header, question, compression pointers, error responses |
| `core/doh.py` | RFC 8484 client, provider failover, bounded runtime, strict TLS |
| `core/upstream.py` | Forwarding to the operating system resolver |
| `core/resolver.py` | Split routing between DNS-over-HTTPS and the system resolver |
| `core/config.py` | The configuration model, its validation and its serialisation |
| `core/redact.py` | Removal of machine identifying data from diagnostic output |
| `core/capability.py` | The platform capability matrix |
| `core/health.py` | The read only health checks |
| `dns/server.py` | The local DNS server (UDP and TCP) |
| `proxy/socks5.py` | The SOCKS5 protocol |
| `proxy/http_connect.py` | The HTTP CONNECT protocol |
| `proxy/fragmentation.py` | The experimental TLS ClientHello fragmenter |
| `proxy/server.py` | The proxy server and the relay |
| `platforms/linux/` | systemd, distribution detection, installation |
| `platforms/windows/` | Task Scheduler (XML definitions), Windows services, paths |

## Design decisions

### DNS messages are parsed, not pattern matched

The prototype that this project replaces detected Discord queries by looking
for the byte string `b"discord"` anywhere in the raw packet.  That produced
false positives (a domain such as `notdiscord.example`, or the word inside an
answer) and forwarded malformed packets to the upstream.  The resolver now
parses the header and the question section and compares the *domain name*,
label by label, so `discord.com` matches `cdn.discord.com` but never
`mydiscord.com`.

### The DoH client connects by address and verifies by name

The client connects to the provider by IP address so that it never depends
on the resolver it is meant to replace; a local DNS server pointing at
itself would otherwise deadlock.  The TLS certificate is still verified
against the provider *hostname* through `server_hostname`, so using an IP
literal as the connect target does not weaken the verification.  Certificate
verification cannot be switched off.

### The total runtime of a query is bounded

`DohClient` gives every attempt the configured timeout but stops at
`timeout * (retries + 1)` in total, whatever the number of providers is.
A DNS server that can block for half a minute is worse than one that
answers with `SERVFAIL`.

### The proxy resolves names itself

The proxy resolves the requested host through the split resolver instead of
handing the name to the operating system.  A Discord domain is therefore
never looked up through the plain text resolver that may be the reason the
connection is being made at all.  The system resolver is only used when
DNS-over-HTTPS is disabled or unavailable, and the configuration makes that
fallback explicit.

### Fragmentation is opt-in and experimental

Splitting the first TLS record changes only the packet boundaries, never the
encrypted payload.  Whether a particular middlebox is confused by it cannot
be verified from inside this project, so the feature is disabled by default,
labelled experimental in the capability matrix and documented as such.

## The data flow

```
Discord (Electron)
   │
   │  HTTPS through the local proxy
   ▼
127.0.0.1:10808  ── SOCKS5 / HTTP CONNECT ──►  proxy resolves the name
   │                                                over DNS-over-HTTPS
   │                                                        │
   │                                                        ▼
   │                                              Cloudflare / Google / Quad9
   ▼
relay with an optional TLS ClientHello fragmentation
```

On Linux the same components can additionally be reached through the local
DNS resolver on `127.0.0.1:5354`, which answers the configured domains over
DNS-over-HTTPS and forwards everything else to the system resolver.

## Concurrency

Both servers use a bounded thread pool.  The DNS server limits the number of
requests that are processed at the same time and answers with `SERVFAIL`
when the limit is reached; the proxy refuses new connections above
`proxy.max_connections`.  Both servers shut down cleanly: the sockets are
closed, the pending work is cancelled and the listener ports are released.

## Testing strategy

* `tests/unit` - pure logic: DNS messages, the DoH client, the resolver, the
  configuration, the protocols, the redaction, the packaging.
* `tests/integration` - real sockets: a DNS query over UDP and TCP, a
  SOCKS5 and an HTTP CONNECT relay, the terminal interface.
* `tests/linux` - the systemd adapter with the external commands mocked.
* `tests/windows` - the Task Scheduler backend with the external commands
  mocked; the same tests run on the Windows runner of the workflow.

The Windows tests are executed on a real Windows runner in GitHub Actions.
A mocked test never counts as proof that a feature works on Windows.  Two
jobs of the workflow go beyond the unit tests: one parses the PowerShell
scripts with the PowerShell parser, the other installs the project on the
runner, checks that the scheduled tasks exist and removes them again.

The scripts of the Windows edition are additionally executed with PowerShell
on every platform that has it, against a stand in for the executable, so
that the error handling of the installer is checked as well.
