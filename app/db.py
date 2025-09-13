from __future__ import annotations
import os
import math
import random
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


def _synthetic_cumulative(days: int) -> pd.DataFrame:
    """Generate synthetic cumulative kWh data for mocking.

    - Creates minute index for given number of days ending 'now'.
    - Simulates a baseload + random device pulses (washer, kettle, etc.).
    - Returns cumulative columns p1,p2,n1,n2,(L1_kwh,L2_kwh,L3_kwh).
    """
    tz = "Europe/Amsterdam"
    periods = days * 24 * 60
    idx = pd.date_range(end=pd.Timestamp.now(tz=tz), periods=periods, freq="T")
    # Start with baseload ~0.25 kW fluctuating
    base = 0.25 + 0.05 * pd.Series([math.sin(i/180) for i in range(periods)], index=idx)
    noise = pd.Series([random.uniform(-0.02, 0.02) for _ in range(periods)], index=idx)
    power = base + noise

    # Add synthetic pulses (e.g. kettle ~2 kW, short; washer waves)
    rng = random.Random(42)
    for _ in range(15):
        start_i = rng.randrange(0, periods - 10)
        dur = rng.randrange(2, 6)
        power.iloc[start_i:start_i+dur] += 2.0  # kettle
    for _ in range(5):
        start_i = rng.randrange(0, periods - 180)
        dur = rng.randrange(60, 150)
        wave = 1.5 * abs(pd.Series([math.sin(j/15) for j in range(dur)], index=power.index[start_i:start_i+dur]))
        power.iloc[start_i:start_i+dur] += wave.values

    power = power.clip(lower=0)
    # Convert kW minute avg to cumulative kWh
    kwh_increment = power / 60.0
    cum = kwh_increment.cumsum()
    df = pd.DataFrame({
        "p1": cum,  # all consumption into p1 for simplicity
        "p2": cum*0,  # zero
        "n1": cum*0,  # no export
        "n2": cum*0,
        "L1_kwh": cum,
        "L2_kwh": cum*0,
        "L3_kwh": cum*0,
    }, index=idx)
    df.index.name = "ts"
    return df.reset_index()


def fetch_minute_power(start_ts: str | None = None, end_ts: str | None = None) -> pd.DataFrame:
    """Fetch cumulative meter readings and convert to power (kW).

    If MOCK_DB=1 (settings.mock_db) a synthetic dataset is returned instead of querying MariaDB.
    """
    if settings.mock_db:
        days = settings.lookback_days
        df = _synthetic_cumulative(days)
    else:
        q = """
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

    df["ts"] = pd.to_datetime(df["ts"], utc=True, errors="coerce").dt.tz_convert("Europe/Amsterdam")
    df = df.dropna(subset=["ts"]).set_index("ts").sort_index()

    # Convert cumulative kWh to kW average over the minute
    for col in ["p1", "p2", "n1", "n2", "L1_kwh", "L2_kwh", "L3_kwh"]:
        if col in df.columns:
            df[col] = df[col].astype(float)
    df["Pin"] = (df["p1"].diff().fillna(0) + df["p2"].diff().fillna(0)) * 60.0
    df["Pout"] = (df["n1"].diff().fillna(0) + df["n2"].diff().fillna(0)) * 60.0

    if {"L1_kwh", "L2_kwh", "L3_kwh"}.issubset(df.columns):
        for ph in ["L1_kwh", "L2_kwh", "L3_kwh"]:
            df[ph.replace("_kwh", "")] = df[ph].diff().fillna(0) * 60.0

    df["Pnet"] = (df["Pin"] - df["Pout"]).clip(lower=-10, upper=15)
    return df
