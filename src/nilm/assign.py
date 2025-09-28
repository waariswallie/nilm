from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Mapping

import pandas as pd

from .events import Event
from .signatures import best_device


@dataclass
class AssignmentConfig:
    min_confidence: float = 0.3


@dataclass
class Assignment:
    event: Event
    device: str
    confidence: float


def assign_events(events: Iterable[Event], phase_groups: Mapping[str, Iterable[int]] | None = None,
                  config: AssignmentConfig | None = None) -> List[Assignment]:
    cfg = config or AssignmentConfig()
    assignments: List[Assignment] = []
    for event in events:
        device, score = best_device(event, phase_groups)
        if score >= cfg.min_confidence:
            assignments.append(Assignment(event=event, device=device, confidence=score))
    return assignments


def build_device_timeseries(assignments: Iterable[Assignment]) -> pd.DataFrame:
    rows = []
    for item in assignments:
        event = item.event
        idx = pd.date_range(event.start, event.end, freq="1min", inclusive="left")
        if not len(idx):
            idx = pd.DatetimeIndex([event.start])
        for ts in idx:
            rows.append({
                "timestamp": ts,
                "device": item.device,
                "kw": event.mean_kw,
            })
    if not rows:
        return pd.DataFrame(columns=["total_kw"])
    df = pd.DataFrame(rows)
    pivot = df.pivot_table(index="timestamp", columns="device", values="kw", aggfunc="mean", fill_value=0.0)
    pivot["total_kw"] = pivot.sum(axis=1)
    return pivot.sort_index()


def aggregate_daily_usage(timeseries: pd.DataFrame) -> pd.DataFrame:
    if timeseries.empty:
        return pd.DataFrame(columns=["date", "device", "kwh"])
    idx = timeseries.index
    if not isinstance(idx, pd.DatetimeIndex):
        raise ValueError("Timeseries must have a DatetimeIndex")
    if len(idx) < 2:
        sample_seconds = 60
    else:
        sample_seconds = int((idx[1] - idx[0]).total_seconds())
        if sample_seconds <= 0:
            sample_seconds = 60
    factor = sample_seconds / 3600
    device_cols = [c for c in timeseries.columns if c != "total_kw"]
    energy = timeseries[device_cols] * factor
    daily = energy.resample("1D").sum()
    stacked = daily.stack().reset_index()
    stacked.columns = ["date", "device", "kwh"]
    stacked = stacked[stacked["kwh"] > 0]
    return stacked.sort_values(["date", "device"]).reset_index(drop=True)
