from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import pandas as pd


@dataclass
class LoaderConfig:
    """Configuration for simple CSV/iterable loading."""

    tz: str | None = "Europe/Amsterdam"
    timestamp_column: str = "timestamp"
    value_columns: Sequence[str] | None = None


def _clean_numeric(series: pd.Series) -> pd.Series:
    """Convert numeric strings (comma decimals) to floats.

    Parameters
    ----------
    series:
        Input series containing numeric values as floats/ints/strings.

    Returns
    -------
    pandas.Series
        Series converted to floats where possible. Invalid entries become NaN.
    """

    if series.empty:
        return series.astype(float)

    cleaned = series.astype(str)
    cleaned = cleaned.str.replace(" ", "", regex=False)
    cleaned = cleaned.str.replace(",", ".", regex=False)
    cleaned = cleaned.replace({"nan": pd.NA, "None": pd.NA, "": pd.NA})
    return pd.to_numeric(cleaned, errors="coerce")


def _prepare_frame(df: pd.DataFrame, config: LoaderConfig) -> pd.DataFrame:
    if config.timestamp_column not in df.columns:
        raise ValueError(f"Missing timestamp column '{config.timestamp_column}'")

    df = df.copy()
    df[config.timestamp_column] = pd.to_datetime(df[config.timestamp_column], utc=True, errors="coerce")
    df = df.dropna(subset=[config.timestamp_column])

    for col in df.columns:
        if col == config.timestamp_column:
            continue
        if df[col].dtype == object:
            df[col] = _clean_numeric(df[col])

    if config.tz:
        df[config.timestamp_column] = df[config.timestamp_column].dt.tz_convert(config.tz)

    df = df.set_index(config.timestamp_column).sort_index()
    if config.value_columns:
        missing = [c for c in config.value_columns if c not in df.columns]
        if missing:
            raise ValueError(f"Missing value columns {missing}")
        df = df[list(config.value_columns)].copy()
    return df


def load_csv(path: str | Path, config: LoaderConfig | None = None) -> pd.DataFrame:
    """Load a CSV file with timestamp + power columns."""

    cfg = config or LoaderConfig()
    df = pd.read_csv(Path(path))
    return _prepare_frame(df, cfg)


def load_records(records: Iterable[Mapping[str, object]], config: LoaderConfig | None = None) -> pd.DataFrame:
    """Load from an iterable of dict-like records."""

    cfg = config or LoaderConfig()
    df = pd.DataFrame.from_records(list(records))
    return _prepare_frame(df, cfg)
