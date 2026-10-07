#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$0")/common.sh"
cd "$PROJECT_DIR"
bash "$DEPLOY_DIR/preflight.sh"
# Refuse uncommitted source so the image label identifies reproducible code.
[[ -z "$(git status --porcelain)" ]] || { echo "Commit or move local changes before deployment." >&2; exit 1; }
revision="$(git rev-parse HEAD)"
mkdir -p "$SECRET_DIR"
chmod 700 "$SECRET_DIR"
if [[ ! -f "$SECRET_DIR/db_password" ]]; then
  umask 077
  head -c 48 /dev/urandom | base64 | tr -d '\n' > "$SECRET_DIR/db_password"
  # The directory is root-only; the mounted file is readable by the app UID.
  chmod 644 "$SECRET_DIR/db_password"
fi
docker network inspect camp-scheduler >/dev/null 2>&1 || docker network create camp-scheduler
docker volume inspect camp-scheduler-postgres >/dev/null 2>&1 || docker volume create camp-scheduler-postgres
if ! docker container inspect scheduler-db >/dev/null 2>&1; then
  docker run -d --name scheduler-db --network camp-scheduler --restart unless-stopped \
    "${LOG_OPTIONS[@]}" --mount type=volume,src=camp-scheduler-postgres,dst=/var/lib/postgresql/data \
    --mount "type=bind,src=$SECRET_DIR/db_password,dst=/run/secrets/db_password,readonly" \
    --env POSTGRES_DB=scheduler --env POSTGRES_USER=scheduler --env POSTGRES_PASSWORD_FILE=/run/secrets/db_password \
    --health-cmd='pg_isready -U scheduler -d scheduler' --health-interval=10s --health-retries=6 "$POSTGRES_IMAGE"
else
  docker start scheduler-db >/dev/null
fi
ready=false
for attempt in $(seq 1 60); do
  if docker exec scheduler-db pg_isready -U scheduler -d scheduler >/dev/null 2>&1; then ready=true; break; fi
  sleep 1
done
[[ "$ready" == true ]] || { echo "Database did not become ready" >&2; exit 1; }
docker build --build-arg "APP_REVISION=$revision" -t "$APP_IMAGE" .
if docker container inspect camp-scheduler >/dev/null 2>&1; then
  bash "$DEPLOY_DIR/backup.sh"
  # Retain the previous immutable image ID for an application rollback.
  docker inspect --format='{{.Image}}' camp-scheduler > "$SECRET_DIR/previous-image"
  docker stop camp-scheduler
fi
docker run --rm --network camp-scheduler "${APP_ENV[@]}" "${APP_MOUNTS[@]}" "$APP_IMAGE" alembic upgrade head
docker container inspect camp-scheduler >/dev/null 2>&1 && docker rm camp-scheduler >/dev/null
docker run -d --name camp-scheduler --network camp-scheduler --restart unless-stopped \
  "${LOG_OPTIONS[@]}" "${APP_ENV[@]}" "${APP_MOUNTS[@]}" --read-only --tmpfs /tmp:rw,noexec,nosuid,size=32m \
  --cap-drop ALL --security-opt no-new-privileges:true --publish "$PUBLISH_ADDRESS:$APP_PORT:8000" "$APP_IMAGE"
check_readiness
printf '%s\n' "$revision" > "$SECRET_DIR/deployed-revision"
echo "Deployed revision $revision"
