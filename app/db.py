from __future__ import annotations
import mysql.connector as mysql
import pandas as pd
from .config import settings


def conn():
    return mysql.connect(
        host=settings.db_host,
        port=settings.db_port,
        user=settings.db_user,
        password=settings.db_pass,
        database=settings.db_name,
    )


def fetch_minute_power(start_ts: str | None = None, end_ts: str | None = None) -> pd.DataFrame:
    """Fetch cumulative meter readings and convert to power (kW). Adjust SQL to your schema."""
    q = f"""
    SELECT
      time as ts,
      p1, p2, n1, n2,
      L1_kwh, L2_kwh, L3_kwh
    FROM meterstanden
    WHERE (%(start)s IS NULL OR time >= %(start)s)
      AND (%(end)s   IS NULL OR time <  %(end)s)
    ORDER BY time
    """
    with conn() as c:
        df = pd.read_sql(q, c, params={"start": start_ts, "end": end_ts})
    df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert("Europe/Amsterdam")
    df = df.set_index("ts").sort_index()

    # Convert cumulative kWh to kW average over the minute
    for col in ["p1", "p2", "n1", "n2", "L1_kwh", "L2_kwh", "L3_kwh"]:
        if col in df.columns:
            df[col] = df[col].astype(float)
    df["Pin"] = (df["p1"].diff().fillna(0) + df["p2"].diff().fillna(0)) * 60.0
    df["Pout"] = (df["n1"].diff().fillna(0) + df["n2"].diff().fillna(0)) * 60.0

    # Per-phase if available
    if {"L1_kwh", "L2_kwh", "L3_kwh"}.issubset(df.columns):
        for ph in ["L1_kwh", "L2_kwh", "L3_kwh"]:
            df[ph.replace("_kwh", "")] = df[ph].diff().fillna(0) * 60.0

    df["Pnet"] = (df["Pin"] - df["Pout"]).clip(lower=-10, upper=15)
    return df
