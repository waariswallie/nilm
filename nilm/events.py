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
    min_event_kw: float = 0.2
    hysteresis_kw: float = 0.1
    min_duration_s: float = 30.0
    max_gap_s: float = 300.0


def _phase_events(series: pd.Series, cfg: EventConfig) -> List[Event]:
    if series.empty:
        return []
    idx = series.index
    diff = series.diff().fillna(0).astype(float)
    zscore = (diff - float(diff.mean())) / (float(diff.std(ddof=1)) + 1e-6)
    is_edge = (diff >= cfg.min_event_kw) & (zscore > 1.0)
    events: List[Event] = []
    i = 0
    n = len(series)
    while i < n:
        if not is_edge.iat[i]:
            i += 1
            continue
        start_idx = i
        start_time = pd.Timestamp(idx[start_idx])
        baseline = float(series.iat[start_idx - 1]) if start_idx > 0 else float(series.iat[start_idx] - diff.iat[start_idx])
        delta_kw = float(diff.iat[start_idx])
        j = start_idx + 1
        found = False
        while j < n:
            if diff.iat[j] <= -(delta_kw - cfg.hysteresis_kw):
                end_time = pd.Timestamp(idx[j])
                duration_s = (end_time - start_time).total_seconds() or cfg.min_duration_s
                if duration_s >= cfg.min_duration_s:
                    slice_series = series.iloc[start_idx : j + 1]
                    peak = float(slice_series.max())
                    mean_kw = float(slice_series.mean())
                    energy = max(delta_kw, mean_kw - baseline) * duration_s / 3600.0
                    events.append(
                        Event(
                            start=start_time,
                            end=end_time,
                            phase=str(series.name or ""),
                            delta_kw=float(delta_kw),
                            mean_kw=float(mean_kw),
                            duration_s=float(duration_s),
                            energy_kwh=float(energy),
                            peak_kw=peak,
                            baseline_kw=float(baseline),
                        )
                    )
                found = True
                i = j
                break
            if (pd.Timestamp(idx[j]) - start_time).total_seconds() > cfg.max_gap_s:
                break
            j += 1
        if not found:
            i += 1
        else:
            i += 1
    return events


def detect_events(df: pd.DataFrame, config: EventConfig | None = None) -> List[Event]:
    cfg = config or EventConfig()
    collected: List[Event] = []
    for phase in PHASE_ORDER:
        if phase not in df.columns:
            continue
        events = _phase_events(df[phase], cfg)
        collected.extend(events)
    collected.sort(key=lambda e: e.start)
    return collected


def events_to_frame(events: List[Event]) -> pd.DataFrame:
    if not events:
        return pd.DataFrame(columns=[
            "start",
            "end",
            "phase",
            "delta_kw",
            "mean_kw",
            "duration_s",
            "energy_kwh",
            "peak_kw",
            "baseline_kw",
        ])
    return pd.DataFrame([e.to_dict() for e in events])


__all__ = ["Event", "EventConfig", "detect_events", "events_to_frame"]
