#!/usr/bin/env bash
set -euo pipefail

# One-command runner that ensures MOCK_DB=0 and launches uvicorn (dev convenience)

export MOCK_DB=0
if [ ! -f .env ]; then
  echo "No .env found. Copying from .env.sample" >&2
  cp .env.sample .env
fi
echo "Starting NILM app using real MariaDB (MOCK_DB=0)" >&2
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload