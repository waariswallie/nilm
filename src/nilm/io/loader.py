from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import pandas as pd

@dataclass
class MeterRecord:
    ts: pd.Timestamp
    power_w: float | None


def load_csv(path: str | Path, tz: str | None = "UTC") -> pd.DataFrame:
    """Load a CSV with columns: timestamp,power_w

    Returns a DataFrame indexed by timezone-aware Timestamp.
    """
    df = pd.read_csv(path)
    if "timestamp" not in df.columns:
        raise ValueError("CSV mist 'timestamp' kolom")
    if "power_w" not in df.columns:
        raise ValueError("CSV mist 'power_w' kolom")
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    if tz:
        df["timestamp"] = df["timestamp"].dt.tz_convert(tz)
    df = df.set_index("timestamp").sort_index()
    return df
