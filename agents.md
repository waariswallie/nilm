# agents.md — NILM pipeline (event-based disaggregation)

This document defines small, composable **agents** (processes or tasks) that together turn
minute-level (3‑phase) meter data into device‑level insights. Each agent has a clear role,
inputs/outputs, triggers, and success metrics. All agents run inside the same container by
default (FastAPI with background jobs), but can later be split into separate services.

---

## Architecture (high‑level)

```
MariaDB (cumulative kWh) → Ingestion → Cleaning → Event Detection → Phase Merge
→ Clustering → Labeling → (optional ML Disaggregation) → Evaluation → Reporting/API
                                      ↘ Baseload Tracker ↗
```

**Current repo mapping**
- `app/db.py`  → Ingestion
- `app/preprocessing.py` → Cleaning
- `app/events.py` → Event Detection
- `app/clustering.py` → Clustering (DBSCAN)
- `app/baseload.py` → Baseload Tracker
- `app/api.py` → API surface (`/scan`)
- (to add) `app/labeling.py`, `app/eval.py`, `app/train.py`, `app/report.py`

---

## Agent directory (TL;DR)

| Agent | Responsibility | Inputs | Outputs | Trigger | Owner module |
|---|---|---|---|---|---|
| Ingestion | Read cumulative kWh (and per‑phase) from MariaDB | SQL | Time‑indexed DataFrame with kW/min | On demand (`/scan`) or schedule | `db.py` |
| Cleaning | Clip/fill/filter series; derive `Pnet = Pin-Pout` | Raw DF | Clean DF | Downstream pull | `preprocessing.py` |
| Event Detection | Detect step changes (`ΔP` > threshold) & pair on/off | Clean DF | List of events | Downstream pull | `events.py` |
| Phase Merge | Enrich events with per‑phase deltas | Events + DF | Events(+L1/L2/L3) | Inside detector | `events.py` |
| Clustering | Group events into candidate device clusters | Events | `events`(+cluster id) | On demand | `clustering.py` |
| Labeling | Map clusters → device labels (rules + overrides) | Clustered events | Labeled clusters | Manual/On demand | *(planned)* |
| Baseload | Night-window median (02–05h) per day | Clean DF | `baseload_daily` | Daily | `baseload.py` |
| Ground Truth | Import smart‑plug logs to align/validate | CSV/API | Device on/off spans | Manual | *(planned)* |
| ML Disagg | Train/infer per‑device models (seq2point/HMM) | Clean DF + labels | Per‑device power series | Manual/schedule | *(planned)* |
| Evaluation | MAE/Energy accuracy per device; drift alerts | Predictions + truth | Metrics | Post‑run | *(planned)* |
| Reporting | Daily/weekly summaries & exports | DB artifacts | JSON/CSV/plots | Cron/schedule | *(planned)* |

---

## Interfaces & Schemas

### Event object (JSON)
```json
{
  "t_on": "2025-09-10T18:42:00+02:00",
  "t_off": "2025-09-10T19:55:00+02:00",
  "dP_on_kW": 1.95,
  "dP_phase_kW": [1.95, 0.0, 0.0],
  "duration_min": 73,
  "cluster": 3
}
```
- **Units:** kW for powers; minutes for durations; timestamps in Europe/Amsterdam.
- `cluster` is `-1` for noise (DBSCAN convention) or `null` when unassigned.

### Cluster label object (JSON)
```json
{
  "cluster": 3,
  "label": "Quooker",
  "rule": {
    "dP_on_kW_min": 1.5,
    "dP_on_kW_max": 2.3,
    "phase": [true, false, false],
    "duration_min_max": [0.5, 5]
  },
  "source": "rule|manual|plug|model",
  "confidence": 0.86,
  "updated_at": "2025-09-11T20:05:00+02:00"
}
```

### Proposed API endpoints (to add)
- `GET /events?from=...&to=...` → paginated events
- `GET /clusters` → list clusters + label summaries
- `POST /labels` → set/override label for a cluster
- `GET /baseload?days=30` → daily baseload series
- `POST /train/{device}` → start training job (seq2point/HMM)
- `GET /metrics` → latest MAE/energy‑accuracy per device
- `GET /export/{kind}` → CSV/JSON dumps (`events`, `clusters`, `predictions`)

---

## Configuration (env)
- `EVENT_WATT_THRESHOLD` (default 500) — step size for on/off detection.
- `MIN_EVENT_DURATION_MIN` / `MAX_EVENT_DURATION_MIN` — pairing window.
- `LOOKBACK_DAYS` — default window for scans.
- (optional) `NIGHT_WINDOW=02-05` for baseload.

**DB user** should be read‑only. Timezone set to EU/Amsterdam; beware DST transitions.

---

## Data model (proposed MariaDB tables)

> These are suggestions; you can also keep artifacts in CSV/Parquet. All powers in kW.

```sql
CREATE TABLE IF NOT EXISTS events (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  t_on DATETIME NOT NULL,
  t_off DATETIME NULL,
  dP_on_kW DOUBLE NOT NULL,
  dP_L1_kW DOUBLE NULL,
  dP_L2_kW DOUBLE NULL,
  dP_L3_kW DOUBLE NULL,
  duration_min DOUBLE NULL,
  cluster INT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  INDEX (t_on), INDEX (t_off), INDEX (cluster)
);

CREATE TABLE IF NOT EXISTS clusters (
  cluster INT PRIMARY KEY,
  label VARCHAR(64) NULL,
  confidence DOUBLE NULL,
  source ENUM('rule','manual','plug','model') NULL,
  updated_at TIMESTAMP NULL
);

CREATE TABLE IF NOT EXISTS baseload_daily (
  day DATE PRIMARY KEY,
  baseload_kW DOUBLE NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS predictions (
  ts DATETIME NOT NULL,
  device VARCHAR(64) NOT NULL,
  p_kW DOUBLE NOT NULL,
  method VARCHAR(32) NOT NULL,
  confidence DOUBLE NULL,
  PRIMARY KEY (ts, device)
);
```

---

## Scheduling & orchestration
- **Now (simple):** Trigger on demand via `/scan`. Use host `cron` to hit `/scan` hourly.
- **Later (in‑container):** Add APScheduler to FastAPI for `scan_hourly`, `baseload_daily`, `report_daily`.
- **Idempotency:** Agents must tolerate re‑runs (upserts by `t_on`/`day`).

Example cron on the host:
```
*/30 * * * * curl -s http://nilm:8000/scan > /dev/null
0 5 * * * curl -s "http://nilm:8000/baseload?days=60" > /dev/null
```

---

## Quality gates & metrics
- **Event precision/recall** on plug‑labeled spans.
- **Energy accuracy** per device/day: `1 - |E_true - E_pred| / E_true`.
- **Baseload trend**: 30‑d median; alert if +20% WoW.
- **Drift**: distribution shift in `dP_on_kW` or durations (KS test) → suggest re‑label/re‑train.

---

## Edge cases & rules of thumb
- PV overshading: use `Pnet = Pin - Pout` to avoid masking.
- Inductie/oven moduleren: meerdere kleinere `ΔP`; allow burst grouping (≤3 min).
- Wasmachine/vaatwasser: pulsen (verwarming) + lange duur; label via pattern not only size.
- DST switches: store UTC internally; present in EU/Amsterdam.
- Meter rollovers/missing minutes: interpolate ≤3 min; otherwise gapmark.

---

## Playbooks (copy‑paste)

**Run a 7‑day scan and dump events to CSV**
```
curl -s "http://localhost:8000/scan?last_days=7" | jq '.events' > events.json
jq -r '(["t_on","t_off","dP_on_kW","duration_min","cluster"]) as $h | ($h | @csv), (.[] | [._time? // .t_on, .t_off, .dP_on_kW, .duration_min, .cluster] | @csv)' events.json > events.csv
```

**Label cluster 3 as Quooker (future endpoint)**
```
curl -X POST http://localhost:8000/labels \
 -H 'Content-Type: application/json' \
 -d '{"cluster":3, "label":"Quooker", "source":"manual", "confidence":0.9}'
```

**Train Quooker model (future endpoint)**
```
curl -X POST http://localhost:8000/train/Quooker -d '{"days":21}'
```

---

## Roadmap (incremental)
1) Add `labeling.py` (rule‑based) + `/events` & `/clusters` endpoints.
2) Add CSV export + `baseload` endpoint.
3) Plug import + evaluation metrics.
4) Optional ML (seq2point or HMM) per device.
