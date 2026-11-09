# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Repairs the Windows edition.  The Windows job of the workflow had been
failing on every push, so the behaviour of the edition was not verified on
a real Windows machine; the workflow is fixed and the findings below were
made on the runner.

### Fixed

- The state of the automatic startup was read from the wrong element of the
  task definition.  A task definition holds two `<Enabled>` elements: the
  one of the trigger says whether the *trigger* is active, the one of the
  settings says whether the *task* is active.  Disabling a task leaves the
  trigger enabled, so `dontneed-dns status` reported a disabled task as
  enabled.
- `dontneed-dns stop` reported a success it could not prove.  The
  `taskkill` command can succeed while the process survives it; the
  component then kept the port and the next start failed with "the port is
  already in use".  The process is now checked and a failure is reported.
  The same applies to the uninstall.
- The uninstall left the scheduled tasks behind when the executable was not
  at the expected path, for example after an installation from the bundled
  wheelhouse.  The tasks are now removed by their name.
- The uninstall reported a success when the directory could not be removed.
  A running program keeps its file locked on Windows, so the removal is
  repeated and the result is reported honestly.
- The Windows edition wrote no log file at all.  The configuration leaves
  the log file empty, so nothing was ever written, and a scheduled task has
  no console to write to.  The platform default is now used when the
  configuration does not name a file.
- The diagnostic report of `dontneed-dns doctor` contained the home
  directory of the user on Windows.  The redaction ran on the serialised
  report, and the JSON encoding escapes the backslashes of a Windows path,
  so the home directory was no longer recognised.  The redaction now runs
  on the values of the report.
- The installation failed when a program wrote to the error stream and
  still succeeded.  `pip` writes such a notice, so the installation from
  the wheelhouse could not succeed.
- The installer only checked that the scheduled tasks existed, not what
  they run.  The definition is read back and compared now.
- The installer could pick the Microsoft Store alias of `python.exe`, which
  opens the Store instead of an interpreter, and it created the virtual
  environment without a guard, so a failure left no log file.
- The `sc create` command of the Windows service backend did not quote the
  path of the program as a whole.  `sc.exe` does not use the rules of the
  command line, so a path with a space in it was cut off and the service
  could not be started.
- The Windows job of the workflow always failed.  The query of a task that
  no longer exists ends with a non zero status, and a cmdlet does not
  reset it, so a successful uninstall looked like a failure.
- A conflict on the datagram port of the resolver was not detected.  The
  resolver needs the port for TCP and for datagrams at the same time, and
  a program that holds only the datagram port broke it silently.
- The process of a component was looked for with `tasklist`, whose output
  is localised and which is missing on some systems.  The Windows kernel
  is asked directly now.
- The system resolver of Windows changed the default timeout of the whole
  process and never restored it.  It is restored when the component stops.
- The PowerShell scripts were only parsed with PowerShell 7, while the end
  user runs them with Windows PowerShell 5.1.  Both engines parse them
  now.
- The health check was happy with any answer of the local resolver.  A
  split domain that is answered by the system resolver means that the plain
  text lookup happens after all, which is the failure this project exists
  to prevent.  The providers are asked directly now and the check reports
  where the answer came from.

### Added

- The type checker `mypy` runs over the sources, and the release scripts
  are linted and formatted like the rest of the project.
- The release verifies that a Windows archive carries the executable and
  the installation scripts, and that every published file is listed in the
  checksum file.
- The release installs the archive, starts the components, runs the health
  check and uninstalls them again, so a broken installer cannot be
  published.
- The workflow checks that the state of the automatic startup is read back
  correctly on the Windows runner.
- The check of the open descriptors used an API of the C runtime that
  Python does not expose.  The property it protects is checked directly
  now: a temporary file that is still open cannot be removed on Windows.
- The Service Control Manager backend is covered by tests now, including
  the quoting of the path of the program.

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
- The Windows edition looks for the PID file where the service runner writes
  it.  The two modules used different directories, so a running component
  was never noticed, the status showed "Stopped" and the uninstall never
  stopped the process.
- The principal of a task is compared with the account of the current user
  in both forms.  The Task Scheduler stores it as a SID, so a correctly
  created task was reported as a task of a different account.
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
