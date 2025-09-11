from fastapi import APIRouter, Query
from .config import settings
from .db import fetch_minute_power
from .preprocessing import clean_series
from .events import detect_events
from .clustering import build_event_frame, cluster_events
from .baseload import nightly_baseload

router = APIRouter()


@router.get("/health")
def health():
    return {"ok": True}


@router.get("/scan")
def scan(last_days: int = Query(default=settings.lookback_days, ge=1, le=365)):
    df = fetch_minute_power()
    df["Pnet"] = clean_series(df["Pnet"])  # basic denoise

    evts = detect_events(
        df,
        watt_threshold=settings.event_watt_threshold,
        min_dur=settings.min_event_duration_min,
        max_dur=settings.max_event_duration_min,
    )
    ef = build_event_frame(evts)
    if not ef.empty:
        ef = cluster_events(ef)

    base = nightly_baseload(df)
    return {
        "n_points": int(len(df)),
        "n_events": int(len(ef)),
        "baseload": base.tail(14).to_dict(),
        "events": ef.tail(200).to_dict(orient="records"),
    }
