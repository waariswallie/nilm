from dataclasses import dataclass
from typing import List, Tuple
import pandas as pd


@dataclass
class Event:
    t_on: pd.Timestamp
    t_off: pd.Timestamp | None
    dP_on: float
    phase_dP: Tuple[float, float, float] | None


def detect_events(df: pd.DataFrame, watt_threshold: float = 500, min_dur=2, max_dur=240) -> List[Event]:
    """Simple step-change event detector on Pnet. Require sustain and pair with off."""
    s = df["Pnet"].copy()
    dP = s.diff()
    candidates_on = dP[dP > watt_threshold / 1000.0].index  # since Pnet in kW
    candidates_off = dP[dP < -watt_threshold / 1000.0].index

    events: List[Event] = []
    used_off = set()
    for t_on in candidates_on:
        # find nearest off after min_dur within max_dur
        t_min = t_on + pd.Timedelta(minutes=min_dur)
        t_max = t_on + pd.Timedelta(minutes=max_dur)
        offs = [t for t in candidates_off if t_min <= t <= t_max and t not in used_off]
        t_off = offs[0] if offs else None
        dPon = float(dP.loc[t_on])
        phase = None
        for cols in [("L1","L2","L3"), ("L1_kwh","L2_kwh","L3_kwh")]:
            if set(cols).issubset(df.columns):
                phase = tuple(float(df[c].diff().loc[t_on]) for c in cols)  # kW deltas
                break
        events.append(Event(t_on=t_on, t_off=t_off, dP_on=dPon, phase_dP=phase))
        if t_off is not None:
            used_off.add(t_off)
    return events
