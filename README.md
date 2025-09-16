### Database kolom mapping (env vars)
Je kunt afwijkende kolomnamen configureren zonder codewijziging:

| Variabele | Default | Betekenis |
|-----------|---------|-----------|
| `TABLE_NAME` | meterstanden | Tabel met cumulatieve standen |
| `TIME_COLUMN` | time | Tijdstempel kolom |
| `CONSUME_COLS` | p1,p2 | Cumulatieve kWh import kolommen |
| `EXPORT_COLS` | n1,n2 | Cumulatieve kWh export kolommen |
| `PHASE_KWH_COLS` | L1_kwh,L2_kwh,L3_kwh | Per-fase kWh kolommen (optioneel) |

Ontbreken fase kolommen → automatische fallback (query opnieuw zonder deze kolommen).
Voorbeeld `.env` fragment:
```
DB_HOST=192.168.0.70
DB_USER=nilm
DB_PASS=SterkPass!
DB_NAME=energy
TABLE_NAME=meterstanden
TIME_COLUMN=ts
CONSUME_COLS=cons1,cons2
EXPORT_COLS=exp1,exp2
PHASE_KWH_COLS=
```

### Performance & prioriteit

Je kunt het CPU scheduling gedrag van het proces tunen (Linux/RPi):

| Variabele | Betekenis | Voorbeeld |
|-----------|-----------|-----------|
| `NICE_LEVEL` | Gewenste nice waarde (relatief lager = hogere prioriteit, vereist rechten voor negatieve waarden) | `-5` |
| `CPU_AFFINITY` | Komma lijst van CPU core IDs waarop de app mag draaien | `0,1` |

Voorbeeld:
```
NICE_LEVEL=-5
CPU_AFFINITY=2,3
```

Overige optimalisaties:
1. Beperk dataset: verlaag `LOOKBACK_DAYS` of implementeer incrementiële fetch (future: cache layer).
2. Indexeer je tabel: `CREATE INDEX idx_meter_time ON meterstanden(time);`
3. Alleen benodigde kolommen ophalen (nu al dynamisch via env mapping).
4. Draai container met hogere CPU share: in `docker-compose.yml` services.nilm: `cpu_shares: 2048`.
5. I/O scheduling: start container met `--device-read-bps` / `--device-write-bps` indien nodig of gebruik `ionice` wrapper.
6. Meerdere workers: `UVICORN_WORKERS=2` bij CPU-bound clustering; voor nu 1 is voldoende.

### Beperken van data (snellere start)
Endpoint `/scan` haalt standaard alleen de laatste 7 dagen op (instelbaar via query parameter `last_days` max 30). Voorbeeld:
```
GET /scan?last_days=5
```
Gebruik `LOOKBACK_DAYS` in `.env` om de default te wijzigen.

### Clustering tuning
Env variabelen:
```
CLUSTER_EPS=0.55            # radius in gestandaardiseerde feature space
CLUSTER_MIN_SAMPLES=8       # minimum events per cluster
```
Output van `/scan` bevat nu `clusters` met per cluster: count, gemiddelde stap (`avg_dP_kW`), mediane duur en gemiddelde ruwe energie (`avg_energy_kWh`). Alles met label -1 is "noise" volgens DBSCAN.

Event energie (benadering) = stapvermogen * duur (rechthoek). Voor apparaten met pulsen (wasmachine) wordt dit onderschat; later kunnen we energie integreren over de periode.

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
Volledig automatische build & deploy bij iedere push naar `main` of `init` via één workflow: **Build and Deploy (Unified)**.

### Belangrijk over netwerk (timeout / i/o timeout)
GitHub *gehoste* runners kunnen je interne LAN (192.168.x.x) meestal niet bereiken → `i/o timeout` bij de SSH stap. Twee oplossingen:

| Methode | Beschrijving | Pro | Con |
|--------|--------------|-----|-----|
| Pull-model (self-hosted runner) | Raspberry Pi draait een self-hosted GitHub Actions runner en trekt zelf het image | Geen inbound poorten/openingen nodig | Runner onderhouden op de Pi |
| Push via publiek bereikbare SSH | Pi publiek bereikbaar of via port-forward/VPN | Geen runner installatie | Netwerk/openbaarheid & security complexities |

Workflow stappen (samengevat):
1. Build (multi-arch amd64/arm64) → push naar GHCR (`:<branch>-latest` + `:<branch>-<sha7>`)
2. Self‑hosted Pi runner forceert direct een redeploy met `scripts/deploy.sh --force`
3. Container start opnieuw (ongeacht digest) → `/status` toont nieuwe `git_sha`

### Quick Start (eerste keer self-hosted runner)
1. Registration token: Repo → Settings → Actions → Runners → New self-hosted runner.
2. Op de Pi installeer runner + docker (zie onder). Zorg dat labels o.a. `pi` bevatten.
3. Push een commit → workflow bouwt & deployt.
4. Controle: `curl -s http://<pi-ip>:8001/status | jq .git_sha`

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

### Force / handmatig triggers
Normaal niet nodig. Opties als je echt wilt:
1. UI: Run workflow (workflow_dispatch)
2. Dummy commit push
3. Handmatig script: `scripts/deploy.sh --branch init --force`
4. Lokaal multi-arch build & push; daarna script draaien

Verificatie checklist:
```
docker pull ghcr.io/waariswallie/nilm-app:init-latest
docker buildx imagetools inspect ghcr.io/waariswallie/nilm-app:init-latest | grep -E "Platform|Name"
ssh pi@<host> docker ps --filter name=nilm-app-init
curl -s http://<pi-ip>:8001/health
```

Alle redeploys zijn forced (digest wordt genegeerd) in de unified workflow.

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

## Data API

| Endpoint | Doel | Belangrijkste velden |
|----------|------|----------------------|
| `/health` | Liveness | `{ok:true}` |
| `/status` | Basis info + kolommen + span | `mock_db`, `span` |
| `/scan` | Gecombineerde scan (laatste events + baseload) | `n_points`, `n_events`, `baseload`, `events` |
| `/series` | Pnet tijdreeks (optioneel downsample) | `timestamps[]`, `pnet_kW[]`, `points` |
| `/events` | Event lijst (limit param) | `count`, `events[]` |
| `/clusters` | Cluster samenvatting | `clusters[]` |

Voorbeelden:
```bash
curl -s "http://localhost:8000/series?last_days=2&downsample=5" | jq '.points'
curl -s "http://localhost:8000/events?last_days=2&limit=20" | jq '.count'
curl -s "http://localhost:8000/clusters" | jq
```
