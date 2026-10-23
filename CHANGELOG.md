# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
