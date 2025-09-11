from __future__ import annotations
import pandas as pd
import numpy as np
from sklearn.cluster import DBSCAN


def build_event_frame(events) -> pd.DataFrame:
    rows = []
    for e in events:
        duration = (e.t_off - e.t_on).total_seconds() / 60 if e.t_off else np.nan
        if e.phase_dP:
            L1, L2, L3 = e.phase_dP
        else:
            L1 = L2 = L3 = np.nan
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
        })
    return pd.DataFrame(rows)


def cluster_events(df_features: pd.DataFrame) -> pd.DataFrame:
    feats = df_features[["dP_on_kW", "duration_min", "dP_L1_kW", "dP_L2_kW", "dP_L3_kW", "hour", "weekday"]].copy()
    feats = feats.fillna(0.0)
    # scale roughly (simple)
    feats["duration_min"] = feats["duration_min"].clip(0, 240) / 60.0
    X = feats.to_numpy()
    model = DBSCAN(eps=0.5, min_samples=10).fit(X)
    df_features["cluster"] = model.labels_
    return df_features
