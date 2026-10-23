#!/usr/bin/env bash
#
# Remove dontneed-dns from Linux.
#
# Only resources that belong to this project are removed: the systemd user
# services, the virtual environment and the configuration file.  The script
# never touches anything else.
#
set -euo pipefail

DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/dontneed-dns"
VENV_DIR="$DATA_DIR/venv"
BIN_DIR="$VENV_DIR/bin"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/dontneed-dns"

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

if [ -x "$BIN_DIR/dontneed-dns" ]; then
    log "removing the systemd user services"
    "$BIN_DIR/dontneed-dns" uninstall --scope user --yes || log "the services could not be removed"
else
    log "no installation was found in $VENV_DIR"
fi

if [ -d "$VENV_DIR" ]; then
    log "removing the virtual environment"
    rm -rf "$VENV_DIR"
fi

if [ "${1:-}" = "--purge" ]; then
    if [ -d "$CONFIG_DIR" ]; then
        log "removing the configuration in $CONFIG_DIR"
        rm -rf "$CONFIG_DIR"
    fi
else
    log "the configuration in $CONFIG_DIR was kept (use --purge to remove it)"
fi

log "dontneed-dns was removed"
