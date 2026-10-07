#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$0")/common.sh"

fail() { echo "Preflight failed: $*" >&2; exit 1; }
for command in docker ss ip df stat curl git; do
  command -v "$command" >/dev/null || fail "Required command not found: $command"
done
docker info >/dev/null 2>&1 || fail "Docker daemon is unavailable"
[[ "$MIN_FREE_DISK_MB" =~ ^[0-9]+$ && "$MIN_FREE_DISK_MB" -ge 256 ]] || fail "Invalid MIN_FREE_DISK_MB"

# Published Docker ports need not have an ss listener when Docker's userland
# proxy is disabled. Reject another container's mapping as well.
containers="$(docker ps --format '{{.Names}}')" || fail "Cannot list Docker containers"
while IFS= read -r container; do
  [[ -n "$container" && "$container" != camp-scheduler ]] || continue
  ports="$(docker port "$container")" || fail "Cannot inspect ports for $container"
  if grep -Eq ":${APP_PORT}$" <<< "$ports"; then
    fail "Port $APP_PORT is published by another container: $container"
  fi
done <<< "$containers"

# Require a numeric address actually assigned to this host, or an explicit
# wildcard. Do not silently resolve a hostname to another machine.
if [[ "$BIND_ADDRESS" != 0.0.0.0 && "$BIND_ADDRESS" != :: ]]; then
  ip -o address show | awk '{split($4, a, "/"); print a[1]}' | grep -Fxq -- "$BIND_ADDRESS" || fail "BIND_ADDRESS is not assigned to this host: $BIND_ADDRESS"
fi
if ss -H -ltn "sport = :$APP_PORT" | grep -q .; then
  # An update may reuse the exact mapping of its own running container.
  [[ "$(docker inspect --format='{{.State.Running}}' camp-scheduler 2>/dev/null || true)" == true ]] || fail "Port $APP_PORT is in use"
  mapping="$BIND_ADDRESS:$APP_PORT"
  [[ "$BIND_ADDRESS" != *:* ]] || mapping="[$BIND_ADDRESS]:$APP_PORT"
  docker port camp-scheduler 8000/tcp | grep -Fxq -- "$mapping" || fail "Port $APP_PORT is not the scheduler's current mapping"
fi

[[ "$BACKUP_DIR" == /* && "$BACKUP_DIR" != / ]] || fail "BACKUP_DIR must be a dedicated absolute directory"
mkdir -p -- "$BACKUP_DIR" || fail "Cannot create BACKUP_DIR"
probe="$(mktemp "$BACKUP_DIR/.scheduler-write-check.XXXXXX")" || fail "BACKUP_DIR is not writable"
rm -f -- "$probe"
docker_root="$(docker info --format '{{.DockerRootDir}}')"
for directory in "$PROJECT_DIR" "$docker_root" "$BACKUP_DIR"; do
  free_kb="$(df -Pk -- "$directory" | awk 'NR==2 {print $4}')"
  [[ "$free_kb" =~ ^[0-9]+$ && "$free_kb" -ge $((MIN_FREE_DISK_MB * 1024)) ]] || fail "Less than $MIN_FREE_DISK_MB MB free at $directory"
done

if [[ -e "$SECRET_DIR" ]]; then
  [[ -d "$SECRET_DIR" && ! -L "$SECRET_DIR" ]] || fail "Secrets directory must be a real directory"
  [[ "$(stat -c %a "$SECRET_DIR")" == 700 && "$(stat -c %u "$SECRET_DIR")" == "$EUID" ]] || fail "Secrets directory must be owned by deployment user with mode 700"
fi
if [[ -e "$SECRET_DIR/db_password" ]]; then
  [[ -f "$SECRET_DIR/db_password" && ! -L "$SECRET_DIR/db_password" && -s "$SECRET_DIR/db_password" ]] || fail "Invalid database password file"
  [[ "$(stat -c %a "$SECRET_DIR/db_password")" == 644 && "$(stat -c %u "$SECRET_DIR/db_password")" == "$EUID" ]] || fail "Database password file must be owned by deployment user with mode 644 inside the private 700 directory"
fi
if docker container inspect scheduler-db >/dev/null 2>&1; then
  [[ -s "$SECRET_DIR/db_password" ]] || fail "Existing database secret is missing; do not generate a replacement"
  [[ "$(docker inspect --format='{{.State.Health.Status}}' scheduler-db)" == healthy ]] || fail "PostgreSQL container is not healthy"
  docker exec scheduler-db pg_isready -U scheduler -d scheduler >/dev/null || fail "PostgreSQL is not ready"
fi
echo "Preflight passed"
