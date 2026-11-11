#!/bin/sh
# Local containers only; never calls kubectl or modifies external deployments.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"
export SRS_EAM_PASSWORD=${SRS_EAM_PASSWORD:-$(python3 -c 'import secrets;print(secrets.token_urlsafe(24))')}
mkdir -p artifacts
chmod 777 artifacts
if command -v podman-compose >/dev/null 2>&1; then
    COMPOSE=podman-compose
else
    COMPOSE='docker compose'
fi
case ${1:-verify} in
  build) $COMPOSE build ;;
  up) $COMPOSE up -d ;;
  down) $COMPOSE down ;;
  verify)
    $COMPOSE up -d
    $COMPOSE --profile verify run --rm verify
    ;;
  *) printf 'usage: %s build|up|verify|down\n' "$0" >&2; exit 2 ;;
esac
