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

    lookback_days: int = int(os.getenv("LOOKBACK_DAYS", 7))
    event_watt_threshold: float = float(os.getenv("EVENT_WATT_THRESHOLD", 500))
    min_event_duration_min: int = int(os.getenv("MIN_EVENT_DURATION_MIN", 2))
    max_event_duration_min: int = int(os.getenv("MAX_EVENT_DURATION_MIN", 240))

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
