"""Pre-processing helpers for NILM data.

Responsibilities:
- ensure minute-level sampling
- smooth noisy signals via rolling median
- compute per-phase totals respecting DSMR wiring layout
- generate convenience columns for later event detection
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

import pandas as pd

PHASE_ORDER = ["l1", "l2", "l3"]

# DSMR mapping groups → phases (as provided in the prompt)
GROUP_TO_PHASE = {
    1: "l3",
    2: "l3",
    3: "l3",
    4: "l3",
    5: "l2",
    6: "l2",
    7: "l2",
    8: "l2",
    9: "l1",
    10: "l1",
    11: "l1",
    12: "l1",
    13: "solar",
}


@dataclass(slots=True)
class PrepConfig:
    resample_seconds: int = 60
    smooth_seconds: int = 60
    max_gap_minutes: int = 3


def _ensure_numeric(df: pd.DataFrame) -> pd.DataFrame:
    return df.apply(pd.to_numeric, errors="coerce")


def resample_and_smooth(
    df: pd.DataFrame,
    config: PrepConfig | None = None,
) -> pd.DataFrame:
    if df.empty:
        return df
    cfg = config or PrepConfig()
    freq = f"{cfg.resample_seconds}s"
    df = df.sort_index()
    df = _ensure_numeric(df)
    df_resampled = df.resample(freq).mean()
    df_resampled = df_resampled.interpolate(limit=cfg.max_gap_minutes, limit_direction="both")
    window = max(int(cfg.smooth_seconds / cfg.resample_seconds), 1)
    smoothed = df_resampled.rolling(window=window, center=True, min_periods=1).median()
    smoothed = smoothed.ffill().bfill()
    return smoothed


def compute_net_columns(
    df: pd.DataFrame,
    delivered_col: str | None = "actual_electricity_power_delivered_kW",
    received_col: str | None = "actual_electricity_power_received_kW",
) -> pd.DataFrame:
    out = df.copy()
    if delivered_col and delivered_col in out.columns:
        out[delivered_col] = pd.to_numeric(out[delivered_col], errors="coerce")
    if received_col and received_col in out.columns:
        out[received_col] = pd.to_numeric(out[received_col], errors="coerce")
    if delivered_col and delivered_col in out.columns:
        out["total_import_kW"] = out[delivered_col]
    if received_col and received_col in out.columns:
        out["total_export_kW"] = out[received_col]
    if {"total_import_kW", "total_export_kW"}.issubset(out.columns):
        imp = out.get("total_import_kW", pd.Series(0, index=out.index)).fillna(0)
        exp = out.get("total_export_kW", pd.Series(0, index=out.index)).fillna(0)
        out["net_total_kW"] = (imp - exp).astype(float)
    return out


def ensure_phase_columns(
    df: pd.DataFrame,
    phase_columns: Dict[str, str],
) -> pd.DataFrame:
    out = df.copy()
    for phase, col in phase_columns.items():
        if col not in out.columns:
            raise KeyError(f"Expected column '{col}' for phase '{phase}' not found in data frame")
        out[phase] = pd.to_numeric(out[col], errors="coerce")
    return out


def add_group_hints(df: pd.DataFrame) -> pd.DataFrame:
    """Add helper columns describing which DSMR groups likely feed each phase."""
    group_phase = pd.Series(GROUP_TO_PHASE).to_frame("phase")
    group_phase["group"] = group_phase.index
    hints = {phase: group_phase[group_phase["phase"] == phase]["group"].tolist() for phase in ("l1", "l2", "l3")}
    df.attrs["phase_groups"] = hints
    return df


def preprocess(
    df: pd.DataFrame,
    phase_columns: Optional[Dict[str, str]] = None,
    config: Optional[PrepConfig] = None,
    delivered_col: Optional[str] = None,
    received_col: Optional[str] = None,
) -> pd.DataFrame:
    """Full preprocessing pipeline:
    - rename/ensure phase columns (l1/l2/l3)
    - resample to minute resolution and smooth using rolling median
    - compute total/net columns (if delivered/received present)
    - attach hints about group-to-phase mapping via DataFrame attrs
    """
    phase_columns = phase_columns or {
        "l1": "instantaneous_active_power_l1_kW",
        "l2": "instantaneous_active_power_l2_kW",
        "l3": "instantaneous_active_power_l3_kW",
    }
    df = ensure_phase_columns(df, phase_columns)
    df = resample_and_smooth(df, config=config)
    df = compute_net_columns(df, delivered_col, received_col)
    df = add_group_hints(df)
    return df


__all__ = [
    "PrepConfig",
    "preprocess",
    "resample_and_smooth",
    "compute_net_columns",
    "ensure_phase_columns",
    "PHASE_ORDER",
    "GROUP_TO_PHASE",
]
