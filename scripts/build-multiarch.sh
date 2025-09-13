#!/usr/bin/env bash
set -euo pipefail

# Multi-arch build & push helper (mirrors GitHub Actions build job)
# Requires: docker buildx, logged in to ghcr.io
#
# Usage:
#   ./scripts/build-multiarch.sh --branch init
#   ./scripts/build-multiarch.sh --tags extra1,extra2
#   IMAGE_BASE=ghcr.io/waariswallie/nilm-app ./scripts/build-multiarch.sh

BRANCH="${BRANCH:-}"        # can be injected
EXTRA_TAGS=""
PUSH=true
PLATFORMS="linux/amd64,linux/arm64"
IMAGE_BASE="${IMAGE_BASE:-ghcr.io/waariswallie/nilm-app}"

usage(){ cat <<EOF
Options:
  --branch <name>     Branch name (default: detect from git)
  --no-push           Build only (no push)
  --tags <t1,t2>      Additional tags (comma separated)
  --platforms <list>  Override platforms (default: $PLATFORMS)
  -h|--help           Help
Environment:
  IMAGE_BASE (default: $IMAGE_BASE)
EOF
}

while [[ $# -gt 0 ]]; do
  case $1 in
    --branch) BRANCH="$2"; shift 2 ;;
    --no-push) PUSH=false; shift ;;
    --tags) EXTRA_TAGS="$2"; shift 2 ;;
    --platforms) PLATFORMS="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $1"; usage; exit 1 ;;
  esac
done

if [[ -z "$BRANCH" ]]; then
  BRANCH=$(git rev-parse --abbrev-ref HEAD)
fi
SAFE_BRANCH=$(echo "$BRANCH" | sed 's/[^a-zA-Z0-9_-]/-/g')
SHORT_SHA=$(git rev-parse --short=7 HEAD)

echo "Branch:        $BRANCH"
echo "Safe branch:    $SAFE_BRANCH"
echo "Short SHA:      $SHORT_SHA"
echo "Image base:     $IMAGE_BASE"
echo "Platforms:      $PLATFORMS"

TAGS=("$IMAGE_BASE:${SAFE_BRANCH}-latest" "$IMAGE_BASE:${SAFE_BRANCH}-${SHORT_SHA}")
IFS=',' read -r -a EXTRA <<< "$EXTRA_TAGS" || true
for t in "${EXTRA[@]}"; do
  [[ -n "$t" ]] && TAGS+=("$t")
done

JOINED_TAGS=$(IFS=','; echo "${TAGS[*]}")
echo "Tags:          ${TAGS[*]}"

if ! docker buildx ls | grep -q multiarch-nilm; then
  docker buildx create --name multiarch-nilm --use > /dev/null
fi

docker buildx inspect multiarch-nilm > /dev/null || docker buildx use multiarch-nilm

BUILD_ARGS=(
  --platform "$PLATFORMS" \
  -f Dockerfile \
  --build-arg VCS_REF=$(git rev-parse HEAD) \
)

for t in "${TAGS[@]}"; do
  BUILD_ARGS+=( -t "$t" )
done

if [[ "$PUSH" = true ]]; then
  BUILD_ARGS+=( --push )
else
  BUILD_ARGS+=( --load )
fi

echo "> Building..." >&2
docker buildx build . "${BUILD_ARGS[@]}"

if [[ "$PUSH" = true ]]; then
  echo "> Inspect manifest (latest tag)"
  docker buildx imagetools inspect "$IMAGE_BASE:${SAFE_BRANCH}-latest" || true
fi

echo "Done." 
