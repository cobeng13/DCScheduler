#!/usr/bin/env bash
set -euo pipefail

DEPLOY_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname -- "$DEPLOY_DIR")"
cd "$PROJECT_DIR"

command -v git >/dev/null
command -v docker >/dev/null
[[ -f "$DEPLOY_DIR/.env" ]] || { echo "Create deploy/.env from deploy/.env.example first." >&2; exit 1; }
git rev-parse --is-inside-work-tree >/dev/null
if [[ -n "$(git status --porcelain)" ]]; then
  echo "The checkout has local changes. Commit or move them before updating." >&2
  exit 1
fi
if ! git symbolic-ref --quiet HEAD >/dev/null; then
  echo "Check out a branch with a configured upstream before updating." >&2
  exit 1
fi
if ! git rev-parse --verify '@{upstream}' >/dev/null 2>&1; then
  echo "Configure this branch's Git upstream before updating." >&2
  exit 1
fi

echo "Pulling the latest revision from this branch's upstream..."
git pull --ff-only
echo "Building and deploying the scheduler..."
# Execute the freshly pulled deployment script. It builds before downtime,
# backs up the database, migrates explicitly, and checks the relaunched app.
exec bash "$DEPLOY_DIR/deploy.sh"
