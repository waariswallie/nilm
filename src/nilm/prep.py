from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

import pandas as pd

from .preprocessing import fill_gaps, resample, smooth


@dataclass
class PrepConfig:
    resample_seconds: int = 60
    smooth_seconds: int = 60
    fill_limit: int = 3
    min_phase_kw: float = 0.4


_PHASE_COL_TEMPLATE = "instantaneous_active_power_{phase}_kW"
_PHASES = ("l1", "l2", "l3")


def _detect_phase_columns(df: pd.DataFrame) -> Dict[str, str]:
    lookup = {}
    for phase in _PHASES:
        col = _PHASE_COL_TEMPLATE.format(phase=phase)
        if col in df.columns:
            lookup[phase] = col
    # fallback: use suffixes like '_l1_kW'
    if not lookup:
        for col in df.columns:
            lower = col.lower()
            for phase in _PHASES:
                if lower.endswith(f"_{phase}_kw"):
                    lookup[phase] = col
    return lookup


def preprocess(df: pd.DataFrame, config: PrepConfig | None = None) -> pd.DataFrame:
    """Basic preprocessing pipeline used in unit tests.

    Steps:
      * resample to a fixed interval
      * fill short gaps
      * smooth with moving average
      * compute total/net power
      * store phase activity hints in DataFrame.attrs
    """

    if df.empty:
        out = pd.DataFrame(columns=["total_kw"], index=pd.Index([], name="timestamp"))
        out.attrs["phase_groups"] = {}
        out.attrs["sample_seconds"] = config.resample_seconds if config else 60
        return out

    cfg = config or PrepConfig()

    work = df.copy()
    if not isinstance(work.index, pd.DatetimeIndex):
        raise ValueError("DataFrame index must be a DatetimeIndex")
    if work.index.tz is None:
        work.index = work.index.tz_localize("UTC")

    if cfg.resample_seconds:
        freq = f"{cfg.resample_seconds}s"
        work = resample(work, rule=freq)
    else:
        freq = pd.infer_freq(work.index) or "60s"
    sample_seconds = int(pd.Timedelta(freq).total_seconds())

    if cfg.fill_limit:
        work = fill_gaps(work, limit=cfg.fill_limit)
    if cfg.smooth_seconds and cfg.smooth_seconds > sample_seconds:
        window = max(int(round(cfg.smooth_seconds / sample_seconds)), 1)
        work = smooth(work, window)

    phase_cols = _detect_phase_columns(work)
    if not phase_cols:
        raise ValueError("No phase power columns detected in input DataFrame")

    out = pd.DataFrame(index=work.index)
    for phase, col in phase_cols.items():
        out[f"{phase}_kw"] = work[col]
    out["total_kw"] = out[[c for c in out.columns if c.endswith("_kw")]].sum(axis=1)
    out["baseline_kw"] = out["total_kw"].rolling(window=30, min_periods=1).median()
    out["net_kw"] = out["total_kw"] - out["baseline_kw"]

    phase_groups: Dict[str, List[int]] = {}
    for phase, col in phase_cols.items():
        series = out[f"{phase}_kw"].fillna(0)
        baseline = float(series.median())
        active = series >= baseline + cfg.min_phase_kw
        if active.any():
            minutes = sorted(set(active[active].index.minute))
            if minutes:
                phase_groups[phase] = minutes

    out.attrs["phase_groups"] = phase_groups
    out.attrs["sample_seconds"] = sample_seconds
    return out
```,