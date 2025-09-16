from __future__ import annotations
from typing import List, Dict, Any, Iterable
import pandas as pd
import math

def group_sessions(df: pd.DataFrame,
                   max_gap_min: float = 15.0,
                   clusters: Iterable[int] | None = None,
                   min_session_energy_kWh: float = 0.0,
                   min_events: int = 1) -> List[Dict[str, Any]]:
    """Groeperen van opeenvolgende events (per cluster) tot 'sessies'.

    Een sessie eindigt als de tijd tussen vorige t_off en volgende t_on groter is dan max_gap_min.
    Open events (t_off NaT) worden genegeerd.
    """
    needed = {"t_on", "t_off", "cluster", "energy_kWh", "dP_on_kW", "duration_min"}
    if df.empty or not needed.issubset(df.columns):
        return []
    work = df[list(needed)].copy()
    work = work[work["t_off"].notna()].copy()
    if clusters is not None:
        work = work[work["cluster"].isin(list(clusters))]
    if work.empty:
        return []
    work = work.sort_values(["cluster", "t_on"])  # type: ignore[arg-type]

    sessions: List[Dict[str, Any]] = []
    current: Dict[str, Any] | None = None
    prev_end = None
    prev_cluster = None

    for _, row in work.iterrows():
        cid = row["cluster"]
        t_on = row["t_on"]
        t_off = row["t_off"]
        dP = row["dP_on_kW"]
        energy = row["energy_kWh"] if not math.isnan(row["energy_kWh"]) else 0.0
        dur = row["duration_min"] if not math.isnan(row["duration_min"]) else None

        if current is None or prev_cluster != cid:
            # start nieuwe sessie
            current = {
                "cluster": int(cid) if cid is not None else -1,
                "start": t_on,
                "end": t_off,
                "n_events": 1,
                "total_energy_kWh": float(energy) if energy is not None else 0.0,
                "dP_values": [dP],
                "durations": [dur] if dur is not None else [],
            }
        else:
            gap_min = (t_on - prev_end).total_seconds() / 60.0 if prev_end is not None else 0.0
            if gap_min <= max_gap_min:
                # Binnen zelfde sessie
                current["end"] = t_off
                current["n_events"] += 1
                current["total_energy_kWh"] += float(energy) if energy is not None else 0.0
                current["dP_values"].append(dP)
                if dur is not None:
                    current["durations"].append(dur)
            else:
                # Nieuwe sessie, push vorige
                sessions.append(_finalize_session(current))
                current = {
                    "cluster": int(cid) if cid is not None else -1,
                    "start": t_on,
                    "end": t_off,
                    "n_events": 1,
                    "total_energy_kWh": float(energy) if energy is not None else 0.0,
                    "dP_values": [dP],
                    "durations": [dur] if dur is not None else [],
                }
        prev_end = t_off
        prev_cluster = cid

    if current is not None:
        sessions.append(_finalize_session(current))

    # Filters
    out: List[Dict[str, Any]] = []
    for s in sessions:
        if s["n_events"] < min_events:
            continue
        if s["total_energy_kWh"] < min_session_energy_kWh:
            continue
        out.append(s)
    return out


def _finalize_session(s: Dict[str, Any]) -> Dict[str, Any]:
    dps = s.pop("dP_values", [])
    durs = s.pop("durations", [])
    start = s["start"]
    end = s["end"]
    duration_total = (end - start).total_seconds() / 60.0 if end and start else None
    s["duration_min"] = duration_total
    s["avg_dP_kW"] = float(sum(dps) / len(dps)) if dps else None
    s["max_dP_kW"] = float(max(dps)) if dps else None
    if durs:
        try:
            import statistics
            s["median_event_duration_min"] = float(statistics.median(durs))
        except Exception:  # noqa: BLE001
            s["median_event_duration_min"] = None
    else:
        s["median_event_duration_min"] = None
    # ISO format timestamps
    s["start"] = start.isoformat()
    s["end"] = end.isoformat() if end else None
    return s

