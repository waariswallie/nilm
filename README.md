# NILM

Non-Intrusive Load Monitoring (NILM) toolkit / playground + een uitbreidbare event-based pipeline met FastAPI & clustering.

Doel: energieverbruik van individuele apparaten afleiden uit een totale (slimme meter) vermogens- of energie tijdreeks (minuut-resolutie, optioneel 3 fasen) en inzicht geven in baseload & apparaat events.

## Inhoud
1. Kern functionaliteit (oorspronkelijke playground)
2. Nieuwe containerized pipeline (`app/`):
   - Ingest MariaDB -> kW series
   - Preprocessing / smoothing
   - Event detectie (step changes)
   - Clustering (DBSCAN) naar kandidaat apparaten
   - Baseload trend
   - FastAPI endpoint `/scan`
3. Verdere roadmap (labels, ML disaggregation)

## Snelle start (lokaal, klassieke code)
```
python -m venv .venv
. .venv/Scripts/Activate.ps1  # Windows PowerShell
pip install -r requirements.txt
pytest -q
```

## Snelle start (Docker + API)
1. Kopieer `.env.sample` naar `.env` en vul waarden
2. (Optioneel) Plaats een `dbconnect.conf` secret (host=..., user=..., password=..., database=...)
3. Build & run:
   ```
   docker compose up --build
   ```
4. Open Swagger: http://localhost:8000/docs
5. Call scan endpoint: `GET /scan`

Output bevat laatste events + baseload per dag.

## Bestanden (nieuw)
```
docker-compose.yml   # Orkestratie
Dockerfile            # Python 3.11 slim + Poetry
pyproject.toml        # Dependencies (pandas, fastapi, sklearn, ...)
app/
  config.py           # Settings + env
  db.py               # MariaDB ingest
  preprocessing.py    # Clean / smooth
  events.py           # Event detectie
  clustering.py       # DBSCAN clustering
  baseload.py         # Nacht-median baseload
  api.py              # FastAPI routes
  main.py             # App entry
agents.md             # Architectuur & roadmap
```

## Oorspronkelijke structuur (playground)
```
src/nilm/
  __init__.py
  io/
    loader.py
  preprocessing.py
  features.py
  disaggregation/
    baseline.py
  evaluation.py
tests/
  test_baseline.py
```

## Roadmap (gecombineerd)
- [x] Baseline event detectie & clustering (/scan)
- [x] Baseload berekening
- [ ] Label rules + `/clusters` & `/events` endpoints
- [ ] Persist events → DB / Parquet
- [ ] Smart plug import (ground truth)
- [ ] Evaluatie metrics (MAE / energy accuracy)
- [ ] ML disaggregation (seq2point / HMM)
- [ ] Export & rapportage endpoints

## CI/CD & Deploy (Raspberry Pi)
Automatisch build & deploy bij push naar `main` (en `init`).

### Belangrijk over netwerk (timeout / i/o timeout)
GitHub *gehoste* runners kunnen je interne LAN (192.168.x.x) meestal niet bereiken → `i/o timeout` bij de SSH stap. Twee oplossingen:

| Methode | Beschrijving | Pro | Con |
|--------|--------------|-----|-----|
| Pull-model (self-hosted runner) | Raspberry Pi draait een self-hosted GitHub Actions runner en trekt zelf het image | Geen inbound poorten/openingen nodig | Runner onderhouden op de Pi |
| Push via publiek bereikbare SSH | Pi publiek bereikbaar of via port-forward/VPN | Geen runner installatie | Netwerk/openbaarheid & security complexities |

Deze repo bevat nu beide workflows:
1. `deploy.yml` (build + SSH deploy) – werkt alleen als de GitHub runner de Pi kan bereiken.
2. `deploy-selfhosted.yml` (pull) – vereist self-hosted runner labels: `self-hosted, linux, arm64, pi`.

Aanbevolen voor thuisnetwerk: gebruik het pull-model (self-hosted).

### Quick Start (Self-hosted Pull Deploy)
1. Haal registration token op: Repo → Settings → Actions → Runners → New self-hosted runner.
2. Op de Pi (vereist curl + jq):
  ```bash
  sudo apt update && sudo apt install -y curl jq tar
  ```
3. Voer (vervang <TOKEN>):
  ```bash
  curl -fsSL https://raw.githubusercontent.com/waariswallie/nilm/init/scripts/setup_runner.sh -o setup_runner.sh
  bash setup_runner.sh --repo waariswallie/nilm --token <TOKEN>
  ```
4. Controleer in GitHub dat de runner “online” staat.
5. Push een commit of run workflow: “Deploy (Self-Hosted Pi Pull)”.
6. Op de Pi verifiëren:
  ```bash
  docker ps | grep nilm-app
  curl -s localhost:8001/health || curl -s localhost:8000/health
  ```
7. (Optioneel) Update `.env` in `/home/pi/docker/dev/nilm/<branch>` en herstart:
  ```bash
  cd /home/pi/docker/dev/nilm/init && docker compose up -d
  ```

### Standalone deploy script
Je kunt buiten GitHub Actions om handmatig (of via cron) updaten met het script:

```
scripts/deploy.sh --branch init
```

Opties:
```
--branch <naam>   (default: init)
--force           Forceer herstart ook als digest gelijk is
--port <poort>    Overschrijft standaard poort mapping
--image <ref>     Gebruik custom image ref (anders ghcr.io/<owner>/nilm-app:<branch>-latest)
--prune           Prune dangling images na deploy
```

Voorbeeld cron (elke 15 minuten check + update):
```
*/15 * * * * /home/pi/docker/dev/nilm/scripts/deploy.sh --branch init >> /var/log/nilm-init.log 2>&1
```

Directory layout blijft hetzelfde: `/home/pi/docker/dev/nilm/<branch>`.

### Volledige pipeline opnieuw draaien
Je hebt drie opties om de build + deploy opnieuw te forceren:

1. GitHub UI (workflow_dispatch)
  - Ga naar Actions → "Build (Image only)" → Run workflow → kies branch
  - Daarna automatisch "Deploy (Self-Hosted Pi Pull)" run (push niet nodig als je eerst een dummy commit doet).

2. Dummy commit (triggervariant)
  ```bash
  echo "# touch" >> pipeline-trigger.txt
  git add pipeline-trigger.txt
  git commit -m "chore: trigger pipeline"
  git push origin $(git rev-parse --abbrev-ref HEAD)
  ```

3. Lokaal multi-arch build & push (repliceert Actions)
  - Vereist: buildx + login bij ghcr.io (`echo $GHCR_PAT | docker login ghcr.io -u <user> --password-stdin` indien private)
  - Bash:
    ```bash
    chmod +x scripts/build-multiarch.sh
    ./scripts/build-multiarch.sh --branch init
    ```
  - PowerShell:
    ```powershell
    ./scripts/build-multiarch.ps1 -Branch init
    ```
  - Daarna op Pi: workflow hoeft niet; run eventueel `scripts/deploy.sh --branch init --force`.

Verificatie checklist:
```
docker pull ghcr.io/waariswallie/nilm-app:init-latest
docker buildx imagetools inspect ghcr.io/waariswallie/nilm-app:init-latest | grep -E "Platform|Name"
ssh pi@<host> docker ps --filter name=nilm-app-init
curl -s http://<pi-ip>:8001/health
```

Indien digest niet wijzigt maar je wél wilt herstarten: `scripts/deploy.sh --branch init --force`.

### Handmatig runner zonder script
Zie eerdere sectie of gebruik GitHub UI instructies. Het script doet alleen: detect arch → download → config → service.

### Self-hosted runner installeren op de Pi
Op de Pi:
```bash
mkdir -p ~/actions-runner && cd ~/actions-runner
curl -o actions-runner.tar.gz -L https://github.com/actions/runner/releases/download/v2.321.0/actions-runner-linux-arm64-2.321.0.tar.gz
tar xzf actions-runner.tar.gz
./config.sh --url https://github.com/<YOUR_USER>/nilm --token <REG_TOKEN> --labels pi,arm64 --unattended
sudo ./svc.sh install
sudo ./svc.sh start
```
Het registratie-token haal je via: GitHub Repo → Settings → Actions → Runners → New self-hosted runner (of gebruik het `setup_runner.sh` script hierboven).

Daarna zal de workflow `Deploy (Self-Hosted Pi Pull)` automatisch bij een push de container pullen en herstarten.

### Bestaande SSH-gebaseerde deploy
Werkt alleen als `RPI_HOST` vanaf internet (of GitHub) bereikbaar is. Voor de meeste thuisinstallaties niet het geval.

### Vereiste GitHub Secrets
| Secret | Beschrijving |
|--------|--------------|
| `RPI_HOST` | IP of hostname (alleen nodig voor SSH-model) |
| `RPI_USER` | SSH user (bv. `pi`) |
| `RPI_SSH_KEY` | Private key (PEM) zonder passphrase |
| `RPI_PORT` | (optioneel) SSH poort, default 22 |

### Directory layout (self-hosted)
```
/home/pi/docker/dev/nilm/
  main/
    docker-compose.yml
    .env
  init/
    docker-compose.yml
    .env
```

De workflow maakt (indien nog niet aanwezig):
- Branch subdirectory
- `docker-compose.yml`
- Placeholder `.env` (met instructieregels) – pas deze aan voor DB / configuratie.

### Voorbereiden Raspberry Pi
```bash
sudo apt update && sudo apt install -y docker.io docker-compose-plugin
sudo usermod -aG docker $USER
mkdir -p /home/pi/docker/dev/nilm
``` 
Eerste self-hosted deploy vult de submap.

### Handmatig herstarten
```bash
ssh pi@192.168.0.70 "cd ~/nilm && docker compose pull && docker compose up -d"
```

## Ontwerpkeuzes
- Event-based i.p.v. volledige sequence model direct → sneller inzicht
- DBSCAN voor unsupervised grouping; later rule + manual labeling
- Baseload = nacht median (02–05h) voor sluipverbruik trend

## Metrics (gepland)
- Event precision/recall (t.o.v. plug data)
- Energy accuracy per apparaat/dag
- Baseload drift (30d median vs vorige week)

## Licentie
MIT (placeholder)
