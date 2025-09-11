from __future__ import annotations
import pandas as pd


def mae(y_true: pd.Series, y_pred: pd.Series) -> float:
    return (y_true - y_pred).abs().mean()
