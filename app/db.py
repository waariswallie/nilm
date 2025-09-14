from __future__ import annotations
import os
import math
import random
import mysql.connector as mysql
from mysql.connector import errors as mysql_errors
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
    """Fetch cumulative meter readings and convert to instantaneous (average kW/min) power.

    Supports flexible column names via env:
      TABLE_NAME, TIME_COLUMN, CONSUME_COLS, EXPORT_COLS, PHASE_KWH_COLS
    Falls back by stripping phase columns if they are missing.
    """
    if settings.mock_db:
        return _synthetic_cumulative(settings.lookback_days).pipe(_postprocess_frame)

    consume_cols = [c.strip() for c in settings.consume_cols.split(',') if c.strip()]
    export_cols = [c.strip() for c in settings.export_cols.split(',') if c.strip()]
    phase_cols = [c.strip() for c in settings.phase_kwh_cols.split(',') if c.strip()]

    # Build select list
    def build_query(include_phase: bool) -> str:
        cols = [settings.time_column + " as ts"]
        cols += consume_cols
        cols += export_cols
        if include_phase and phase_cols:
            cols += phase_cols
        col_sql = ",\n          ".join(cols)
        return f"""
        SELECT
          {col_sql}
        FROM {settings.table_name}
        WHERE (%(start)s IS NULL OR {settings.time_column} >= %(start)s)
          AND (%(end)s   IS NULL OR {settings.time_column} <  %(end)s)
        ORDER BY {settings.time_column}
        """

    params = {"start": start_ts, "end": end_ts}
    attempt_phase = True
    with conn() as c:
        try:
            q = build_query(include_phase=True)
            df = pd.read_sql(q, c, params=params)
        except Exception as e:  # noqa: BLE001 broad to catch pandas DatabaseError wrap
            msg = str(e)
            if 'Unknown column' in msg and phase_cols:
                attempt_phase = False
                q = build_query(include_phase=False)
                df = pd.read_sql(q, c, params=params)
            else:
                raise

    return _postprocess_frame(df, consume_cols, export_cols, phase_cols if attempt_phase else [])


def _postprocess_frame(df: pd.DataFrame, consume_cols: list[str] | None = None,
                       export_cols: list[str] | None = None, phase_cols: list[str] | None = None) -> pd.DataFrame:
    if df.empty:
        return df.set_index(pd.to_datetime([]))

    # Ensure tz-aware Europe/Amsterdam; handle already tz-aware values
    ts_series = pd.to_datetime(df["ts"], utc=True, errors="coerce")
    if isinstance(ts_series, pd.Series):
        try:
            ts_series = ts_series.dt.tz_convert("Europe/Amsterdam")
        except Exception:  # noqa: BLE001
            # If not tz-aware, localize first
            ts_series = ts_series.dt.tz_localize("UTC").dt.tz_convert("Europe/Amsterdam")
    df["ts"] = ts_series
    df = df.dropna(subset=["ts"]).set_index("ts").sort_index()

    consume_cols = consume_cols or [c for c in ["p1", "p2"] if c in df.columns]
    export_cols = export_cols or [c for c in ["n1", "n2"] if c in df.columns]
    phase_cols = phase_cols or []

    # Cast numeric
    for col in list(df.columns):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Compute sums of diffs*60 (kWh -> kW)
    def diff_sum(cols):
        if not cols:
            return pd.Series(0.0, index=df.index)
        acc = None
        for c in cols:
            if c in df.columns:
                series = df[c].diff().fillna(0) * 60.0
                acc = series if acc is None else (acc + series)
        return acc if acc is not None else pd.Series(0.0, index=df.index)

    df["Pin"] = diff_sum(consume_cols)
    df["Pout"] = diff_sum(export_cols)

    for ph in phase_cols:
        if ph in df.columns:
            name = ph.replace('_kwh', '').replace('_KWH', '')
            df[name] = df[ph].diff().fillna(0) * 60.0

    df["Pnet"] = (df["Pin"] - df["Pout"]).clip(lower=-10, upper=15)
    return df

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
