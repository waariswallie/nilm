from __future__ import annotations
import pandas as pd
import numpy as np
from sklearn.cluster import DBSCAN
from sklearn.preprocessing import StandardScaler
from .config import settings


def build_event_frame(events) -> pd.DataFrame:
    rows = []
    for e in events:
        duration = (e.t_off - e.t_on).total_seconds() / 60 if e.t_off else np.nan
        if e.phase_dP:
            L1, L2, L3 = e.phase_dP
        else:
            L1 = L2 = L3 = np.nan
        energy_kwh = None
        if duration and duration > 0 and e.dP_on is not None:
            try:
                energy_kwh = (e.dP_on * duration) / 60.0  # crude rectangle approximation
            except Exception:  # noqa: BLE001
                energy_kwh = None
        rows.append({
            "t_on": e.t_on,
            "t_off": e.t_off,
            "dP_on_kW": e.dP_on,
            "duration_min": duration,
            "dP_L1_kW": L1,
            "dP_L2_kW": L2,
            "dP_L3_kW": L3,
            "hour": e.t_on.hour,
            "weekday": e.t_on.weekday(),
            "energy_kWh": energy_kwh,
        })
    return pd.DataFrame(rows)


def cluster_events(df_features: pd.DataFrame) -> pd.DataFrame:
    if df_features.empty:
        df_features["cluster"] = []
        return df_features

    feats = df_features[["dP_on_kW", "duration_min", "dP_L1_kW", "dP_L2_kW", "dP_L3_kW", "hour", "weekday"]].copy()
    feats = feats.fillna(0.0)
    feats["duration_min"] = feats["duration_min"].clip(0, settings.max_event_duration_min) / 60.0
    # Simple cyclical encoding for hour/weekday (normalize 0-1)
    feats["hour"] = feats["hour"] / 23.0
    feats["weekday"] = feats["weekday"] / 6.0
    X = feats.to_numpy()
    Xs = StandardScaler().fit_transform(X)
    model = DBSCAN(eps=settings.cluster_eps, min_samples=settings.cluster_min_samples).fit(Xs)
    df_features["cluster"] = model.labels_
    return df_features
