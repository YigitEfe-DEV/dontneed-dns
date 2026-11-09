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
first.  The redaction is applied to the values of the report and not to its
serialised form: the JSON encoding escapes the backslashes of a Windows
path, so a home directory of `C:\Users\somebody` would no longer be
recognised as such and would survive into the report.

## The local listeners

The resolver on `127.0.0.1:5354` and the proxy on `127.0.0.1:10808` are
listeners of the current user.  They are not authenticated: every program
of the current user can send a query to the resolver and can use the
proxy.  That is the same situation as with every other local proxy and it
is what makes the tool usable without administrator rights.

The ports are checked before the components start, and a program that
already holds them is reported as a conflict instead of being silently
replaced.  On Windows versions before 1607 two sockets could share an
address, so on those systems another program of the same user could take
the port over after the start; Windows 10 version 1607 and every Windows 11
do not allow it.  The resolver binds the loopback interface only, never a
routable address.
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
