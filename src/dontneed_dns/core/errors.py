"""Exception hierarchy shared by every layer of the project.

The CLI and TUI translate these into human readable messages, so the error
strings must never contain technical noise such as raw tracebacks.  The
platform specific adapters may attach a ``detail`` field with the underlying
system message, but the ``str()`` form stays safe to display.
"""

from __future__ import annotations


class DontneedDnsError(Exception):
    """Base class for every error raised by this project."""


class ConfigError(DontneedDnsError):
    """The configuration file is missing, malformed or holds invalid values."""


class UnsupportedPlatformError(DontneedDnsError):
    """The requested operation is not available on the current platform."""


class PermissionDeniedError(DontneedDnsError):
    """The operation requires elevated privileges that are not held."""


class ServiceError(DontneedDnsError):
    """A platform service could not be started, stopped or inspected."""


class NetworkConfigError(DontneedDnsError):
    """A networking change could not be applied or rolled back."""


class DNSProtocolError(DontneedDnsError):
    """A DNS packet could not be parsed or built."""


class UpstreamError(DontneedDnsError):
    """An upstream DNS-over-HTTPS provider failed."""


class ProxyError(DontneedDnsError):
    """A proxy protocol or connection error occurred."""
