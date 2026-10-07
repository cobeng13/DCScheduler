#!/usr/bin/env bash
set -euo pipefail
DEPLOY_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname -- "$DEPLOY_DIR")"
if [[ ! -f "$DEPLOY_DIR/.env" ]]; then
  echo "Create deploy/.env from deploy/.env.example first." >&2
  exit 1
fi
set -a
source "$DEPLOY_DIR/.env"
set +a
: "${PUBLIC_ORIGIN:?Set PUBLIC_ORIGIN}"
: "${BACKUP_DIR:?Set BACKUP_DIR}"
APP_PORT="${APP_PORT:-8004}"
BIND_ADDRESS="${BIND_ADDRESS:-127.0.0.1}"
APP_IMAGE="${APP_IMAGE:-camp-scheduler:local}"
POSTGRES_IMAGE="${POSTGRES_IMAGE:-postgres:16-bookworm}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"
[[ "$APP_PORT" =~ ^[0-9]+$ && "$APP_PORT" -ge 1024 && "$APP_PORT" -le 65535 ]] || { echo "Invalid APP_PORT" >&2; exit 1; }
[[ "$RETENTION_DAYS" =~ ^[0-9]+$ && "$RETENTION_DAYS" -ge 1 ]] || { echo "Invalid retention" >&2; exit 1; }
[[ "$PUBLIC_ORIGIN" == https://* && "$PUBLIC_ORIGIN" != *example* ]] || { echo "Set your real HTTPS PUBLIC_ORIGIN" >&2; exit 1; }
SECRET_DIR="$DEPLOY_DIR/secrets"
APP_ENV=(--env "DATABASE_HOST=scheduler-db" --env "DATABASE_PASSWORD_FILE=/run/secrets/db_password" --env "PUBLIC_ORIGIN=$PUBLIC_ORIGIN" --env COOKIE_SECURE=true)
APP_MOUNTS=(--mount "type=bind,src=$SECRET_DIR/db_password,dst=/run/secrets/db_password,readonly")
LOG_OPTIONS=(--log-driver json-file --log-opt max-size=10m --log-opt max-file=3)
