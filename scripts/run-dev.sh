#!/usr/bin/env bash
set -e
echo "[nilm] Quick dev run" >&2
if [ ! -f .env ]; then
  cp .env.sample .env
  echo "Created .env from sample (adjust DB_ vars if nodig)." >&2
fi
docker compose up --build