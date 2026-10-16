#!/bin/sh
# Render mounts persistent disks root-owned. Start as root, make the data dir writable
# for the app user, then drop privileges before launching the server.
set -e
DB_FILE="${DB_PATH:-/data/yards.db}"
DATA_DIR="$(dirname "$DB_FILE")"
if [ "$(id -u)" = "0" ]; then
  mkdir -p "$DATA_DIR"
  chown app "$DATA_DIR" 2>/dev/null || echo "[entrypoint] warning: chown $DATA_DIR failed"
  # also fix files left from a previous run (db, -wal, -shm)
  for f in "$DB_FILE" "$DB_FILE-wal" "$DB_FILE-shm"; do [ -e "$f" ] && chown app "$f" || true; done
  exec setpriv --reuid=app --regid=app --init-groups "$@"
fi
mkdir -p "$DATA_DIR" 2>/dev/null || true
exec "$@"
