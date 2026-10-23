# Contributing

Thank you for taking the time to improve `dontneed-dns`.

## Getting started

```bash
git clone https://github.com/YigitEfe-DEV/dontneed-dns
cd dontneed-dns
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

Python 3.11 or newer is required.

## Before you open a pull request

```bash
.venv/bin/ruff format --check src/ tests/
.venv/bin/ruff check src/ tests/
.venv/bin/python -m pytest tests/ -q
```

All three have to pass.  The same commands run in the GitHub Actions
workflow on both Linux and Windows.

## The rules of the project

1. **The shared code never calls a platform administration command.**  If a
   command is needed, add a method to `PlatformAdapter` and implement it in
   `platforms/linux/` and `platforms/windows/`.
2. **A feature that cannot be implemented safely on a platform is marked
   `UNSUPPORTED`** in the capability matrix, with a note.  It is never
   emulated with a hack.
3. **No global network change happens without an explicit confirmation and a
   rollback.**  If a change can be undone, the undo has to be implemented
   and tested.
4. **No telemetry, no credentials, no unreviewed binary.**
5. **The tests cover the behaviour, not the implementation.**  A change that
   breaks the protocol has to break a test.

## The structure

```
src/dontneed_dns/
  core/        portable: DNS messages, DoH, resolver, configuration, redaction
  dns/         the DNS server
  proxy/       SOCKS5, HTTP CONNECT, the fragmenter, the proxy server
  cli/         the command line interface
  tui/         the terminal interface
  platforms/
    linux/     systemd, distributions, installation
    windows/   Task Scheduler, Windows services, paths
tests/
  unit/        pure logic
  integration/ real sockets
  linux/       the Linux edition
  windows/     the Windows edition
```

## Writing tests

* A new protocol rule needs a test in `tests/unit`.
* A new behaviour that touches a socket needs a test in `tests/integration`.
* A new platform operation needs a test in `tests/linux` or `tests/windows`
  with the external command mocked.
* A Windows behaviour is only considered verified when the test ran on the
  Windows runner of the workflow.

## Commit messages

Use the conventional prefixes:

```
feat: add the split resolver
fix: read the SOCKS5 request in full
docs: describe the Windows service backend
test: cover the truncated DNS answer
```

## Reporting a bug

Please include:

* the operating system and the version (`dontneed-dns status` prints it),
* the output of `dontneed-dns check`,
* the output of `dontneed-dns doctor -o report.json` - it is redacted and
  meant to be attached.

Never attach a log file without reading it first: the log contains the
domain names that were queried.

## Security issues

Please use the private security advisory of the repository instead of a
public issue.  See [SECURITY.md](SECURITY.md).
