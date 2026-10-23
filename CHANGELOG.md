# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
