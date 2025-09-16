from __future__ import annotations
import os
from pathlib import Path
from pydantic import BaseModel
from dotenv import load_dotenv

# 1) Laad standaard .env (indien aanwezig)
load_dotenv(override=False)

# 2) Fallback: als DB_HOST nog op default staat en er is een sample-bestand,
#    laad DB_* variabelen uit `.env.sample` of `.env_sample` zonder bestaande waarden te overschrijven.
def _load_sample_env():
    if os.getenv("DB_HOST") not in (None, "127.0.0.1", "localhost"):  # al gezet → geen fallback
        return
    for fname in (".env.sample", ".env_sample"):
        if not os.path.exists(fname):
            continue
        try:
            with open(fname, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = [x.strip() for x in line.split("=", 1)]
                    if k.startswith("DB_") and not os.getenv(k):  # alleen DB_* en niet overschrijven
                        os.environ[k] = v
        except Exception:  # pragma: no cover - defensief, geen crash bij parse fouten
            pass
        break  # eerste gevonden sample is genoeg

_load_sample_env()


class Settings(BaseModel):
    db_host: str = os.getenv("DB_HOST", "127.0.0.1")
    db_port: int = int(os.getenv("DB_PORT", 3306))
    db_name: str = os.getenv("DB_NAME", "youless-smigp1")
    db_user: str = os.getenv("DB_USER", "root")
    db_pass: str = os.getenv("DB_PASS", "")

    # Flexible schema mapping (comma separated lists)
    table_name: str = os.getenv("TABLE_NAME", "meterstanden")
    time_column: str = os.getenv("TIME_COLUMN", "time")
    consume_cols: str = os.getenv("CONSUME_COLS", "p1,p2")
    export_cols: str = os.getenv("EXPORT_COLS", "n1,n2")
    phase_kwh_cols: str = os.getenv("PHASE_KWH_COLS", "L1_kwh,L2_kwh,L3_kwh")
    
    # Performance / scheduling
    @staticmethod
    def _int_env(name: str) -> int | None:
        val = os.getenv(name)
        if val is None or val == "":
            return None
        try:
            return int(val)
        except ValueError:
            return None

    nice_level: int | None = _int_env.__func__("NICE_LEVEL")  # type: ignore[attr-defined]
    cpu_affinity: str | None = os.getenv("CPU_AFFINITY")  # e.g. "0,1"

    # Default lookback (days) for /scan and other summaries. Hard cap enforced in API at 30 now.
    lookback_days: int = int(os.getenv("LOOKBACK_DAYS", 7))
    # Optional high‑resolution sampling interval (seconds) if raw 10s table is available.
    # If not set, system assumes only minute-level cumulative data.
    highres_interval_s: int | None = (lambda v: int(v) if v and v.isdigit() else None)(os.getenv("HIGHRES_INTERVAL_SECONDS", ""))
    highres_table: str | None = os.getenv("HIGHRES_TABLE", None)  # separate table for 10s samples (instant power)
    highres_time_column: str = os.getenv("HIGHRES_TIME_COLUMN", "ts")
    highres_phase_cols: str = os.getenv("HIGHRES_PHASE_COLS", "L1,L2,L3")  # instantaneous kW per phase if available
    highres_power_col: str = os.getenv("HIGHRES_POWER_COL", "Pnet")  # optional total net power; else sum phases
    event_watt_threshold: float = float(os.getenv("EVENT_WATT_THRESHOLD", 500))
    min_event_duration_min: int = int(os.getenv("MIN_EVENT_DURATION_MIN", 2))
    max_event_duration_min: int = int(os.getenv("MAX_EVENT_DURATION_MIN", 240))

    # Clustering parameters
    cluster_eps: float = float(os.getenv("CLUSTER_EPS", 0.55))
    cluster_min_samples: int = int(os.getenv("CLUSTER_MIN_SAMPLES", 8))

    dbconnect_path: Path = Path("/run/secrets/dbconnect.conf")
    mock_db: bool = bool(int(os.getenv("MOCK_DB", "0")))  # MOCK_DB=1 to use synthetic data

    def override_from_file(self):
        if self.dbconnect_path.exists():
            d = {}
            for line in self.dbconnect_path.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                k, v = [x.strip() for x in line.split("=", 1)]
                d[k.lower()] = v
            self.db_host = d.get("host", self.db_host)
            self.db_port = int(d.get("port", self.db_port))
            self.db_name = d.get("database", self.db_name)
            self.db_user = d.get("user", self.db_user)
            self.db_pass = d.get("password", self.db_pass)
        return self


settings = Settings().override_from_file()
