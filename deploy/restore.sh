#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$0")/common.sh"
[[ "$#" == 2 && "$2" == --replace-database ]] || { echo "Usage: bash deploy/restore.sh /absolute/backup.dump --replace-database" >&2; exit 1; }
test -f "$1"
docker exec -i scheduler-db pg_restore --list < "$1" >/dev/null
bash "$DEPLOY_DIR/backup.sh"
docker stop camp-scheduler
docker exec scheduler-db dropdb -U scheduler --force scheduler
docker exec scheduler-db createdb -U scheduler -O scheduler scheduler
docker exec -i scheduler-db pg_restore -U scheduler -d scheduler --exit-on-error --no-owner < "$1"
docker start camp-scheduler
echo "Restored. Verify /api/health and login before reopening access."
