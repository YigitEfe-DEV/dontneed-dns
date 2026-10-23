#!/usr/bin/env bash
#
# Install dontneed-dns on Linux.
#
# The script creates a virtual environment in the user data directory, installs
# the project from the bundled wheelhouse (no network access needed) and
# registers the systemd user services.  Nothing outside of the home directory
# is touched and no root privileges are required.
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/dontneed-dns"
VENV_DIR="$DATA_DIR/venv"
BIN_DIR="$VENV_DIR/bin"

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

# --- prerequisites --------------------------------------------------------

command -v python3 >/dev/null 2>&1 || fail "python3 was not found on the PATH"

python3 - <<'PY' || fail "python 3.11 or newer is required"
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY

if ! command -v systemctl >/dev/null 2>&1; then
    log "systemctl was not found; the service management commands will not work."
fi

# --- virtual environment --------------------------------------------------

log "creating the virtual environment in $VENV_DIR"
mkdir -p "$DATA_DIR"
python3 -m venv "$VENV_DIR"

# --- installation ---------------------------------------------------------

if [ -d "$HERE/wheels" ]; then
    log "installing from the bundled wheelhouse"
    "$BIN_DIR/pip" install --no-index --find-links "$HERE/wheels" dontneed-dns >/dev/null
else
    log "installing from the source tree"
    "$BIN_DIR/pip" install "$HERE" >/dev/null
fi

"$BIN_DIR/dontneed-dns" --version >/dev/null || fail "the installation did not produce a working command"

# --- services -------------------------------------------------------------

if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
    log "registering the systemd user services"
    "$BIN_DIR/dontneed-dns" install --scope user --yes || fail "the services could not be installed"
else
    log "systemd is not available; start the components manually:"
    printf '    %s\n' "$BIN_DIR/dontneed-dns dns start"
    printf '    %s\n' "$BIN_DIR/dontneed-dns proxy start"
fi

# --- done -----------------------------------------------------------------

cat <<EOF

dontneed-dns is installed.

  command    $BIN_DIR/dontneed-dns
  interface  $BIN_DIR/dontneed-dns tui
  status     $BIN_DIR/dontneed-dns status
  logs       $BIN_DIR/dontneed-dns logs

The DNS resolver listens on 127.0.0.1:5354 and the proxy on 127.0.0.1:10808.
Add "$BIN_DIR" to your PATH to use the short command name.

EOF
