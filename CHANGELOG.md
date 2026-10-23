# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.2] - 2026-10-23

Fixes the two problems of the installation report of a Windows machine and
makes the state of the installation consistent on both platforms.

### Fixed

- The IPv4 TCP listener of the DNS resolver was closed as soon as the IPv6
  listener was opened.  A single socket cannot serve both families: the
  second one replaced the first, which was then closed and released its
  port, so a TCP query to `127.0.0.1:5354` was refused while the UDP
  listener worked.  Every address family has its own socket and its own
  accept loop now.
- The scheduled tasks of the Windows edition are created from an XML
  definition instead of from the command line form of `schtasks`.  The
  command line form strips the outer quotes of the `/TR` value, which
  breaks the command as soon as the path of the executable or one of its
  arguments contains a space.  The XML definition also uses a logon trigger
  for the current user with the `InteractiveToken` logon type, which the
  Task Scheduler accepts without administrator rights, while the "at log
  on" trigger of the command line form is rejected with "Access is
  denied" in a terminal that is not elevated.
- A scheduled task is read back after it was created.  A task that exists
  but runs another command or belongs to another account is reported as a
  failure instead of a successful installation.
- The complete output of a failed command is reported.  Only the last line
  of the error message was kept, which is often not the part that explains
  what went wrong.
- The temporary file of the task definition is closed before it is deleted.
  Windows refuses to delete a file that is still open, so the file was left
  behind and its descriptor leaked.
- The source installation of the Windows edition works again.  The task
  starts in the directory of the package, which `python -m` puts at the
  beginning of `sys.path`, so the package is found even when it is not
  installed in the interpreter.  The previous attempt used the shell
  operator `&&`, which a scheduled task does not interpret at all, so the
  task never started.

### Added

- The state "Running (not registered)" for a component whose process runs
  but is not registered to start automatically.  The status output explains
  that the component will not survive a reboot and how to register it.
- The uninstall stops a component whose process runs without a
  registration, because it belongs to this project and would keep the
  port.
- An incomplete installation is reported as such, with the hint to run the
  installation again.  The configuration of the user is kept.
- The installation script of Windows writes the output of every step to
  `install-output.txt` and reads the tasks back after the installation.
- A workflow that installs the project on a Windows runner, checks that the
  scheduled tasks exist and removes them again.

### Changed

- The health check describes the resolver of the running platform instead
  of always reading `/etc/resolv.conf`.

## [0.1.1] - 2026-10-23

### Fixed

- The listening port is released immediately when a component stops.  A
  thread that was blocked in `accept()` kept the socket alive in the
  kernel, so the port stayed bound until the next connection arrived.
- The DNS listener opens the TCP socket first, so the operating system
  chooses a port that is valid for both protocols.  Windows reserves parts
  of the dynamic range for TCP and a reserved port cannot be bound at all.
- The IPv6 listener uses the port of the IPv4 listener instead of picking
  a second one.
- A busy port is reported instead of being taken over silently.  Windows
  allows two sockets to bind the same address because `SO_REUSEADDR` is not
  as restrictive there as on Linux.
- The systemd unit directory follows `XDG_CONFIG_HOME`, which is where
  systemd reads the user units from.
- The installation creates the state directory, without which the hardened
  unit fails to start with `226/NAMESPACE`.
- An empty `dns.listen_host_v6` disables the IPv6 listener instead of
  being rejected.

## [0.1.0] - 2026-10-23

The first release.  It replaces an installation that grew from a single
script into a project with two platform editions.

### Added

- A portable DNS-over-HTTPS core: a strict DNS message parser, an RFC 8484
  client with provider failover and a bounded runtime, and a split resolver
  that routes the configured domains over DoH and forwards everything else
  to the system resolver.
- A local DNS server on `127.0.0.1:5354` speaking UDP and TCP, with a
  bounded concurrency, proper error responses and a clean shutdown.
- A SOCKS5 and HTTP CONNECT proxy on `127.0.0.1:10808` that resolves the
  requested host itself over DNS-over-HTTPS, with correct handling of
  fragmented requests, connection timeouts, an idle timeout and a
  concurrency limit.
- An experimental, opt-in TLS ClientHello fragmentation.
- The Linux edition: systemd user and system units, distribution detection,
  installation, uninstallation and the journal.
- The Windows edition: scheduled tasks for the user scope, an optional
  Windows service for the system scope, and a log file.
- A command line interface with the same commands on both platforms, and a
  terminal interface built with Textual.
- A capability matrix that classifies every feature per platform.
- A redacted diagnostic report (`dontneed-dns doctor`).
- Separate release archives for Linux and Windows with SHA-256 checksums,
  and a GitHub Actions workflow that tests and builds both.

### Fixed

Relative to the prototype that this project replaces:

- The Discord query detection no longer matches a byte string anywhere in
  the packet.  The domain name is parsed and compared label by label, so
  `mydiscord.com` is no longer treated as a Discord domain.
- The SOCKS5 request is no longer read with a single `recv` call, which
  truncated it whenever the client sent it in more than one segment.
- The proxy no longer keeps a five second socket timeout on the relayed
  connection, which closed idle connections.
- The proxy no longer falls back to the system resolver for a domain it
  could not resolve over DoH without saying so.
- The DNS server no longer starts an unbounded thread per packet.
- The system resolver is read from `/etc/resolv.conf` instead of being
  hard coded to `127.0.0.53`, which does not exist on every distribution.
- The installer no longer appends the same firewall rule four times.
