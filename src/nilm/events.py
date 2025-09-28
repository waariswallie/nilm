from __future__ import annotations

from dataclasses import dataclass
from typing import List

import pandas as pd


@dataclass
class EventConfig:
    min_event_kw: float = 0.5
    min_duration_s: int = 60


@dataclass
class Event:
    start: pd.Timestamp
    end: pd.Timestamp
    phase: str
    delta_kw: float
    mean_kw: float
    duration_s: float
    energy_kwh: float
    peak_kw: float
    baseline_kw: float


def _contiguous_runs(mask: pd.Series) -> List[tuple[int, int]]:
    runs: List[tuple[int, int]] = []
    start: int | None = None
    for idx, flag in enumerate(mask.to_numpy()):
        if flag:
            if start is None:
                start = idx
        else:
            if start is not None:
                runs.append((start, idx))
                start = None
    if start is not None:
        runs.append((start, len(mask)))
    return runs


def detect_events(df: pd.DataFrame, config: EventConfig | None = None) -> List[Event]:
    if df.empty:
        return []

    cfg = config or EventConfig()
    phases = [c for c in df.columns if c.endswith("_kw") and c[0] == "l"]
    if not phases:
        phases = [c for c in df.columns if c.endswith("_kw") and c != "total_kw"]

    if len(df.index) < 2:
        return []

    freq = (df.index[1] - df.index[0]).total_seconds()
    if freq <= 0:
        freq = df.attrs.get("sample_seconds", 60)
    min_len = max(int(round(cfg.min_duration_s / freq)), 1)

    events: List[Event] = []
    for phase_col in phases:
        phase = phase_col.split("_", 1)[0]
        series = df[phase_col].ffill().fillna(0)
        baseline = float(series.median())
        diff = series - baseline
        mask = diff >= cfg.min_event_kw
        if not mask.any():
            continue
        for start_idx, end_idx in _contiguous_runs(mask):
            span = end_idx - start_idx
            if span < min_len:
                continue
            slice_ = series.iloc[start_idx:end_idx]
            start_ts = slice_.index[0]
            end_ts = slice_.index[-1]
            duration_s = span * freq
            delta_kw = float((slice_.max() - baseline))
            mean_kw = float(slice_.mean())
            peak_kw = float(slice_.max())
            energy_kwh = float(slice_.sum() * freq / 3600)
            events.append(
                Event(
                    start=start_ts,
                    end=end_ts,
                    phase=phase,
                    delta_kw=delta_kw,
                    mean_kw=mean_kw,
                    duration_s=duration_s,
                    energy_kwh=energy_kwh,
                    peak_kw=peak_kw,
                    baseline_kw=baseline,
                )
            )
    events.sort(key=lambda e: e.start)
    return events
