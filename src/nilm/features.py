from __future__ import annotations
import pandas as pd


def add_derivatives(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["dP"] = out["power_w"].diff()
    return out


def rolling_stats(df: pd.DataFrame, window: int = 5) -> pd.DataFrame:
    out = df.copy()
    out["roll_mean"] = out["power_w"].rolling(window, min_periods=1).mean()
    out["roll_std"] = out["power_w"].rolling(window, min_periods=1).std()
    return out
