#!/usr/bin/env bash
set -euo pipefail

# NILM deployment script (clean version with .env.sample handling)

REPO_OWNER="waariswallie"
IMAGE_BASE="ghcr.io/${REPO_OWNER}/nilm-app"
BASE_DIR="/home/pi/docker/dev/nilm"
BRANCH="init"
PORT_OVERRIDE=""
FORCE=false
PRUNE=false
CUSTOM_IMAGE=""
SOURCE_DIR=$(pwd -P)  # directory from which script is launched (repo checkout)

log() { echo -e "[nilm-deploy] $*"; }
warn() { echo -e "\e[33m[nilm-deploy][warn]\e[0m $*"; }
err()  { echo -e "\e[31m[nilm-deploy][error]\e[0m $*" >&2; }

usage() {
  cat <<EOF
NILM Deploy Script

Options:
  --branch <name>    Branch naam (default: init)
  --image <ref>      Volledig image ref (overschrijft branch mapping)
  --port <port>      Forceer host poort (main=8000, init=8001, anders 8002+)
  --force            Forceer herstart ook bij gelijke digest
  --prune            Verwijder dangling images na deploy
  -h|--help          Deze hulp
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --branch) BRANCH="$2"; shift 2;;
    --image) CUSTOM_IMAGE="$2"; shift 2;;
    --port) PORT_OVERRIDE="$2"; shift 2;;
    --force) FORCE=true; shift;;
    --prune) PRUNE=true; shift;;
    -h|--help) usage; exit 0;;
    *) err "Onbekende optie: $1"; usage; exit 1;;
  esac
done

command -v docker >/dev/null 2>&1 || { err "docker niet gevonden"; exit 1; }
command -v docker compose >/dev/null 2>&1 || { warn "docker compose plugin ontbreekt? sudo apt install docker-compose-plugin"; }

SAFE_BRANCH=$(echo "$BRANCH" | sed 's/[^a-zA-Z0-9_-]/-/g')

if [[ -n "$PORT_OVERRIDE" ]]; then
  PORT="$PORT_OVERRIDE"
else
  case "$BRANCH" in
    main) PORT=8000;;
    init) PORT=8001;;
    *) PORT=8002;;
  esac
fi

APP_DIR="${BASE_DIR}/${SAFE_BRANCH}"
mkdir -p "$APP_DIR"

IMAGE="${IMAGE_BASE}:${SAFE_BRANCH}-latest"
[[ -n "$CUSTOM_IMAGE" ]] && IMAGE="$CUSTOM_IMAGE"

log "Branch: $BRANCH (safe: $SAFE_BRANCH)"
log "Directory: $APP_DIR"
log "Image: $IMAGE"
log "Port: $PORT"

cd "$APP_DIR"

# Image digests (before)
OLD_DIGEST=""
if docker ps --format '{{.Names}}' | grep -q "^nilm-app-${SAFE_BRANCH}$"; then
  CID=$(docker inspect -f '{{.Image}}' nilm-app-${SAFE_BRANCH} 2>/dev/null || true)
  OLD_DIGEST=$(docker inspect -f '{{index .RepoDigests 0}}' "$CID" 2>/dev/null || true)
fi
[[ -z "$OLD_DIGEST" ]] && OLD_DIGEST=$(docker inspect -f '{{index .RepoDigests 0}}' "$IMAGE" 2>/dev/null || true)
log "Oude digest: ${OLD_DIGEST:-<none>}"

if ! docker pull "$IMAGE"; then err "Image pull gefaald"; exit 10; fi
NEW_DIGEST=$(docker inspect -f '{{index .RepoDigests 0}}' "$IMAGE" 2>/dev/null || true)
log "Nieuwe digest: ${NEW_DIGEST:-<onbekend>}"

if [[ "$FORCE" = false && -n "$OLD_DIGEST" && -n "$NEW_DIGEST" && "$OLD_DIGEST" == "$NEW_DIGEST" ]]; then
  log "Digest ongewijzigd – geen redeploy (gebruik --force om te forceren)."; exit 0
fi

# Compose file
COMPOSE_FILE=docker-compose.yml
if [[ ! -f $COMPOSE_FILE ]]; then
  log "Genereer $COMPOSE_FILE"
  cat > $COMPOSE_FILE <<YAML
services:
  nilm-app:
    image: $IMAGE
    container_name: nilm-app-$SAFE_BRANCH
    restart: unless-stopped
    env_file:
      - .env
    ports:
      - $PORT:8000
YAML
fi

# .env handling
if [[ ! -f .env ]]; then
  if [[ -f "$SOURCE_DIR/.env" ]]; then
    log "Kopieer .env vanuit source checkout"
    cp "$SOURCE_DIR/.env" .env
  elif [[ -f "$SOURCE_DIR/.env.sample" ]]; then
    log "Kopieer .env.sample naar .env (pas credentials aan!)"
    cp "$SOURCE_DIR/.env.sample" .env
  else
    log "Maak placeholder .env"
    cat > .env <<ENV
APP_ENV=prod
LOG_LEVEL=info
# DB_HOST=...
# DB_USER=...
# DB_PASS=...
# DB_NAME=...
ENV
  fi
fi

# Vul basiskeys als ze ontbreken
grep -q '^APP_ENV=' .env || echo 'APP_ENV=prod' >> .env
grep -q '^LOG_LEVEL=' .env || echo 'LOG_LEVEL=info' >> .env

# Append DB_* van sample indien DB_HOST ontbreekt
if ! grep -q '^DB_HOST=' .env && [[ -f "$SOURCE_DIR/.env.sample" ]]; then
  log "Append DB_* uit sample (DB_HOST ontbrak)"
  grep '^DB_' "$SOURCE_DIR/.env.sample" >> .env || true
fi

log "(Re)start compose stack"
if ! docker compose up -d --remove-orphans; then err "Compose start gefaald"; exit 20; fi

log "Actieve container:"
docker ps --filter name=nilm-app-$SAFE_BRANCH --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'

if [[ "$PRUNE" = true ]]; then
  log "Prune dangling images"; docker image prune -f >/dev/null 2>&1 || true
fi

log "Klaar (digest: $NEW_DIGEST)"
exit 0
