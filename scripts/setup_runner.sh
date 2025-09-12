#!/usr/bin/env bash
set -euo pipefail

# setup_runner.sh — Install & configure a GitHub self‑hosted Actions runner on a Raspberry Pi / ARM Linux.
# Usage:
#   ./setup_runner.sh --repo waariswallie/nilm --token <REG_TOKEN> [--labels pi,arm64] [--version v2.321.0]
# If --version is omitted the latest release is auto‑detected.

REPO=""
TOKEN=""
LABELS="pi,arm64"
VERSION=""
WORKDIR="${HOME}/actions-runner"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo) REPO="$2"; shift 2;;
    --token) TOKEN="$2"; shift 2;;
    --labels) LABELS="$2"; shift 2;;
    --version) VERSION="$2"; shift 2;;
    --dir) WORKDIR="$2"; shift 2;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# //'; exit 0;;
    *) echo "Unknown arg: $1"; exit 1;;
  esac
done

if [[ -z "$REPO" || -z "$TOKEN" ]]; then
  echo "ERROR: --repo en --token zijn verplicht" >&2; exit 1;
fi

ARCH=$(uname -m)
case "$ARCH" in
  aarch64) PKG_ARCH="arm64";;
  armv7l|armv6l) PKG_ARCH="arm";;
  *) echo "Niet ondersteunde arch: $ARCH"; exit 1;;
esac

if [[ -z "$VERSION" ]]; then
  echo "Zoek laatste runner release..."
  VERSION=$(curl -fsSL https://api.github.com/repos/actions/runner/releases/latest | jq -r .tag_name)
fi

echo "Gebruik versie: $VERSION ($PKG_ARCH)"
mkdir -p "$WORKDIR"
cd "$WORKDIR"

TARBALL="actions-runner-linux-${PKG_ARCH}-${VERSION#v}.tar.gz"
URL="https://github.com/actions/runner/releases/download/${VERSION}/${TARBALL}"

echo "Download $URL"
curl -fL -o runner.tgz "$URL"
echo "Uitpakken..."
tar xzf runner.tgz

if [[ ! -x ./config.sh ]]; then
  echo "config.sh niet gevonden na uitpakken" >&2; exit 1;
fi

echo "Configureer runner voor $REPO met labels: $LABELS"
./config.sh --url "https://github.com/${REPO}" --token "$TOKEN" --labels "$LABELS" --unattended --replace

echo "Installeer service"
sudo ./svc.sh install
echo "Start service"
sudo ./svc.sh start
sleep 2
sudo ./svc.sh status || true

echo "Klaar. Controleer in GitHub → Settings → Actions → Runners dat de runner 'online' is."
