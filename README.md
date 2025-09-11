# NILM

Non-Intrusive Load Monitoring (NILM) toolkit / playground + een uitbreidbare event-based pipeline met FastAPI & clustering.

Doel: energieverbruik van individuele apparaten afleiden uit een totale (slimme meter) vermogens- of energie tijdreeks (minuut-resolutie, optioneel 3 fasen) en inzicht geven in baseload & apparaat events.

## Inhoud
1. Kern functionaliteit (oorspronkelijke playground)
2. Nieuwe containerized pipeline (`app/`):
   - Ingest MariaDB -> kW serie
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
Automatisch build & deploy bij push naar `main`:

1. GitHub Actions workflow `deploy.yml` bouwt een multi-arch image (amd64 + arm64) en pusht naar GHCR `ghcr.io/<owner>/nilm-app:latest`.
2. Tweede job maakt via SSH verbinding met je Raspberry Pi (bijv. `raspi52` op 192.168.0.70) en runt `docker compose up -d`.

### Vereiste GitHub Secrets
| Secret | Beschrijving |
|--------|--------------|
| `RPI_HOST` | IP of hostname (bv. 192.168.0.70) |
| `RPI_USER` | SSH user (bv. `pi`) |
| `RPI_SSH_KEY` | Private key (PEM) zonder passphrase |
| `RPI_PORT` | (optioneel) SSH poort, default 22 |

### Voorbereiden Raspberry Pi
```bash
sudo apt update && sudo apt install -y docker.io docker-compose-plugin
sudo usermod -aG docker $USER
mkdir -p ~/nilm
cd ~/nilm
cp /path/naar/.env .env   # vul env variabelen
```

Eerste deploy maakt automatisch een eenvoudige `docker-compose.yml` indien niet aanwezig.

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
