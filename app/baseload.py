import pandas as pd


def nightly_baseload(df: pd.DataFrame, start_hour=2, end_hour=5) -> pd.Series:
    s = df["Pnet"].copy()
    night = s[(s.index.hour >= start_hour) & (s.index.hour < end_hour)]
    return night.resample("1D").median().rename("baseload_kW")
