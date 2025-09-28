"""Event detection utilities for NILM."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

import pandas as pd

from .prep import PHASE_ORDER


@dataclass(slots=True)
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

    def to_dict(self) -> Dict[str, float | str]:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "phase": self.phase,
            "delta_kw": float(self.delta_kw),
            "mean_kw": float(self.mean_kw),
            "duration_s": float(self.duration_s),
            "energy_kwh": float(self.energy_kwh),
            "peak_kw": float(self.peak_kw),
            "baseline_kw": float(self.baseline_kw),
        }


@dataclass(slots=True)
class EventConfig:
    min_event_kw: float = 0.5
    min_duration_s: float = 60.0
    hysteresis_kw: float = 0.1
    max_gap_s: float = 600.0


def _find_runs(mask: pd.Series) -> List[tuple[int, int]]:
    runs: List[tuple[int, int]] = []
    start: int | None = None
    for idx, flag in enumerate(mask.to_numpy()):
        if flag and start is None:
            start = idx
        elif not flag and start is not None:
            runs.append((start, idx))
            start = None
    if start is not None:
        runs.append((start, len(mask)))
    return runs


def _phase_events(series: pd.Series, cfg: EventConfig, sample_seconds: float) -> List[Event]:
    if series.empty:
        return []
    values = series.ffill().bfill().astype(float)
    baseline = float(values.median())
    threshold = baseline + cfg.min_event_kw
    active = values >= threshold
    min_len = max(int(round(cfg.min_duration_s / sample_seconds)), 1)
    events: List[Event] = []
    for start_idx, end_idx in _find_runs(active):
        span = end_idx - start_idx
        if span < min_len:
            continue
        window = values.iloc[start_idx:end_idx]
        start_ts = window.index[0]
        # treat end timestamp as exclusive upper bound for readability
        end_ts = window.index[-1] + pd.to_timedelta(sample_seconds, unit="s")
        duration_s = span * sample_seconds
        peak_kw = float(window.max())
        mean_kw = float(window.mean())
        delta_kw = float(peak_kw - baseline)
        energy_kwh = float(window.sum() * sample_seconds / 3600.0)
        events.append(
            Event(
                start=start_ts,
                end=end_ts,
                phase=str(series.name or ""),
                delta_kw=delta_kw,
                mean_kw=mean_kw,
                duration_s=duration_s,
                energy_kwh=energy_kwh,
                peak_kw=peak_kw,
                baseline_kw=baseline,
            )
        )
    return events


def detect_events(df: pd.DataFrame, config: EventConfig | None = None) -> List[Event]:
    if df.empty:
        return []
    cfg = config or EventConfig()
    # derive sampling interval
    if len(df.index) >= 2:
        sample_seconds = (df.index[1] - df.index[0]).total_seconds() or 60.0
    else:
        sample_seconds = 60.0
    if sample_seconds <= 0:
        sample_seconds = 60.0

    collected: List[Event] = []
    for phase in PHASE_ORDER:
        if phase not in df.columns:
            continue
        collected.extend(_phase_events(df[phase], cfg, sample_seconds))
    collected.sort(key=lambda e: e.start)
    return collected


def events_to_frame(events: List[Event]) -> pd.DataFrame:
    if not events:
        return pd.DataFrame(
            columns=[
                "start",
                "end",
                "phase",
                "delta_kw",
                "mean_kw",
                "duration_s",
                "energy_kwh",
                "peak_kw",
                "baseline_kw",
            ]
        )
    return pd.DataFrame([e.to_dict() for e in events])


__all__ = ["Event", "EventConfig", "detect_events", "events_to_frame"]
