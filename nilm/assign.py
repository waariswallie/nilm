"""Assign detected events to device signatures."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional

import pandas as pd

from .events import Event
from .signatures import best_device, SIGNATURES


@dataclass(slots=True)
class AssignedEvent:
    device: str
    confidence: float
    event: Event

    def to_dict(self) -> dict:
        d = self.event.to_dict()
        d.update({"device": self.device, "confidence": float(self.confidence)})
        return d


@dataclass(slots=True)
class AssignmentConfig:
    min_confidence: float = 0.25
    supported_devices: Optional[Iterable[str]] = None


def assign_events(
    events: List[Event],
    phase_groups: Optional[dict[str, list[int]]] = None,
    config: Optional[AssignmentConfig] = None,
) -> List[AssignedEvent]:
    cfg = config or AssignmentConfig()
    supported = set(d.lower() for d in cfg.supported_devices) if cfg.supported_devices else None
    assigned: List[AssignedEvent] = []
    for event in sorted(events, key=lambda e: e.start):
        key, score = best_device(event, phase_groups)
        canonical = key.lower()
        display_name = SIGNATURES[key].name
        if supported and canonical not in supported and display_name.lower() not in supported:
            continue
        if score < cfg.min_confidence:
            continue
        assigned.append(AssignedEvent(device=display_name, confidence=score, event=event))
    # Simple overlap resolution per phase: dampen later events if they overlap strongly
    out: List[AssignedEvent] = []
    for evt in assigned:
        conflict = next(
            (other for other in out if other.event.phase == evt.event.phase and _overlap(other.event, evt.event)),
            None,
        )
        if conflict and evt.confidence <= conflict.confidence:
            # reduce weight to express uncertainty
            evt = AssignedEvent(device=evt.device, confidence=evt.confidence * 0.6, event=evt.event)
            if evt.confidence < cfg.min_confidence:
                continue
        out.append(evt)
    return out


def _overlap(a: Event, b: Event) -> bool:
    latest_start = max(a.start, b.start)
    earliest_end = min(a.end, b.end)
    return latest_start < earliest_end


def build_device_timeseries(
    assigned: List[AssignedEvent],
    freq: str = "1min",
) -> pd.DataFrame:
    records: list[dict] = []
    for a in assigned:
        # Build inclusive-exclusive range to avoid double counting end
        rng = pd.date_range(start=a.event.start, end=a.event.end, freq=freq, inclusive="left")
        power_kw = max(a.event.mean_kw - a.event.baseline_kw, a.event.delta_kw)
        for ts in rng:
            records.append(
                {
                    "timestamp": ts,
                    "device": a.device,
                    "power_kw": float(max(power_kw, 0.0)),
                    "status": 1,
                    "confidence": float(a.confidence),
                }
            )
    if not records:
        return pd.DataFrame(columns=["timestamp", "device", "power_kw", "status", "confidence"])
    df = pd.DataFrame.from_records(records)
    df.sort_values(["timestamp", "device"], inplace=True)
    return df


def aggregate_daily_usage(timeseries: pd.DataFrame) -> pd.DataFrame:
    if timeseries.empty:
        return pd.DataFrame(columns=["date", "device", "kwh", "duration_min", "confidence_mean"])
    ts = timeseries.copy()
    ts["date"] = ts["timestamp"].dt.date
    # energy per row ≈ power_kw * freq (assumes 1 minute)
    ts["energy_kwh"] = ts["power_kw"] / 60.0
    agg = ts.groupby(["date", "device"], as_index=False).agg(
        kwh=("energy_kwh", "sum"),
        duration_min=("status", "sum"),
        confidence_mean=("confidence", "mean"),
    )
    return agg


__all__ = [
    "AssignedEvent",
    "AssignmentConfig",
    "assign_events",
    "build_device_timeseries",
    "aggregate_daily_usage",
]
