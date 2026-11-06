#!/bin/bash
# End-to-end test of the installer on a DISPOSABLE Linux machine or VM (it installs a real service and, at the start, REMOVES /opt/totr and the 'totr' user).
# Run as root from the unpacked TOTR folder:   sudo ./deploy/lab-test.sh [sqlite|postgres] [update|move]
# 'update' adds a real signed-update test at the end (needs the internet, because the new release's Python packages are installed): a good update, then a deliberately
# broken one that must roll back. It uses a throwaway signing key and temporarily puts its public half in update_pubkey.txt (restored at the end).
# 'move' (sqlite mode only, last, because it switches the install to PostgreSQL) tests the admin page's Move to PostgreSQL helper: a rehearsal, then the real move, then a
# restart on the new database. It needs a PostgreSQL server on this machine that accepts a PASSWORD login on 127.0.0.1 (the usual default); it makes and removes its own
# administrator account (labadmin).
# Postgres mode needs a PostgreSQL server on this machine that `psql -U postgres` can reach without a password (peer/trust), e.g. a fresh install of the
# distribution's postgresql package.
set -u
MODE=${1:-sqlite}
UPDATE_TEST=${2:-}
MOVE_TEST=
[ "$UPDATE_TEST" = move ] && {
  MOVE_TEST=move
  UPDATE_TEST=
}
here=$(cd "$(dirname "$0")/.." && pwd)
PREFIX=/opt/totr
pass=0
fail=0
ok() {
  echo "ok   $1"
  pass=$((pass + 1))
}
bad() {
  echo "BAD  $1"
  fail=$((fail + 1))
}
check() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }

[ "$(id -u)" -eq 0 ] || {
  echo "run as root"
  exit 2
}
echo "== clean slate"
START=$(date "+%Y-%m-%d %H:%M:%S") # the log check looks only at what happened after this
systemctl stop totr 2>/dev/null
systemctl disable totr 2>/dev/null
rm -f /etc/systemd/system/totr.service
systemctl daemon-reload
rm -rf "$PREFIX"
id totr >/dev/null 2>&1 && userdel totr
if [ "$MODE" = postgres ]; then
  psql -U postgres -c "DROP DATABASE IF EXISTS labtotr" >/dev/null 2>&1
  for r in labt_read labt_write labt_maint labt_auth labt_mixtape; do psql -U postgres -c "DROP ROLE IF EXISTS $r" >/dev/null 2>&1; done
  ARGS="--db postgres --pg-host /run/postgresql --pg-name labtotr --pg-admin-user postgres --pg-role-prefix labt"
else
  ARGS="--db sqlite"
fi

if [ "$UPDATE_TEST" = update ]; then
  SIGNDIR=$(mktemp -d)
  cp "$here/update_pubkey.txt" "$SIGNDIR/update_pubkey.txt.orig"
  export TOTR_SIGNING_PASSPHRASE="lab test passphrase, throwaway"
  python3 "$here/tools/release_tool.py" keygen --out "$SIGNDIR/keys" >/dev/null
  cp "$SIGNDIR/keys/update_pubkey.txt" "$here/update_pubkey.txt"
fi
echo "== install ($MODE)"
# shellcheck disable=SC2086  # ARGS is a list of options, split on purpose
"$here/deploy/install.sh" --yes --site-name "Lab test" $ARGS | tee /tmp/totr-install.txt
check "the installer finished (exit 0)" "[ \${PIPESTATUS[0]} -eq 0 ]"

IP=$(grep -m1 '^Address:' /tmp/totr-install.txt | sed 's#.*//##; s#:.*##')
LINK=$(grep -m1 'First admin:' /tmp/totr-install.txt | sed 's#.*open  *##')
check "the service is active" "systemctl is-active --quiet totr"
check "the service starts at boot" "systemctl is-enabled --quiet totr"
check "it runs as the totr user, not root" "[ \"\$(ps -o user= -p \$(systemctl show -p MainPID --value totr))\" = totr ]"
check "/health answers over HTTPS, checked against the server's own CA" "curl -fsS --cacert $PREFIX/state/certs/totr-ca.pem https://$IP:8443/health | grep -q ok"
check "plain HTTP is off" "! curl -fsS -m 3 http://$IP:8080/health 2>/dev/null"
check "the admin sign-in link opens" "[ \"\$(curl -s -o /dev/null -w '%{http_code}' --cacert $PREFIX/state/certs/totr-ca.pem '$LINK')\" = 200 ]"
check ".env is mode 600 and owned by totr" "[ \"\$(stat -c '%a %U' $PREFIX/state/.env)\" = '600 totr' ]"
check "the CA key is mode 600" "[ \"\$(stat -c %a $PREFIX/state/certs/totr-ca.key)\" = 600 ]"
check "the update service is ready (its path unit is active and enabled)" "systemctl is-active --quiet totr-update.path && systemctl is-enabled --quiet totr-update.path"
check "the web app's user can write the update folder" "runuser -u totr -- test -w $PREFIX/state/updates"
check "the code is not writable by the service user" "! runuser -u totr -- test -w $PREFIX/current/main.py"
check "no password in the install output" "! grep -qi 'password=' /tmp/totr-install.txt"
if [ "$MODE" = postgres ]; then
  check "five roles were made" "[ \"\$(psql -U postgres -Atc \"SELECT count(*) FROM pg_roles WHERE rolname LIKE 'labt\\_%'\")\" = 5 ]"
  check "the database is at the newest version" "[ \"\$(psql -U postgres -d labtotr -Atc 'SELECT max(version) FROM totr.schema_version')\" = 2 ]"
  check "the PostgreSQL admin login is not kept anywhere in .env" "! grep -Eqi '^DB_ADMIN|PG_ADMIN' $PREFIX/state/.env"
  # The media schema is a fixed design and must be the same in every install: dump what was built and compare it, line for line, with the dump of the original
  # (comments, blank lines and the dump's own session settings are ignored; the pg_bigm indexes only exist where that extension does).
  norm() { grep -vE '^(--|\\restrict|\\unrestrict|SET |SELECT pg_catalog\.set_config|[[:space:]]*$)' | grep -v bigm; }
  psql -U postgres -d labtotr -c '\q' >/dev/null 2>&1 && pg_dump -U postgres -d labtotr -n media --schema-only --no-owner --no-privileges | norm >/tmp/totr-media-built.txt
  norm <"$here/sql/reference/media_schema_reference.sql" >/tmp/totr-media-ref.txt
  check "the media schema is exactly the reference (pg_dump compared with sql/reference/media_schema_reference.sql)" "diff -u /tmp/totr-media-ref.txt /tmp/totr-media-built.txt"
fi

if [ "$UPDATE_TEST" = update ]; then
  echo "== update: a real signed update, then a broken one"
  STATE=$PREFIX/state
  UPD=$STATE/updates
  PY=$PREFIX/current/.venv/bin/python
  make_release() { # $1 = version, $2 = "ok" or "broken"; writes $SIGNDIR/rel-$1 and files it as the web app would
    local v=$1 kind=$2 src=$SIGNDIR/src-$1 out=$SIGNDIR/rel-$1
    rm -rf "$src" "$out"
    mkdir -p "$src"
    (cd "$here" && tar --exclude=.venv --exclude=__pycache__ --exclude=tests --exclude=data --exclude=reports --exclude=certs --exclude=downloads -cf - .) | tar -xf - -C "$src"
    sed -i "s/^SERVER_VERSION = .*/SERVER_VERSION = \"$v\"/" "$src/version.py"
    [ "$kind" = broken ] && echo "this is not python (" >>"$src/main.py"
    (cd "$src" && TOTR_SIGNING_PASSPHRASE="$TOTR_SIGNING_PASSPHRASE" python3 tools/release_tool.py build --key "$SIGNDIR/keys/signing.key" --out "$out" --notes "lab $kind" >/dev/null)
    rm -rf "$UPD/pending"
    cp -r "$out" "$UPD/pending"
    chown -R totr:totr "$UPD/pending"
    echo "{\"release\": \"$v\"}" >"$UPD/request.json"
    chown totr:totr "$UPD/request.json"
  }
  wait_for() { # $1 = a status stage, up to 300 s
    for _ in $(seq 1 300); do
      grep -q "\"stage\": \"$1\"" "$UPD/status.json" 2>/dev/null && return 0
      sleep 1
    done
    return 1
  }
  make_release 99.0.0 ok
  check "a signed update is installed by the update service" "wait_for done"
  check "the code now in use is the new release" "[ \"\$(readlink $PREFIX/current)\" = $PREFIX/releases/99.0.0 ]"
  check "and the server answers" "curl -fsS --cacert $PREFIX/state/certs/totr-ca.pem https://$IP:8443/health | grep -q ok"
  check "the web app's user could not have done that itself (the code is still read-only for it)" "! runuser -u totr -- test -w $PREFIX/current/main.py"
  make_release 99.1.0 broken
  check "a broken update is rolled back" "wait_for rolled_back"
  check "the working version is back in use" "[ \"\$(readlink $PREFIX/current)\" = $PREFIX/releases/99.0.0 ]"
  check "and the server answers again" "curl -fsS --cacert $PREFIX/state/certs/totr-ca.pem https://$IP:8443/health | grep -q ok"
  cp "$SIGNDIR/update_pubkey.txt.orig" "$here/update_pubkey.txt"
  rm -rf "$SIGNDIR"
fi

echo "== crash: kill -9 and see it come back"
PID=$(systemctl show -p MainPID --value totr)
kill -9 "$PID"
sleep 8
check "it restarted by itself with a new process" "systemctl is-active --quiet totr && [ \"\$(systemctl show -p MainPID --value totr)\" != \"$PID\" ]"
check "and answers again" "curl -fsS --cacert $PREFIX/state/certs/totr-ca.pem https://$IP:8443/health | grep -q ok"

echo "== run the installer again: it repairs and changes nothing that exists"
before=$(sha256sum $PREFIX/state/.env $PREFIX/state/certs/totr-ca.pem $PREFIX/state/certs/server.pem | sha256sum)
# shellcheck disable=SC2086  # ARGS is a list of options, split on purpose
"$here/deploy/install.sh" --yes --site-name "Lab test" $ARGS >/tmp/totr-install2.txt
check "second run finished (exit 0)" "[ \$? -eq 0 ]"
after=$(sha256sum $PREFIX/state/.env $PREFIX/state/certs/totr-ca.pem $PREFIX/state/certs/server.pem | sha256sum)
check "settings, secrets and certificates are exactly as before" "[ '$before' = '$after' ]"
check "it said it was repairing" "grep -q repairing /tmp/totr-install2.txt"
check "the service is still active" "systemctl is-active --quiet totr"

echo "== the log"
check "the log of this run has no traceback or permission error" "! journalctl -u totr --since \"$START\" --no-pager | grep -Ei 'Traceback|PermissionError|Read-only file system'"

if [ "$MOVE_TEST" = move ] && [ "$MODE" = sqlite ]; then
  echo "== move to PostgreSQL: rehearsal, the real move, then a restart on the new database"
  STATE=$PREFIX/state
  PY=$PREFIX/current/.venv/bin/python
  psql -U postgres -c "DROP DATABASE IF EXISTS labmove" >/dev/null 2>&1
  psql -U postgres -c "DROP DATABASE IF EXISTS labmove_rehearsal" >/dev/null 2>&1
  for r in labmove_read labmove_write labmove_maint labmove_auth labmove_mixtape labadmin; do psql -U postgres -c "DROP ROLE IF EXISTS $r" >/dev/null 2>&1; done
  psql -U postgres -c "CREATE ROLE labadmin LOGIN SUPERUSER PASSWORD 'lab-admin-password-123'" >/dev/null
  helper() { # $1 = mode; the administrator's password goes in on standard input, as the admin page does it
    printf '{"mode": "%s", "host": "127.0.0.1", "port": "5432", "dbname": "labmove", "sslmode": "disable", "roles": "several", "admin_user": "labadmin", "admin_password": "lab-admin-password-123"}' "$1" |
      runuser -u totr -- env TOTR_STATE_DIR=$STATE $PY $PREFIX/current/tools/move_to_postgres.py
  }
  helper test >/tmp/totr-move-test.txt
  check "the connection test passes" "grep -q '\"ok\": true' /tmp/totr-move-test.txt"
  helper rehearse >/tmp/totr-move-rehearse.txt
  check "the rehearsal worked" "tail -1 /tmp/totr-move-rehearse.txt | grep -q '\"rehearsal\": true'"
  check "the rehearsal kept nothing (no scratch database, no database, .env unchanged)" "[ -z \"\$(psql -U postgres -Atc \"SELECT datname FROM pg_database WHERE datname LIKE 'labmove%'\")\" ] && ! grep -q '^DB_ENGINE=postgres' $STATE/.env"
  helper move >/tmp/totr-move-real.txt
  check "the move worked" "tail -1 /tmp/totr-move-real.txt | grep -q '\"ok\": true'"
  check "the helper never printed the administrator's password or a new role password" "! grep -q 'lab-admin-password-123' /tmp/totr-move-real.txt /tmp/totr-move-test.txt && ! grep -qE 'DB_(AUTH_)?PASSWORD=' /tmp/totr-move-real.txt"
  check ".env now names PostgreSQL, still mode 600, and the old one is kept" "grep -q '^DB_ENGINE=postgres' $STATE/.env && [ \"\$(stat -c %a $STATE/.env)\" = 600 ] && [ -f $STATE/.env.before-postgres ]"
  check "the SQLite file was set aside, not deleted" "[ ! -f $STATE/data/totr.db ] && [ -f $STATE/data/totr.db.before-postgres ]"
  systemctl restart totr
  sleep 8
  check "TOTR runs on PostgreSQL after the restart" "systemctl is-active --quiet totr && curl -fsS --cacert $PREFIX/state/certs/totr-ca.pem https://$IP:8443/health | grep -q ok"
  check "the data is in PostgreSQL" "[ \"\$(psql -U postgres -d labmove -Atc 'SELECT count(*) FROM media.item_types')\" -ge 1 ]"
fi

echo
echo "$pass passed, $fail failed"
[ "$fail" -eq 0 ]
