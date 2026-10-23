# Security and privacy

## What this project does

dontneed-dns sends the DNS queries of the configured domains over
DNS-over-HTTPS and forwards the resulting connections through a local proxy.
It is a narrow tool for a narrow problem.

## What this project does not do

* It does not collect telemetry.  There is no analytics, no crash reporter
  and no phone home.  The only network connections are the ones you ask
  for: DNS-over-HTTPS to the configured providers and the connections of
  the proxy itself.
* It does not collect credentials.  No password, token or certificate is
  ever read or stored.
* It does not install kernel drivers, rootkits or unsigned binaries.
* It does not modify the system wide DNS servers, the firewall, the routing
  table or the network adapters without an explicit confirmation and a
  reliable rollback.

## The permissions model

| Operation | Linux | Windows |
| --- | --- | --- |
| Status, logs, configuration validation | no privileges | no privileges |
| Starting and stopping the components | no privileges (systemd user units) | no privileges (scheduled task) |
| Automatic startup | no privileges | no privileges |
| Installing into the user scope | no privileges | no privileges |
| Installing into the system scope | root | administrator |
| Reading the journal | no privileges for user units | not applicable |

No command of this project asks for a password.  The elevation is requested
by the operating system when an operation that genuinely needs it is
started, and only for that operation.

## TLS

Certificate verification is always enabled.  `ssl.create_default_context()`
is used, which requires a valid certificate and verifies the hostname.  The
DNS-over-HTTPS client connects to the provider by IP address to avoid a
bootstrap dependency, but it still verifies the certificate against the
provider hostname through `server_hostname`.  There is no switch that turns
the verification off.

## Logging

The log is deliberately thin:

* the domain that was queried and the result are logged,
* the address of the client is **not** logged,
* no request identifier, no payload and no header is logged,
* every message passes through the redactor when `logging.redact` is
  enabled, which removes the home directory, the user name, the host name,
  mail addresses, tokens and globally routable addresses.

`dontneed-dns doctor` produces a report in which the same redaction has
been applied, so it can be pasted into an issue without thinking about it
first.

## Third party code and licences

| Component | Licence | Usage |
| --- | --- | --- |
| typer | MIT | Command line interface |
| rich | MIT | Terminal formatting |
| textual | MIT | Terminal user interface |
| pytest | MIT | Test suite (development only) |
| ruff | MIT | Linting and formatting (development only) |
| pyinstaller | GPL with a bootloader exception | Building the Windows executable (development only) |

The licences of the bundled wheels are checked by the packaging script
before an archive is published.  The project itself is MIT licensed; the
original code in this repository is the only code under that licence.

The Windows executable is **not digitally signed**.  Windows may show a
warning on the first start ("Windows protected your PC").  This is expected
and is not a defect of the archive.

## Reporting a vulnerability

Please open a private security advisory on the repository rather than a
public issue.  Include the version, the operating system and a description
of the problem.  See `SECURITY.md` at the root of the repository for the
current support window.
