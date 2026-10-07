#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$0")/common.sh"
[[ "$BACKUP_DIR" == /* && "$BACKUP_DIR" != / ]] || { echo "BACKUP_DIR must be a dedicated absolute directory" >&2; exit 1; }
umask 077
mkdir -p "$BACKUP_DIR"
file="$BACKUP_DIR/scheduler-$(date -u +%Y%m%dT%H%M%SZ).dump"
trap 'rm -f -- "$file.partial"' EXIT
docker exec scheduler-db pg_dump -U scheduler -d scheduler -Fc > "$file.partial"
test -s "$file.partial"
docker exec -i scheduler-db pg_restore --list < "$file.partial" >/dev/null
mv -- "$file.partial" "$file"
find "$BACKUP_DIR" -maxdepth 1 -type f -name 'scheduler-*.dump' -mtime "+$RETENTION_DAYS" -delete
echo "Backup saved: $file"
