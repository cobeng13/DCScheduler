#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$0")/common.sh"
bash "$DEPLOY_DIR/preflight.sh"
[[ -f "$SECRET_DIR/previous-image" ]] || { echo "No previous app image was recorded" >&2; exit 1; }
image="$(cat "$SECRET_DIR/previous-image")"
[[ "$image" =~ ^sha256:[a-f0-9]{64}$ ]] || { echo "Invalid previous image ID" >&2; exit 1; }
docker image inspect "$image" >/dev/null
# Use only after confirming the current schema is compatible with this image.
bash "$DEPLOY_DIR/backup.sh"
if docker container inspect camp-scheduler >/dev/null 2>&1; then
  docker stop camp-scheduler
  docker rm camp-scheduler >/dev/null
fi
docker run -d --name camp-scheduler --network camp-scheduler --restart unless-stopped \
  "${LOG_OPTIONS[@]}" "${APP_ENV[@]}" "${APP_MOUNTS[@]}" --read-only --tmpfs /tmp:rw,noexec,nosuid,size=32m \
  --cap-drop ALL --security-opt no-new-privileges:true --publish "$PUBLISH_ADDRESS:$APP_PORT:8000" "$image"
check_readiness
revision="$(docker image inspect --format='{{index .Config.Labels "org.opencontainers.image.revision"}}' "$image")"
printf '%s\n' "${revision:-unknown}" > "$SECRET_DIR/deployed-revision"
echo "Rollback ready; revision ${revision:-unknown}. No database migration was run."
