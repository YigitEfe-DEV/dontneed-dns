# Security policy

## Supported versions

| Version | Supported |
| --- | --- |
| 0.1.x | Yes |

## Reporting a vulnerability

Please **do not** open a public issue for a security problem.

Use the private security advisory of the repository
("Security" -> "Report a vulnerability") on GitHub.  Include:

* the version of `dontneed-dns`,
* the operating system and its version,
* a description of the problem and of the impact,
* the steps that reproduce it.

You will get an acknowledgement within a few days.  A fix and a release are
planned for every confirmed report; the reporter is credited in the release
notes unless they prefer otherwise.

## What counts as a vulnerability

* The tool changes a network setting without the explicit consent that the
  documentation promises.
* The tool executes a command that was influenced by data it received over
  the network.
* A credential, a token or personal data ends up in a log or in a
  diagnostic report.
* The TLS certificate verification can be bypassed.
* The uninstall removes something that does not belong to the project.

## What is not a vulnerability

* The tool does not make a blocked network reachable.  The fragmentation is
  experimental and no bypass is promised.
* The default ports collide with another program.  The port is
  configurable.
* The Windows executable is not signed and Windows shows a warning.
