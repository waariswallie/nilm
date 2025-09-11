from __future__ import annotations
import pandas as pd

def resample(df: pd.DataFrame, rule: str = "1min") -> pd.DataFrame:
    """Resample naar vaste interval (gemiddelde)."""
    return df.resample(rule).mean(numeric_only=True)


def fill_gaps(df: pd.DataFrame, limit: int = 5) -> pd.DataFrame:
    """Kleine gaten forward-fill (max 'limit' opeenvolgende)."""
    return df.ffill(limit=limit)


def smooth(df: pd.DataFrame, window: int = 3) -> pd.DataFrame:
    if window <= 1:
        return df
    return df.rolling(window=window, min_periods=1, center=True).mean(numeric_only=True)
