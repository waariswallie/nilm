"""Utilities for loading power data from MariaDB for NILM analysis.

This module is intentionally light-weight: all DB connection details are
read from environment variables (possibly populated via ``python-dotenv``).
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterable, Iterator, Optional

import os

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
import pandas as pd

DEFAULT_TABLE = "MeterData"
DEFAULT_TIME_COLUMN = "timestamp"
DEFAULT_PHASE_COLUMNS = {
    "l1": "instantaneous_active_power_l1_kW",
    "l2": "instantaneous_active_power_l2_kW",
    "l3": "instantaneous_active_power_l3_kW",
}


def load_environment(dotenv_path: Optional[str] = None) -> None:
    """Load environment variables from ``.env`` if present."""
    load_dotenv(dotenv_path=dotenv_path, override=False)


@dataclass(slots=True)
class DBConfig:
    host: str
    port: int
    user: str
    password: str
    database: str

    table: str = DEFAULT_TABLE
    time_column: str = DEFAULT_TIME_COLUMN

    phase_columns: dict[str, str] = None  # type: ignore[assignment]
    delivered_column: Optional[str] = "actual_electricity_power_delivered_kW"
    received_column: Optional[str] = "actual_electricity_power_received_kW"

    @classmethod
    def from_env(cls) -> "DBConfig":
        load_environment()
        host = os.getenv("DB_HOST", "127.0.0.1")
        port = int(os.getenv("DB_PORT", "3306"))
        user = os.getenv("DB_USER", "root")
        password = os.getenv("DB_PASS", "")
        database = os.getenv("DB_NAME", "nilm")
        table = os.getenv("DB_TABLE", DEFAULT_TABLE)
        time_column = os.getenv("DB_TIME_COLUMN", DEFAULT_TIME_COLUMN)
        phase_columns = {
            "l1": os.getenv("DB_PHASE_L1", DEFAULT_PHASE_COLUMNS["l1"]),
            "l2": os.getenv("DB_PHASE_L2", DEFAULT_PHASE_COLUMNS["l2"]),
            "l3": os.getenv("DB_PHASE_L3", DEFAULT_PHASE_COLUMNS["l3"]),
        }
        delivered = os.getenv("DB_POWER_DELIVERED", "actual_electricity_power_delivered_kW")
        received = os.getenv("DB_POWER_RECEIVED", "actual_electricity_power_received_kW")
        delivered = delivered or None
        received = received or None
        return cls(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            table=table,
            time_column=time_column,
            phase_columns=phase_columns,
            delivered_column=delivered,
            received_column=received,
        )

    @property
    def url(self) -> str:
        return (
            f"mariadb+mariadbconnector://{self.user}:{self.password}"
            f"@{self.host}:{self.port}/{self.database}"
        )


@contextmanager
def db_engine(config: Optional[DBConfig] = None) -> Iterator[Engine]:
    cfg = config or DBConfig.from_env()
    engine = create_engine(cfg.url, pool_pre_ping=True, pool_recycle=3600)
    try:
        yield engine
    finally:
        engine.dispose()


def _clean_numeric(series: pd.Series) -> pd.Series:
    """Convert strings with comma decimals to floats."""
    if series.dtype.kind in ("f", "i"):
        return series.astype(float)
    return pd.to_numeric(series.astype(str).str.replace(",", ".", regex=False), errors="coerce")


def _clean_frame(df: pd.DataFrame, columns: Iterable[str]) -> pd.DataFrame:
    for col in columns:
        if col in df.columns:
            df[col] = _clean_numeric(df[col])
    return df


def load_power_data(
    start: str,
    end: str,
    config: Optional[DBConfig] = None,
    columns: Optional[Iterable[str]] = None,
    tz: Optional[str] = None,
) -> pd.DataFrame:
    """Load power data from ``start`` to ``end``.

    Parameters
    ----------
    start, end:
        ISO8601 timestamps or anything SQLAlchemy can parse in a bound query.
    columns:
        Extra columns to select. Phase columns and time column are included automatically.
    tz:
        Optional timezone to localise the returned datetime index.
    """
    cfg = config or DBConfig.from_env()
    base_cols = list({cfg.time_column, *cfg.phase_columns.values()})
    if cfg.delivered_column:
        base_cols.append(cfg.delivered_column)
    if cfg.received_column:
        base_cols.append(cfg.received_column)
    if columns:
        base_cols.extend(columns)
    col_list = ",".join(f"`{c}`" for c in base_cols)
    query = text(
        f"SELECT {col_list} FROM `{cfg.table}` "
        f"WHERE `{cfg.time_column}` >= :start AND `{cfg.time_column}` < :end "
        f"ORDER BY `{cfg.time_column}`"
    )
    with db_engine(cfg) as engine:
        df = pd.read_sql_query(query, engine, params={"start": start, "end": end})
    if df.empty:
        return df
    df = _clean_frame(df, base_cols)
    df.rename(columns={cfg.time_column: "timestamp"}, inplace=True)
    df.set_index(pd.to_datetime(df["timestamp"], utc=True), inplace=True)
    df.index.name = "timestamp"
    df.drop(columns=["timestamp"], inplace=True)
    if tz:
        df.index = df.index.tz_convert(tz)
    return df


__all__ = ["DBConfig", "load_power_data", "load_environment", "db_engine"]
