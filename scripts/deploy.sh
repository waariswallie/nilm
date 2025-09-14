#!/usr/bin/env bash
set -euo pipefail

# Automatic NILM app deployment script for Raspberry Pi (self-hosted pull model)
#
# Features:
#  - Pull latest multi-arch image for a branch (main/init by default)
#  - Create fixed directory layout: /home/pi/docker/dev/nilm/<branch>
#  - Generate docker-compose.yml if missing
#  - Create placeholder .env if missing
#  - Skip container restart if image digest unchanged (unless --force)
#  - Optional: --force, --prune, --branch <name>, --port <port>, --image <ref>
#
# Usage examples:
#   ./deploy.sh --branch init
#   ./deploy.sh --branch main --force
#   ./deploy.sh --branch feature-x --port 8010
#   ./deploy.sh --image ghcr.io/waariswallie/nilm-app:custom-tag
#
# Can be used in cron (pull every 15 min):
#   */15 * * * * /home/pi/docker/dev/nilm/scripts/deploy.sh --branch init >> /var/log/nilm-init.log 2>&1

REPO_OWNER="waariswallie"
IMAGE_BASE="ghcr.io/${REPO_OWNER}/nilm-app"
BASE_DIR="/home/pi/docker/dev/nilm"
BRANCH="init"
PORT_OVERRIDE=""
FORCE=false
PRUNE=false
CUSTOM_IMAGE=""
REPO_SRC_DIR=$(pwd)  # directory waar script is gestart (checkout root)

log() { echo -e "[nilm-deploy] $*"; }
warn() { echo -e "\e[33m[nilm-deploy][warn]\e[0m $*"; }
err()  { echo -e "\e[31m[nilm-deploy][error]\e[0m $*" >&2; }

usage() {
  cat <<EOF
NILM Deploy Script

Options:
  --branch <name>    Branch naam (default: init)
  --image <ref>      Volledig image ref (overschrijft branch mapping)
  --port <port>      Forceer host poort (anders: main=8000, init=8001, anders 8002+)
  --force            Forceer herstart, zelfs als digest gelijk is
  --prune            Verwijder dangling images na succesvolle deploy
  -h|--help          Deze hulp

Exit codes:
  0 success / nothing changed
  10 pull failed
  20 compose failed
EOF
}
  # Probeer sample kopie te maken
  if [[ -f "$REPO_SRC_DIR/.env.sample" ]]; then
    log "Kopieer .env.sample naar .env (pas credentials aan!)"
    cp "$REPO_SRC_DIR/.env.sample" .env
    # Zorg dat verplichte basiskeys bestaan (zonder overschrijven indien sample ze had)
    grep -q '^APP_ENV=' .env || echo 'APP_ENV=prod' >> .env
    grep -q '^LOG_LEVEL=' .env || echo 'LOG_LEVEL=info' >> .env
  else
    log "Maak placeholder .env (geen .env.sample gevonden)"
    cat > .env <<ENV
APP_ENV=prod
LOG_LEVEL=info
# DB_HOST=...
# DB_USER=...
# DB_PASS=...
ENV
  fi
else
  # .env bestaat; check of DB_HOST ontbreekt en sample beschikbaar is
  if ! grep -q '^DB_HOST=' .env && [[ -f "$REPO_SRC_DIR/.env.sample" ]]; then
    log "Append DB_* vars uit .env.sample (DB_HOST ontbrak)"
    grep '^DB_' "$REPO_SRC_DIR/.env.sample" >> .env || true
  fi
fi
    *) err "Onbekende optie: $1"; usage; exit 1;;
  esac
done

command -v docker >/dev/null 2>&1 || { err "docker niet gevonden"; exit 1; }
command -v docker compose >/dev/null 2>&1 || { warn "docker compose plugin ontbreekt? probeer: sudo apt install docker-compose-plugin"; }

SAFE_BRANCH=$(echo "$BRANCH" | sed 's/[^a-zA-Z0-9_-]/-/g')

# Port mapping beleid
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
if [[ -n "$CUSTOM_IMAGE" ]]; then
  IMAGE="$CUSTOM_IMAGE"
fi

log "Branch: $BRANCH (safe: $SAFE_BRANCH)"
log "Directory: $APP_DIR"
log "Image: $IMAGE"
log "Port: $PORT"

cd "$APP_DIR"

# Pull image (capture previous digest via container or existing image)
OLD_DIGEST=""
if docker ps --format '{{.Names}}' | grep -q "^nilm-app-${SAFE_BRANCH}$"; then
  OLD_IMG_ID=$(docker inspect -f '{{.Image}}' nilm-app-${SAFE_BRANCH} 2>/dev/null || true)
  OLD_DIGEST=$(docker inspect -f '{{index .RepoDigests 0}}' "$OLD_IMG_ID" 2>/dev/null || true)
fi
if [[ -z "$OLD_DIGEST" ]]; then
  OLD_DIGEST=$(docker inspect -f '{{index .RepoDigests 0}}' "$IMAGE" 2>/dev/null || true)
fi

log "Oude digest: ${OLD_DIGEST:-<none>}"

if ! docker pull "$IMAGE"; then
  err "Image pull gefaald"; exit 10
fi

NEW_DIGEST=$(docker inspect -f '{{index .RepoDigests 0}}' "$IMAGE" 2>/dev/null || true)
log "Nieuwe digest: ${NEW_DIGEST:-<onbekend>}"

if [[ "$FORCE" = false ]] && [[ -n "$OLD_DIGEST" ]] && [[ -n "$NEW_DIGEST" ]] && [[ "$OLD_DIGEST" == "$NEW_DIGEST" ]]; then
  log "Digest ongewijzigd – geen redeploy (gebruik --force om te forceren)."
  exit 0
fi

COMPOSE_FILE=docker-compose.yml
if [[ ! -f "$COMPOSE_FILE" ]]; then
  log "Genereer $COMPOSE_FILE"
  cat > "$COMPOSE_FILE" <<YAML
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

if [[ ! -f .env ]]; then
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

log "(Re)start compose stack"
if ! docker compose up -d --remove-orphans; then
  err "Compose start gefaald"; exit 20
fi

log "Actieve container:"
docker ps --filter name=nilm-app-$SAFE_BRANCH --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'

if [[ "$PRUNE" = true ]]; then
  log "Prune dangling images"
  docker image prune -f >/dev/null 2>&1 || true
fi

log "Klaar (digest: $NEW_DIGEST)"
exit 0
