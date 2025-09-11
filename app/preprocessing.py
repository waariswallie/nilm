import pandas as pd


def clean_series(s: pd.Series, max_gap_min: int = 3, clip_low: float = -10, clip_high: float = 15) -> pd.Series:
    s = s.copy()
    # Clip impossible spikes
    s = s.clip(lower=clip_low, upper=clip_high)
    # Fill small gaps (NaNs)
    if s.isna().any():
        s = s.interpolate(limit=max_gap_min, limit_direction="both")
    # Median filter (window 5)
    s = s.rolling(5, center=True, min_periods=1).median()
    return s
