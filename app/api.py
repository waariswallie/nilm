from fastapi import APIRouter, Query
import os
from datetime import datetime, timezone
from typing import Dict, Any
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


@router.get("/status", summary="Lightweight status about data availability & config")
def status() -> Dict[str, Any]:
    try:
        df = fetch_minute_power()
        span = None
        if not df.empty:
            span = {
                "from": df.index.min().isoformat(),
                "to": df.index.max().isoformat(),
                "minutes": int(len(df)),
            }
        detected_cols = [c for c in ["p1","p2","n1","n2","L1","L2","L3","Pnet"] if c in df.columns]
        return {
            "ok": True,
            "mock_db": settings.mock_db,
            "db_host": settings.db_host,
            "db_name": settings.db_name,
            "span": span,
            "columns": detected_cols,
            "threshold_watt": settings.event_watt_threshold,
            "git_sha": os.getenv("APP_GIT_SHA", "unknown"),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": str(exc),
            "mock_db": settings.mock_db,
            "db_host": settings.db_host,
            "db_name": settings.db_name,
            "table": settings.table_name,
            "consume_cols": settings.consume_cols,
            "export_cols": settings.export_cols,
            "phase_kwh_cols": settings.phase_kwh_cols,
        }


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
