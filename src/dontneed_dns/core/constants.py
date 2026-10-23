"""Project-wide constants.

Keeping every tunable that has a sensible default in one place avoids magic
numbers scattered across the code base and makes the defaults reviewable.
"""

from __future__ import annotations

# --- Networking defaults -------------------------------------------------

DEFAULT_DNS_HOST = "127.0.0.1"
DEFAULT_DNS_HOST_V6 = "::1"
DEFAULT_DNS_PORT = 5354

DEFAULT_PROXY_HOST = "127.0.0.1"
DEFAULT_PROXY_PORT = 10808

#: Maximum size of a DNS response we are willing to receive over UDP.
MAX_UDP_PAYLOAD = 4096
#: Maximum size of a DNS message accepted over TCP.
MAX_TCP_PAYLOAD = 65535
#: Size of the buffer used while reading from a socket.
SOCKET_BUFFER_SIZE = 65535
#: Refuse DNS requests larger than this before parsing them.
MAX_REQUEST_SIZE = 65535

# --- Timeouts (seconds) --------------------------------------------------

DEFAULT_DOH_TIMEOUT = 3.0
DEFAULT_DOH_RETRIES = 2
DEFAULT_PROXY_CONNECT_TIMEOUT = 5.0
DEFAULT_PROXY_IDLE_TIMEOUT = 300.0
DEFAULT_PROXY_HANDSHAKE_TIMEOUT = 15.0
DEFAULT_COMMAND_TIMEOUT = 30.0
DEFAULT_CONNECT_TIMEOUT = 5.0

# --- Concurrency ---------------------------------------------------------

DEFAULT_MAX_CONNECTIONS = 256
DEFAULT_MAX_DNS_CONCURRENCY = 64
DEFAULT_LISTEN_BACKLOG = 128

# --- DNS -----------------------------------------------------------------

DNS_HEADER_SIZE = 12
DNS_MAX_LABEL_LENGTH = 63
DNS_MAX_NAME_LENGTH = 255
DNS_CLASS_IN = 1
DNS_TYPE_A = 1
DNS_TYPE_AAAA = 28
DNS_TYPE_CNAME = 5
DNS_TYPE_OPT = 41

RCODE_NOERROR = 0
RCODE_FORMERR = 1
RCODE_SERVFAIL = 2
RCODE_NXDOMAIN = 3
RCODE_NOTIMP = 4
RCODE_REFUSED = 5

#: Domains routed through DNS-over-HTTPS by the split resolver.
DEFAULT_SPLIT_DOMAINS: tuple[str, ...] = (
    "discord.com",
    "discordapp.com",
    "discord.gg",
    "dis.gd",
    "discord.dev",
)

# --- Fragmentation (experimental) ---------------------------------------

DEFAULT_FRAGMENT_CHUNK_SIZE = 16
DEFAULT_FRAGMENT_HEADER_SIZE = 5
DEFAULT_FRAGMENT_PREAMBLE_SIZE = 15
DEFAULT_FRAGMENT_PREAMBLE_DELAY_MS = 50
DEFAULT_FRAGMENT_CHUNK_DELAY_MS = 10

# --- Filesystem ----------------------------------------------------------

APP_DIR_NAME = "dontneed-dns"
SERVICE_NAME_PREFIX = "dontneed-dns"
LOG_FILE_NAME = "dontneed-dns.log"
CONFIG_FILE_NAME = "config.toml"

# --- Unit / task names ---------------------------------------------------

DNS_UNIT_NAME = "dontneed-dns-dns.service"
PROXY_UNIT_NAME = "dontneed-dns-proxy.service"
DNS_TASK_NAME = "dontneed-dns-dns"
PROXY_TASK_NAME = "dontneed-dns-proxy"

#: Windows Service Control Manager service names.
DNS_SERVICE_NAME = "dontneed-dns-dns"
PROXY_SERVICE_NAME = "dontneed-dns-proxy"
