#!/bin/bash
# A throwaway Arch Linux container on THIS machine for deploy/lab-test.sh, so the test's "remove /opt/totr and the totr user" happens in the container and never on
# the real machine (or on your server). Run as root on an Arch host:
#     sudo ./deploy/lab-container.sh make           build the container once (needs the internet; installs arch-install-scripts if missing)
#     sudo ./deploy/lab-container.sh test [args]    boot it, copy this TOTRsrv folder in, run lab-test.sh [args] (default: sqlite update; 'sqlite move' sets up PostgreSQL in the container first) and shut it down
#     sudo ./deploy/lab-container.sh remove         delete the container
# It shares the host's network (the container's server listens on the host's ports 8080/8443 while the test runs: stop a real TOTR first if it uses them).
set -eu
NAME=totrlab
ROOT=/var/lib/machines/$NAME
here=$(cd "$(dirname "$0")/.." && pwd)
[ "$(id -u)" -eq 0 ] || {
  echo "run as root"
  exit 2
}
case "${1:-}" in
  make)
    command -v pacstrap >/dev/null || pacman -S --needed --noconfirm arch-install-scripts
    [ -e "$ROOT" ] && {
      echo "$ROOT already exists (use 'remove' first)"
      exit 1
    }
    mkdir -p "$ROOT"
    pacstrap -c "$ROOT" base python python-pip python-cryptography openssl postgresql sudo
    echo "pts/0" >>"$ROOT/etc/securetty" 2>/dev/null || true
    echo "Built $ROOT. Next: sudo $0 test"
    ;;
  test)
    shift
    [ -e "$ROOT" ] || {
      echo "no container yet: sudo $0 make"
      exit 1
    }
    machinectl terminate $NAME 2>/dev/null || true
    rm -rf "$ROOT/root/TOTRsrv"
    mkdir -p "$ROOT/root"
    # a copy without the venv, the database, logs and secrets, so the container starts from what a fresh checkout has
    tar -C "$here/.." --exclude=.venv --exclude=__pycache__ --exclude=.env --exclude='*.db' --exclude=certs -cf - "$(basename "$here")" | tar -C "$ROOT/root" -xf -
    systemd-nspawn -D "$ROOT" -M $NAME -b --quiet >/tmp/$NAME.boot.log 2>&1 &
    for _ in $(seq 1 60); do
      machinectl show $NAME >/dev/null 2>&1 && break
      sleep 1
    done
    sleep 5
    case "${*:-}" in *move*) # the move test needs a PostgreSQL server inside the container that takes a password login on 127.0.0.1
      # local socket logins need no password (lab-test.sh runs `psql -U postgres` as root and makes its own administrator); logins over TCP need one
      machinectl shell root@$NAME /bin/bash -c "systemctl stop postgresql; rm -rf /var/lib/postgres/data; su postgres -c 'initdb -D /var/lib/postgres/data --auth-local=trust --auth-host=scram-sha-256' && systemctl enable --now postgresql && sleep 3"
      ;;
    esac
    machinectl shell root@$NAME /bin/bash -c "cd /root/$(basename "$here") && ./deploy/lab-test.sh ${*:-sqlite update}" && rc=0 || rc=$?
    machinectl poweroff $NAME 2>/dev/null || true
    echo "lab-test exit status: $rc"
    exit "$rc"
    ;;
  remove)
    machinectl terminate $NAME 2>/dev/null || true
    rm -rf "$ROOT"
    echo removed
    ;;
  *)
    sed -n 2,9p "$0"
    exit 2
    ;;
esac
