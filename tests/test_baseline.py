from nilm.disaggregation.baseline import simple_threshold
import pandas as pd

def test_simple_threshold_basic():
    idx = pd.date_range('2025-01-01', periods=6, freq='1min')
    df = pd.DataFrame({'power_w':[0,20,60,70,10,0]}, index=idx)
    res = simple_threshold(df, threshold=50, min_duration=2)
    # Only 60 & 70 exceed threshold and run length=2 so both on
    assert res['on'].sum() == 2
    assert res['segment_id'].nunique() == 2  # segment ids: (1,1)
