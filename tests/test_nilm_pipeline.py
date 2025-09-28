import pandas as pd
import numpy as np

from nilm.loader import _clean_numeric
from nilm.prep import preprocess, PrepConfig
from nilm.events import EventConfig, detect_events
from nilm.signatures import best_device
from nilm.assign import AssignmentConfig, assign_events, build_device_timeseries, aggregate_daily_usage


def test_clean_numeric_handles_comma_decimal():
    series = pd.Series(["1,23", "4,56", None])
    cleaned = _clean_numeric(series)
    assert np.isclose(cleaned.iloc[0], 1.23)
    assert np.isclose(cleaned.iloc[1], 4.56)
    assert pd.isna(cleaned.iloc[2])


def _synthetic_frame(step_kw: float = 2.0, duration_min: int = 10) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01 00:00", periods=120, freq="1min", tz="Europe/Amsterdam")
    base = pd.Series(0.2, index=idx)
    active = base.copy()
    active.iloc[10 : 10 + duration_min] += step_kw
    df = pd.DataFrame({
        "instantaneous_active_power_l1_kW": active,
        "instantaneous_active_power_l2_kW": base,
        "instantaneous_active_power_l3_kW": base,
    })
    return df


def test_preprocess_adds_phase_hints():
    df = _synthetic_frame()
    prep = preprocess(df, config=PrepConfig(resample_seconds=60, smooth_seconds=60))
    hints = prep.attrs.get("phase_groups")
    assert hints is not None
    assert "l1" in hints and any(g in hints["l1"] for g in [9, 10, 11, 12])


def test_event_detection_detects_step():
    df = _synthetic_frame()
    prep = preprocess(df)
    events = detect_events(prep, EventConfig(min_event_kw=0.5, min_duration_s=60))
    assert events, "Expected at least one event"
    evt = events[0]
    assert evt.phase == "l1"
    assert evt.duration_s >= 60


def test_signature_and_assignment_workflow():
    df = _synthetic_frame()
    prep = preprocess(df)
    events = detect_events(prep, EventConfig(min_event_kw=0.5, min_duration_s=60))
    assigned = assign_events(events, phase_groups=prep.attrs.get("phase_groups"), config=AssignmentConfig(min_confidence=0.2))
    assert assigned, "Assignments should not be empty"
    ts = build_device_timeseries(assigned)
    assert not ts.empty
    daily = aggregate_daily_usage(ts)
    assert not daily.empty
    value = float(daily["kwh"].iloc[0])
    assert value > 0


def test_best_device_prefers_phase_hint():
    from nilm.events import Event

    start = pd.Timestamp("2024-01-01 12:00", tz="Europe/Amsterdam")
    evt = Event(
        start=start,
        end=start + pd.Timedelta(minutes=5),
        phase="l3",
        delta_kw=2.2,
        mean_kw=2.5,
        duration_s=300,
        energy_kwh=0.18,
        peak_kw=2.6,
        baseline_kw=0.2,
    )
    device, score = best_device(evt, {"l3": [3, 4]})
    assert device in {"quooker", "droger"}
    assert score > 0.3
