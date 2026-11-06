#!/bin/sh
# Install or repair a TOTR server. Run it from the unpacked TOTR folder:
#
#     sudo ./deploy/install.sh              asks a few plain questions, then does everything
#     sudo ./deploy/install.sh --yes        no questions: the built-in SQLite database, the defaults for the rest (see --help)
#     sudo ./deploy/install.sh --help
#
# It needs Python 3.11 or newer (on Debian / Ubuntu: sudo apt install python3 python3-venv openssl). Safe to run again: it repairs, and
# never changes a setting or secret that is already in place.
set -eu

here=$(cd "$(dirname "$0")/.." && pwd)

PY=""
for candidate in python3.14 python3.13 python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    PY=$candidate
    break
  fi
done
if [ -z "$PY" ]; then
  echo "TOTR needs Python 3.11 or newer, and I could not find one."
  echo "Debian / Ubuntu:  sudo apt install python3 python3-venv openssl"
  echo "Arch:             sudo pacman -S python openssl"
  exit 1
fi

needs_root=yes
for arg in "$@"; do
  case "$arg" in --no-service | --help | -h) needs_root=no ;; esac
done
if [ "$needs_root" = yes ] && [ "$(id -u)" -ne 0 ]; then
  echo "Run this with sudo: it makes a 'totr' service user and a systemd service."
  echo "(To only unpack and set up the files without a service: add --no-service.)"
  exit 1
fi

exec "$PY" "$here/tools/install_wizard.py" "$@"
