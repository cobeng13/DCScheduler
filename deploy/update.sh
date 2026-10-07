#!/usr/bin/env bash
set -euo pipefail

DEPLOY_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname -- "$DEPLOY_DIR")"
REPOSITORY_URL="https://github.com/cobeng13/DCScheduler.git"
DEPLOY_BRANCH="main"
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
  echo "Check out a branch before updating." >&2
  exit 1
fi

bash "$DEPLOY_DIR/preflight.sh"

echo "Pulling $DEPLOY_BRANCH from $REPOSITORY_URL..."
# Use the online repository explicitly, including in checkouts whose origin
# still points at the original local scheduler repository.
git pull --ff-only "$REPOSITORY_URL" "$DEPLOY_BRANCH"
echo "Building and deploying the scheduler..."
# Execute the freshly pulled deployment script. It builds before downtime,
# backs up the database, migrates explicitly, and checks the relaunched app.
exec bash "$DEPLOY_DIR/deploy.sh"
