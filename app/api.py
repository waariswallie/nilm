from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse
import os
from datetime import datetime, timezone, timedelta
from typing import Dict, Any
import pandas as pd
from .config import settings
from .db import fetch_minute_power, fetch_highres_power
from .preprocessing import clean_series
from .events import detect_events
from .clustering import build_event_frame, cluster_events
from .baseload import nightly_baseload
from .labeling import LabelStore, enrich_cluster_stats, apply_suggestions, auto_label_clusters
from .sessions import group_sessions
import numpy as np

router = APIRouter()
_label_store = LabelStore(path="labels.json")


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
def scan(last_days: int = Query(default=settings.lookback_days, ge=1, le=30),
         eps: float | None = Query(default=None, description="Override DBSCAN eps"),
         min_samples: int | None = Query(default=None, ge=1, description="Override DBSCAN min_samples")):
    # Enforce hard cap 30 days
    if last_days > 30:
        last_days = 30
    start_dt = datetime.utcnow() - timedelta(days=last_days)
    start_iso = start_dt.replace(second=0, microsecond=0).isoformat(sep=" ")

    # Decide whether to use high-res (if configured and window small)
    use_highres = False
    df_hr = None
    if settings.highres_table and settings.highres_interval_s:
        # heuristic: only use high-res if <= 3 days (avoid huge payloads)
        if last_days <= 3:
            df_hr = fetch_highres_power(start_ts=start_iso)
            if df_hr is not None and not df_hr.empty:
                use_highres = True

    if use_highres and df_hr is not None:
        df_src = df_hr
        # Build minute aggregate for baseload + fallback features
        df = df_src.resample('1min').mean(numeric_only=True)
        if "Pnet" in df.columns:
            df["Pnet"] = clean_series(df["Pnet"].ffill())
    else:
        df = fetch_minute_power(start_ts=start_iso)
        if not df.empty:
            df["Pnet"] = clean_series(df["Pnet"])  # basic denoise

    # Event detection source: high-res if available else minute-level
    event_df_source = df_hr if use_highres else df
    evts = []
    if event_df_source is not None and not event_df_source.empty:
        # Adjust threshold for high-res: reduce if using high-res (heuristic 60% of minute threshold)
        threshold = settings.event_watt_threshold
        if use_highres:
            threshold = max(100, threshold * 0.6)
        evts = detect_events(
            event_df_source,
            watt_threshold=threshold,
            min_dur=settings.min_event_duration_min,
            max_dur=settings.max_event_duration_min,
        )
    ef = build_event_frame(evts)
    if not ef.empty:
        ef = cluster_events(ef, eps=eps, min_samples=min_samples)

    base = nightly_baseload(df)
    # Sanitize for JSON (remove NaN/inf)
    def safe(val):
        if isinstance(val, (float, int)):
            if not np.isfinite(val):
                return None
        return val

    events_out = []
    if not ef.empty:
        for r in ef.tail(200).to_dict(orient="records"):
            events_out.append({k: safe(v) for k, v in r.items()})

    base_dict = {str(k): safe(v) for k, v in base.tail(14).items()}

    # Cluster summary for inline response
    cluster_summary = []
    if not ef.empty and "cluster" in ef.columns:
        grp = ef.groupby("cluster", dropna=False)
        for cid, g in grp:
            try:
                cid_int = int(f"{cid}")
            except Exception:  # noqa: BLE001
                cid_int = -9999
            cluster_summary.append({
                "cluster": cid_int,
                "count": int(len(g)),
                "avg_dP_kW": float(g["dP_on_kW"].mean()),
                "median_duration_min": float(g["duration_min"].median() if g["duration_min"].notna().any() else 0),
                "avg_energy_kWh": float(g["energy_kWh"].mean()) if g["energy_kWh"].notna().any() else None,
            })

    return {
        "lookback_days": last_days,
        "highres": use_highres,
        "from": df.index.min().isoformat() if not df.empty else None,
        "to": df.index.max().isoformat() if not df.empty else None,
        "n_points": int(len(df)),
        "n_events": int(len(ef)),
        "baseload": base_dict,
        "events": events_out,
        "clusters": cluster_summary,
    }


@router.get("/events", summary="List recent events with optional limit & days")
def events_endpoint(last_days: int = Query(default=7, ge=1, le=30), limit: int = Query(default=200, ge=1, le=2000),
                    eps: float | None = Query(default=None),
                    min_samples: int | None = Query(default=None, ge=1)):
    start_dt = datetime.utcnow() - timedelta(days=last_days)
    start_iso = start_dt.replace(second=0, microsecond=0).isoformat(sep=" ")
    df = fetch_minute_power(start_ts=start_iso)
    evts = detect_events(
        df,
        watt_threshold=settings.event_watt_threshold,
        min_dur=settings.min_event_duration_min,
        max_dur=settings.max_event_duration_min,
    )
    ef = build_event_frame(evts)
    if not ef.empty:
        ef = cluster_events(ef, eps=eps, min_samples=min_samples)
    out = []
    if not ef.empty:
        for r in ef.tail(limit).to_dict(orient="records"):
            r["t_on"] = r["t_on"].isoformat() if r.get("t_on") else None
            r["t_off"] = r["t_off"].isoformat() if r.get("t_off") else None
            out.append(r)
    return {"count": len(out), "events": out}


@router.get("/clusters", summary="Cluster summaries with (suggested) labels")
def clusters_endpoint(last_days: int = Query(default=7, ge=1, le=30),
                      eps: float | None = Query(default=None),
                      min_samples: int | None = Query(default=None, ge=1),
                      watt_threshold: float | None = Query(default=None, description="Override event watt threshold (W)"),
                      min_dur: int | None = Query(default=None, description="Override min event duration (minutes)"),
                      recluster_noise: bool = Query(default=False, description="Voer tweede clustering uit op noise (-1)"),
                      noise_eps: float | None = Query(default=None, description="DBSCAN eps voor noise re-cluster"),
                      noise_min_samples: int | None = Query(default=None, ge=1, description="DBSCAN min_samples voor noise re-cluster"),
                      feature_set: str = Query(default="basic", pattern="^(basic|extended)$"),
                      debug: bool = Query(default=False, description="Voeg rule_trace toe")):
    start_dt = datetime.utcnow() - timedelta(days=last_days)
    start_iso = start_dt.replace(second=0, microsecond=0).isoformat(sep=" ")
    df = fetch_minute_power(start_ts=start_iso)
    evts = detect_events(
        df,
        watt_threshold=watt_threshold or settings.event_watt_threshold,
        min_dur=min_dur or settings.min_event_duration_min,
        max_dur=settings.max_event_duration_min,
    )
    ef = build_event_frame(evts)
    if ef.empty:
        return {"clusters": []}
    ef = cluster_events(ef, eps=eps, min_samples=min_samples)

    note_parts = []
    if recluster_noise and "cluster" in ef.columns and (ef["cluster"] == -1).any():
        noise_idx = ef.index[ef["cluster"] == -1]
        noise_df = ef.loc[noise_idx].copy()
        noise_df = noise_df.drop(columns=["cluster"], errors="ignore")
        if not noise_df.empty:
            base_eps = eps if eps is not None else settings.cluster_eps
            base_min = min_samples if min_samples is not None else settings.cluster_min_samples
            re_eps = noise_eps if noise_eps is not None else base_eps * 1.3
            re_min = noise_min_samples if noise_min_samples is not None else max(3, base_min // 2)
            # Zorg dat noise_df zeker een DataFrame is
            import pandas as _pd  # local import to avoid top-level cost
            if not isinstance(noise_df, _pd.DataFrame):
                noise_df = _pd.DataFrame(noise_df)
            noise_df = noise_df.copy()
            reclustered = cluster_events(noise_df, eps=re_eps, min_samples=re_min)
            # Bepaal hoogste bestaande cluster (excl -1)
            existing_non_noise = ef.loc[ef["cluster"] != -1, "cluster"]
            if not existing_non_noise.empty:
                try:
                    max_val = existing_non_noise.max()
                    max_cluster_existing = int(max_val) if isinstance(max_val, (int, float)) else -1
                except Exception:
                    max_cluster_existing = -1
            else:
                max_cluster_existing = -1
            next_id = max_cluster_existing + 1
            replaced = 0
            for i in reclustered.index:
                subc = reclustered.at[i, "cluster"]
                if subc == -1:
                    continue
                ef.at[i, "cluster"] = next_id + int(subc)
                replaced += 1
            if replaced:
                note_parts.append(f"noise re-clustered: {replaced} events uit -1 verplaatst (eps={re_eps:.3f}, min_samples={re_min})")
            else:
                note_parts.append("noise re-cluster poging gaf geen extra clusters")
    cluster_stats = []
    if "cluster" in ef.columns:
        for cid, g in ef.groupby("cluster", dropna=False):
            hours = list(g["t_on"].dt.hour) if "t_on" in g else []
            if cid is None:
                cid_int = -1
            else:
                if isinstance(cid, (int,)):
                    cid_int = int(cid)
                else:
                    try:
                        cid_int = int(str(cid))
                    except Exception:
                        cid_int = -1
            cluster_stats.append({
                "cluster": cid_int,
                "count": int(len(g)),
                "avg_dP_kW": float(g["dP_on_kW"].mean()),
                "median_duration_min": float(g["duration_min"].median() if g["duration_min"].notna().any() else 0),
                "avg_energy_kWh": float(g["energy_kWh"].mean()) if g["energy_kWh"].notna().any() else None,
                "total_energy_kWh": float(g["energy_kWh"].sum()) if g["energy_kWh"].notna().any() else None,
                "avg_event_energy_kWh": float(g["energy_kWh"].mean()) if g["energy_kWh"].notna().any() else None,
                "dP_min_kW": float(g["dP_on_kW"].min()),
                "dP_max_kW": float(g["dP_on_kW"].max()),
                "duration_min_min": float(g["duration_min"].min() if g["duration_min"].notna().any() else 0),
                "duration_min_max": float(g["duration_min"].max() if g["duration_min"].notna().any() else 0),
                "hours": hours,
            })
    cluster_stats = enrich_cluster_stats(cluster_stats)
    enriched = apply_suggestions(cluster_stats, _label_store, extended=(feature_set == "extended"), debug=debug)
    note = None
    if note_parts:
        note = "; ".join(note_parts)
    return {"clusters": enriched, "note": note, "feature_set": feature_set, "debug": debug}


@router.post("/autolabel", summary="Automatisch labels toepassen op clusters op basis van heuristieken")
def autolabel_endpoint(last_days: int = Query(default=7, ge=1, le=30), overwrite: bool = False,
                       eps: float | None = Query(default=None),
                       min_samples: int | None = Query(default=None, ge=1),
                       feature_set: str = Query(default="basic", pattern="^(basic|extended)$")):
    # Reuse clustering logic
    start_dt = datetime.utcnow() - timedelta(days=last_days)
    start_iso = start_dt.replace(second=0, microsecond=0).isoformat(sep=" ")
    df = fetch_minute_power(start_ts=start_iso)
    evts = detect_events(
        df,
        watt_threshold=settings.event_watt_threshold,
        min_dur=settings.min_event_duration_min,
        max_dur=settings.max_event_duration_min,
    )
    ef = build_event_frame(evts)
    if ef.empty:
        return {"applied": [], "skipped": [], "clusters": []}
    ef = cluster_events(ef, eps=eps, min_samples=min_samples)
    cluster_stats = []
    if "cluster" in ef.columns:
        for cid, g in ef.groupby("cluster", dropna=False):
            hours = list(g["t_on"].dt.hour) if "t_on" in g else []
            if cid is None:
                cid_int = -1
            else:
                try:
                    cid_int = int(str(cid))
                except Exception:
                    cid_int = -1
            cluster_stats.append({
                "cluster": cid_int,
                "count": int(len(g)),
                "avg_dP_kW": float(g["dP_on_kW"].mean()),
                "median_duration_min": float(g["duration_min"].median() if g["duration_min"].notna().any() else 0),
                "avg_energy_kWh": float(g["energy_kWh"].mean()) if g["energy_kWh"].notna().any() else None,
                "total_energy_kWh": float(g["energy_kWh"].sum()) if g["energy_kWh"].notna().any() else None,
                "avg_event_energy_kWh": float(g["energy_kWh"].mean()) if g["energy_kWh"].notna().any() else None,
                "dP_min_kW": float(g["dP_on_kW"].min()),
                "dP_max_kW": float(g["dP_on_kW"].max()),
                "dP_std_kW": float(g["dP_on_kW"].std() if g["dP_on_kW"].notna().any() else 0.0),
                "duration_min_min": float(g["duration_min"].min() if g["duration_min"].notna().any() else 0),
                "duration_min_max": float(g["duration_min"].max() if g["duration_min"].notna().any() else 0),
                "hours": hours,
                "dows": list(g["t_on"].dt.weekday) if "t_on" in g else [],
                "first_ts": g["t_on"].min().isoformat() if "t_on" in g else None,
                "last_ts": g["t_on"].max().isoformat() if "t_on" in g else None,
            })
    cluster_stats = enrich_cluster_stats(cluster_stats)
    result = auto_label_clusters(cluster_stats, _label_store, overwrite=overwrite, extended=(feature_set == "extended"))
    enriched = apply_suggestions(cluster_stats, _label_store, extended=(feature_set == "extended"))
    return {"lookback_days": last_days, **result, "clusters": enriched}


# Extra alias zodat je met GET kunt testen of de route bestaat zonder iets te veranderen.
@router.get("/autolabel", summary="Alias van POST /autolabel (dry-run)")
def autolabel_get(last_days: int = Query(default=7, ge=1, le=30),
                  eps: float | None = Query(default=None),
                  min_samples: int | None = Query(default=None, ge=1),
                  feature_set: str = Query(default="basic", pattern="^(basic|extended)$"),
                  debug: bool = Query(default=False)):
    # Voer dezelfde logica uit maar persist niet: we roepen autolabel_endpoint aan met overwrite=False
    # en daarna laden we labels opnieuw zodat we een consistent antwoord hebben.
    # Omdat autolabel_endpoint sowieso persist doet, maken we hier een 'dry run' variant die GEEN opslag doet.
    start_dt = datetime.utcnow() - timedelta(days=last_days)
    start_iso = start_dt.replace(second=0, microsecond=0).isoformat(sep=" ")
    df = fetch_minute_power(start_ts=start_iso)
    evts = detect_events(
        df,
        watt_threshold=settings.event_watt_threshold,
        min_dur=settings.min_event_duration_min,
        max_dur=settings.max_event_duration_min,
    )
    ef = build_event_frame(evts)
    if ef.empty:
        return {"lookback_days": last_days, "applied": [], "skipped": [], "clusters": []}
    ef = cluster_events(ef, eps=eps, min_samples=min_samples)
    cluster_stats = []
    if "cluster" in ef.columns:
        for cid, g in ef.groupby("cluster", dropna=False):
            hours = list(g["t_on"].dt.hour) if "t_on" in g else []
            if cid is None:
                cid_int = -1
            else:
                try:
                    cid_int = int(str(cid))
                except Exception:  # noqa: BLE001
                    cid_int = -1
            cluster_stats.append({
                "cluster": cid_int,
                "count": int(len(g)),
                "avg_dP_kW": float(g["dP_on_kW"].mean()),
                "median_duration_min": float(g["duration_min"].median() if g["duration_min"].notna().any() else 0),
                "avg_energy_kWh": float(g["energy_kWh"].mean()) if g["energy_kWh"].notna().any() else None,
                "total_energy_kWh": float(g["energy_kWh"].sum()) if g["energy_kWh"].notna().any() else None,
                "avg_event_energy_kWh": float(g["energy_kWh"].mean()) if g["energy_kWh"].notna().any() else None,
                "dP_min_kW": float(g["dP_on_kW"].min()),
                "dP_max_kW": float(g["dP_on_kW"].max()),
                "dP_std_kW": float(g["dP_on_kW"].std() if g["dP_on_kW"].notna().any() else 0.0),
                "duration_min_min": float(g["duration_min"].min() if g["duration_min"].notna().any() else 0),
                "duration_min_max": float(g["duration_min"].max() if g["duration_min"].notna().any() else 0),
                "hours": hours,
                "dows": list(g["t_on"].dt.weekday) if "t_on" in g else [],
                "first_ts": g["t_on"].min().isoformat() if "t_on" in g else None,
                "last_ts": g["t_on"].max().isoformat() if "t_on" in g else None,
            })
    cluster_stats = enrich_cluster_stats(cluster_stats)
    enriched = apply_suggestions(cluster_stats, _label_store, extended=(feature_set == "extended"), debug=debug)
    return {"lookback_days": last_days, "applied": [], "skipped": [], "clusters": enriched, "feature_set": feature_set, "debug": debug, "note": "GET /autolabel is dry-run; gebruik POST om labels op te slaan."}


from pydantic import BaseModel


class LabelIn(BaseModel):
    cluster: int
    label: str
    confidence: float | None = None


@router.post("/labels", summary="Persist a manual label for a cluster")
def set_label(body: LabelIn):
    """Voeg of overschrijf een label voor een cluster.

    Voorbeeld body:
    {
      "cluster": 3,
      "label": "Quooker",
      "confidence": 0.9
    }

    Het clusternummer vind je via /clusters (veld 'cluster') of via /devices.
    """
    cl = _label_store.set(cluster=body.cluster, label=body.label, source="manual", confidence=body.confidence)
    return {"ok": True, "label": cl.to_dict()}


@router.get("/labels", summary="Lijst van alle opgeslagen labels")
def list_labels():
    out = []
    for cid, cl in _label_store.all().items():
        d = cl.to_dict()
        d["cluster"] = cid
        out.append(d)
    out.sort(key=lambda x: x["cluster"])
    return {"count": len(out), "labels": out}


@router.delete("/labels/{cluster}", summary="Verwijder een label voor een cluster")
def delete_label(cluster: int):
    ok = _label_store.delete(cluster)
    return {"ok": ok, "cluster": cluster}


@router.get("/devices", summary="Aggregated per-device (cluster) energy usage over a window")
def devices_endpoint(last_days: int = Query(default=7, ge=1, le=30), include_noise: bool = False,
                     merge_labels: bool = Query(default=True, description="Combine clusters met hetzelfde label"),
                     eps: float | None = Query(default=None),
                     min_samples: int | None = Query(default=None, ge=1),
                     feature_set: str = Query(default="basic", pattern="^(basic|extended)$")):
    """Return an approximate energy usage overview per detected (labeled) device.

    Energy is estimated from event rectangle (ΔP * duration). For multi-cycle devices (bv. wasmachine)
    alleen heating pulses worden nu getoond; totale cycle energie kan onderschat zijn.
    """
    start_dt = datetime.utcnow() - timedelta(days=last_days)
    start_iso = start_dt.replace(second=0, microsecond=0).isoformat(sep=" ")
    df = fetch_minute_power(start_ts=start_iso)
    evts = detect_events(
        df,
        watt_threshold=settings.event_watt_threshold,
        min_dur=settings.min_event_duration_min,
        max_dur=settings.max_event_duration_min,
    )
    ef = build_event_frame(evts)
    if ef.empty:
        return {"devices": [], "total_event_energy_kWh": 0.0, "from": start_iso, "to": datetime.utcnow().isoformat(), "lookback_days": last_days}
    ef = cluster_events(ef, eps=eps, min_samples=min_samples)

    # Aggregate per cluster
    clusters: Dict[int, Dict[str, Any]] = {}
    for cid, g in ef.groupby("cluster", dropna=False):
        try:
            cid_int = int(f"{cid}")
        except Exception:  # noqa: BLE001
            cid_int = -9999
        if cid_int == -1 and not include_noise:
            continue
        energy_series = g["energy_kWh"].fillna(0)
        total_energy = float(energy_series.sum())
        clusters[cid_int] = {
            "cluster": cid_int,
            "events": int(len(g)),
            "avg_dP_kW": float(g["dP_on_kW"].mean()),
            "median_duration_min": float(g["duration_min"].median() if g["duration_min"].notna().any() else 0),
            "total_energy_kWh": total_energy,
            "avg_event_energy_kWh": float(energy_series.mean()) if len(energy_series) else 0.0,
            "first_seen": g["t_on"].min().isoformat() if "t_on" in g else None,
            "last_seen": g["t_on"].max().isoformat() if "t_on" in g else None,
            "hours": list(g["t_on"].dt.hour) if "t_on" in g else [],
            # extra stats for labeling
            "dP_min_kW": float(g["dP_on_kW"].min()),
            "dP_max_kW": float(g["dP_on_kW"].max()),
            "dP_std_kW": float(g["dP_on_kW"].std() if g["dP_on_kW"].notna().any() else 0.0),
            "duration_min_min": float(g["duration_min"].min() if g["duration_min"].notna().any() else 0),
            "duration_min_max": float(g["duration_min"].max() if g["duration_min"].notna().any() else 0),
            "first_ts": g["t_on"].min().isoformat() if "t_on" in g else None,
            "last_ts": g["t_on"].max().isoformat() if "t_on" in g else None,
            "dows": list(g["t_on"].dt.weekday) if "t_on" in g else [],
        }

    # Enrich with heuristics & suggestions
    raw_list = list(clusters.values())
    raw_list = enrich_cluster_stats(raw_list)
    enriched = apply_suggestions(raw_list, _label_store, extended=(feature_set == "extended"))

    # Build device list
    total_energy_all = sum(d.get("total_energy_kWh", 0.0) for d in enriched)
    devices_out = []

    if merge_labels:
        # Merge clusters that share the same resolved label (manual > rule > suggestion)
        merged: Dict[str, Dict[str, Any]] = {}
        for d in enriched:
            resolved_label = d.get("label") or d.get("suggested_label") or f"Cluster {d['cluster']}"
            source = d.get("label_source") or ("suggested" if d.get("suggested_label") else None)
            key = resolved_label
            if key not in merged:
                merged[key] = {
                    "name": resolved_label,
                    "clusters": [d["cluster"]],
                    "source": source,
                    "events": d["events"],
                    "total_energy_kWh": d.get("total_energy_kWh", 0.0),
                    "avg_dP_kW_acc": d["avg_dP_kW"] * d["events"],  # weighted sum
                    "median_durations": [d["median_duration_min"]],
                    "first_seen": d.get("first_seen"),
                    "last_seen": d.get("last_seen"),
                }
            else:
                m = merged[key]
                m["clusters"].append(d["cluster"])
                m["events"] += d["events"]
                m["total_energy_kWh"] += d.get("total_energy_kWh", 0.0)
                m["avg_dP_kW_acc"] += d["avg_dP_kW"] * d["events"]
                # update span
                if d.get("first_seen") and (m["first_seen"] is None or d["first_seen"] < m["first_seen"]):
                    m["first_seen"] = d["first_seen"]
                if d.get("last_seen") and (m["last_seen"] is None or d["last_seen"] > m["last_seen"]):
                    m["last_seen"] = d["last_seen"]
                m["median_durations"].append(d["median_duration_min"])
        # finalize
        for key, m in merged.items():
            avg_dP = m["avg_dP_kW_acc"] / m["events"] if m["events"] else 0.0
            share = (m["total_energy_kWh"] / total_energy_all * 100.0) if total_energy_all > 0 else 0.0
            devices_out.append({
                "name": m["name"],
                "clusters": m["clusters"],
                "source": m["source"],
                "events": m["events"],
                "avg_dP_kW": round(avg_dP, 4),
                "median_duration_min": float(np.median(m["median_durations"])) if m["median_durations"] else 0.0,
                "total_energy_kWh": round(m["total_energy_kWh"], 4),
                "avg_event_energy_kWh": round(m["total_energy_kWh"] / m["events"], 4) if m["events"] else 0.0,
                "energy_share_pct": round(share, 2),
                "first_seen": m.get("first_seen"),
                "last_seen": m.get("last_seen"),
                "merged": True,
            })
    else:
        for d in enriched:
            label = d.get("label") or d.get("suggested_label") or f"Cluster {d['cluster']}"
            source = d.get("label_source") or ("suggested" if d.get("suggested_label") else None)
            share = (d.get("total_energy_kWh", 0.0) / total_energy_all * 100.0) if total_energy_all > 0 else 0.0
            devices_out.append({
                "name": label,
                "cluster": d["cluster"],
                "source": source,
                "events": d["events"],
                "avg_dP_kW": d["avg_dP_kW"],
                "median_duration_min": d["median_duration_min"],
                "total_energy_kWh": round(d.get("total_energy_kWh", 0.0), 4),
                "avg_event_energy_kWh": round(d.get("avg_event_energy_kWh", 0.0), 4),
                "energy_share_pct": round(share, 2),
                "first_seen": d.get("first_seen"),
                "last_seen": d.get("last_seen"),
                "merged": False,
            })

    # Sort by energy desc
    devices_out.sort(key=lambda x: x["total_energy_kWh"], reverse=True)

    return {
        "from": df.index.min().isoformat() if not df.empty else start_iso,
        "to": df.index.max().isoformat() if not df.empty else datetime.utcnow().isoformat(),
        "lookback_days": last_days,
        "total_event_energy_kWh": round(total_energy_all, 4),
        "devices": devices_out,
        "note": "Energy per event = dP * duration (rectangle). Lange cycli met meerdere heating pulses (bv. wasmachine) worden als losse events getoond.",
    }


@router.get("/sessions", summary="Gegroepeerde apparaat-runs (sessions) op basis van events")
def sessions_endpoint(last_days: int = Query(default=7, ge=1, le=30),
                      max_gap_min: int = Query(default=15, ge=1, le=120),
                      min_energy_kwh: float = Query(default=0.0, ge=0.0),
                      min_events: int = Query(default=2, ge=1, le=500),
                      clusters: str | None = Query(default=None, description="Komma lijst van cluster ids")):
    start_dt = datetime.utcnow() - timedelta(days=last_days)
    start_iso = start_dt.replace(second=0, microsecond=0).isoformat(sep=" ")
    df = fetch_minute_power(start_ts=start_iso)
    evts = detect_events(
        df,
        watt_threshold=settings.event_watt_threshold,
        min_dur=settings.min_event_duration_min,
        max_dur=settings.max_event_duration_min,
    )
    ef = build_event_frame(evts)
    if ef.empty:
        return {"sessions": [], "count": 0}
    ef = cluster_events(ef)
    cluster_filter = None
    if clusters:
        try:
            cluster_filter = [int(x.strip()) for x in clusters.split(',') if x.strip()]
        except Exception:  # noqa: BLE001
            cluster_filter = None
    sessions = group_sessions(ef, max_gap_min=max_gap_min, clusters=cluster_filter,
                              min_session_energy_kWh=min_energy_kwh, min_events=min_events)
    # Label enrichment
    for s in sessions:
        cid = s.get("cluster")
        lbl = _label_store.get(cid) if cid is not None else None
        if lbl:
            s["label"] = lbl.label
            s["label_source"] = lbl.source
    # Sort by energy desc
    sessions.sort(key=lambda x: x.get("total_energy_kWh", 0), reverse=True)
    total_sessions_energy = sum(s.get("total_energy_kWh", 0) for s in sessions)
    for s in sessions:
        te = s.get("total_energy_kWh", 0)
        s["energy_share_pct"] = round((te / total_sessions_energy * 100.0), 2) if total_sessions_energy > 0 else 0.0
    return {
        "lookback_days": last_days,
        "from": df.index.min().isoformat() if not df.empty else start_iso,
        "to": df.index.max().isoformat() if not df.empty else datetime.utcnow().isoformat(),
        "max_gap_min": max_gap_min,
        "count": len(sessions),
        "total_sessions_energy_kWh": round(total_sessions_energy, 4),
        "sessions": sessions,
        "note": "Sessions = samenvoegen van events per cluster zolang de gap <= max_gap_min; open events genegeerd.",
    }


@router.get("/viz", response_class=HTMLResponse, summary="Simple in-browser visualization of baseload, clusters and events")
def viz(days: int = Query(default=30, ge=1, le=365)):
    """Return a lightweight HTML page with client-side charts.

    Usage:
        /viz              -> last 30 days
        /viz?days=90      -> last 90 days
        /viz?days=365     -> last year (may be slower on low-power devices)
    """
    # We keep the page fully static (just one request to /scan) to avoid heavy backend work.
    # For large day windows only summary (clusters, baseload) is meaningful because events list is capped.
    # Build HTML without f-string to avoid escaping braces; simple placeholder replacement used.
    html = """
<!DOCTYPE html>
<html lang=\"en\">
<head>
    <meta charset=\"utf-8\" />
    <title>NILM Quick Viz</title>
    <meta name=\"viewport\" content=\"width=device-width,initial-scale=1\" />
    <style>
    body { font-family: system-ui, Arial, sans-serif; margin: 0; padding: 1rem 1.5rem 3rem; background:#0f1115; color:#e6e8ea; }
    h1,h2 { font-weight:600; margin: 0.8rem 0 0.4rem; }
    a, a:visited { color:#4ea3ff; }
    .row { display:flex; flex-wrap:wrap; gap:1.5rem; }
    .card { background:#1b1f26; padding:1rem 1.2rem; border-radius:8px; flex:1 1 360px; box-shadow:0 2px 4px rgba(0,0,0,0.4); }
    canvas { max-width:100%; height:300px; }
    table { border-collapse: collapse; width:100%; font-size:0.85rem; }
    th, td { border-bottom:1px solid #2c323c; padding:4px 6px; text-align:left; }
    th { background:#232a33; position:sticky; top:0; }
    .tag { display:inline-block; background:#26323f; padding:2px 6px; border-radius:4px; margin:2px; font-size:0.7rem; }
    .warn { color:#ffa94d; }
    #footer { margin-top:2rem; font-size:0.7rem; opacity:0.7; }
    input, button { background:#232a33; border:1px solid #36404c; color:#e6e8ea; padding:4px 8px; border-radius:4px; }
    button { cursor:pointer; }
    </style>
    <script src=\"https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js\"></script>
</head>
<body>
    <h1>NILM Quick Visualization</h1>
    <p>Laatste <span id=\"days-span\"></span> dagen. Pas aan: <input type=\"number\" id=\"days-input\" min=\"1\" max=\"365\" style=\"width:5rem\"/> <button id=\"reload-btn\">Herlaad</button> &middot; <a href=\"/docs\">API docs</a></p>
    <div class=\"row\">
        <div class=\"card\" style=\"flex:1 1 500px\">
            <h2>Baseload (nacht median 02–05h)</h2>
            <canvas id=\"baseloadChart\"></canvas>
        </div>
        <div class=\"card\" style=\"max-width:420px\">
            <h2>Cluster verdeling</h2>
            <canvas id=\"clusterChart\"></canvas>
            <div id=\"clusterSummary\" style=\"margin-top:0.5rem;font-size:0.8rem\"></div>
        </div>
    </div>
    <div class=\"card\">
        <h2>Recente events (max 200)</h2>
        <canvas id=\"eventsScatter\" style=\"height:260px\"></canvas>
        <p style=\"font-size:0.7rem;opacity:0.7\">We tonen alleen de laatste 200 events uit /scan voor performance. Gebruik kleinere window voor meer detail.</p>
        <table id=\"eventsTable\"></table>
    </div>
        <div id=\"footer\">Generated __GEN_AT__ – Simple client viz.</div>
<script>
const urlParams = new URLSearchParams(window.location.search);
    const days = Number(urlParams.get('days') || __DAYS__);
document.getElementById('days-input').value = days;
document.getElementById('days-span').textContent = days;
document.getElementById('reload-btn').onclick = () => {
    const d = document.getElementById('days-input').value || 30;
    window.location.search = '?days=' + d;
};

function fmtTS(ts){ if(!ts) return ''; return ts.replace('T',' ').replace('+00:00',''); }

async function load(){
            const resp = await fetch('/scan?last_days=' + days);
    if(!resp.ok){
        document.body.innerHTML = `<h2>Fout bij laden /scan (${resp.status})</h2>`;return;
    }
    const data = await resp.json();
    renderBaseload(data.baseload);
    renderClusters(data.clusters, data.n_events);
    renderEvents(data.events);
}

function renderBaseload(base){
    const labels = Object.keys(base).sort();
    const vals = labels.map(k=> base[k]);
    const ctx = document.getElementById('baseloadChart');
    new Chart(ctx,{type:'line',data:{labels, datasets:[{label:'Baseload kW', data:vals, tension:0.25, borderColor:'#4ea3ff', backgroundColor:'rgba(78,163,255,0.15)', fill:true, pointRadius:2}]}, options:{scales:{x:{ticks:{color:'#adb5bd'}}, y:{ticks:{color:'#adb5bd'}}}, plugins:{legend:{labels:{color:'#e6e8ea'}}}}});
}

function renderClusters(clusters, total){
    if(!clusters || !clusters.length){ return; }
    const ctx = document.getElementById('clusterChart');
    const labels = clusters.map(c=> 'C'+c.cluster);
    const counts = clusters.map(c=> c.count);
    const colors = clusters.map(c=> c.cluster === -1 ? '#ff6b6b' : '#51cf66');
    new Chart(ctx,{type:'doughnut', data:{labels, datasets:[{data:counts, backgroundColor:colors}]}, options:{plugins:{legend:{labels:{color:'#e6e8ea'}}}}});
    const div = document.getElementById('clusterSummary');
    div.innerHTML = clusters.map(c=>`<div class=tag>C${c.cluster}: {count: ${c.count}, avg_dP: ${c.avg_dP_kW?.toFixed?.(2)} kW}</div>`).join('');
}

function renderEvents(events){
    if(!events) return;
    // Scatter: x = start timestamp index, y = dP_on_kW, color by cluster
    const ctx = document.getElementById('eventsScatter');
    const palette = ['#ff6b6b','#51cf66','#339af0','#845ef7','#ffa94d','#15aabf'];
    const points = events.map((e,i)=>({x:i, y:e.dP_on_kW, c:e.cluster}));
    const datasets = [];
    // group by cluster
    const byC = {};
    points.forEach(p=>{ byC[p.c] = byC[p.c] || []; byC[p.c].push(p); });
    Object.keys(byC).forEach((cid,idx)=>{
        const color = cid == -1 ? '#495057' : palette[idx % palette.length];
        datasets.push({label:'C'+cid, data:byC[cid], parsing:false, showLine:false, pointRadius:4, borderColor:color, backgroundColor:color});
    });
    new Chart(ctx,{type:'scatter', data:{datasets}, options:{scales:{x:{ticks:{color:'#adb5bd'}, title:{display:true,text:'Event index',color:'#adb5bd'}}, y:{ticks:{color:'#adb5bd'}, title:{display:true,text:'ΔP kW',color:'#adb5bd'}}}, plugins:{legend:{labels:{color:'#e6e8ea'}}}}});

    // Table (first 40)
    const tbl = document.getElementById('eventsTable');
    const header = `<tr><th>Start</th><th>ΔP kW</th><th>Duur (min)</th><th>Energy kWh</th><th>Cluster</th></tr>`;
    const rows = events.slice(0,40).map(e=>`<tr><td>${fmtTS(e.t_on)}</td><td>${e.dP_on_kW?.toFixed?.(2)}</td><td>${e.duration_min ?? ''}</td><td>${e.energy_kWh? e.energy_kWh.toFixed(3):''}</td><td>${e.cluster}</td></tr>`).join('');
    tbl.innerHTML = header + rows;
}

load();
</script>
</body>
</html>
    """
    html = html.replace("__DAYS__", str(days)).replace("__GEN_AT__", datetime.utcnow().isoformat()+"Z")
    return HTMLResponse(content=html, headers={"Cache-Control":"no-store, max-age=0"})


@router.get("/overview", response_class=HTMLResponse, summary="Unified dashboard (clusters, devices, sessions)")
def overview(last_days: int = Query(default=7, ge=1, le=30), feature_set: str = Query(default="extended", pattern="^(basic|extended)$")):
    html = """
<!DOCTYPE html>
<html lang=\"en\">
<head>
<meta charset=\"utf-8\" />
<title>NILM Overview</title>
<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\" />
<style>
body{font-family:system-ui,Arial,sans-serif;background:#101317;color:#e6e8ea;margin:0;padding:1rem 1.25rem 3rem}
h1{margin:.2rem 0 1rem;font-size:1.5rem}
h2{margin:1.5rem 0 .6rem;font-size:1.1rem}
table{border-collapse:collapse;width:100%;font-size:.72rem}
th,td{padding:4px 6px;border-bottom:1px solid #24303b;text-align:left;vertical-align:top}
th{background:#182229;position:sticky;top:0}
.tag{display:inline-block;background:#24303b;padding:2px 6px;border-radius:4px;margin:2px;font-size:.65rem}
.grid{display:grid;gap:1.2rem;grid-template-columns:repeat(auto-fill,minmax(340px,1fr))}
.card{background:#161d23;padding:.9rem 1rem;border-radius:10px;box-shadow:0 1px 2px rgba(0,0,0,.5)}
.warn{color:#ffa94d}
input,select{background:#202a31;color:#e6e8ea;border:1px solid #32414d;padding:4px 6px;border-radius:4px}
.small{font-size:.65rem;opacity:.7}
code{background:#1e272e;padding:2px 4px;border-radius:4px}
</style>
<script src=\"https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js\"></script>
</head>
<body>
 <h1>NILM Overview</h1>
 <div style=\"margin-bottom:1rem\">Laatste <b id=\"daysLbl\"></b> dagen · Feature set <b id=\"fsLbl\"></b> · <label>Wijzig dagen <input id=\"daysInput\" type=number min=1 max=30 style=width:70px></label> <label style=margin-left:.5rem>Feature <select id=\"featureSetSel\"><option value=basic>basic</option><option value=extended selected>extended</option></select></label> <label style=margin-left:.5rem><input type=checkbox id=\"debugChk\"> Debug</label> <button id=\"reloadBtn\">Herlaad</button> · <button id=\"autoLabelBtn\" title=\"Past suggesties toe en slaat labels op\">Auto-label</button> <label class=small><input type=checkbox id=\"overwriteChk\" checked> overwrite</label> · <a href=\"/docs\">API docs</a> <span id=\"statusLbl\" class=small style=margin-left:.5rem></span></div>
 <div class=\"grid\">
    <div class=card>
     <h2>Clusters</h2>
     <canvas id=clusterDonut height=200></canvas>
     <table id=clustersTbl></table>
    </div>
    <div class=card>
     <h2>Devices (geaggregeerd)</h2>
     <canvas id=deviceBar height=220></canvas>
     <table id=devicesTbl></table>
    </div>
    <div class=card>
     <h2>Sessions (top 12 op energie)</h2>
     <table id=sessionsTbl></table>
    </div>
    <div class=card>
     <h2>Baseload (nacht)</h2>
     <canvas id=baseLine height=200></canvas>
    </div>
 </div>
 <p class=small>Tip: gebruik <code>/clusters?recluster_noise=true&noise_eps=...&noise_min_samples=...</code> voor fijnmazige splitsing. Deze pagina haalt nu losse API calls parallel op.</p>
 <div class=small id=foot></div>
<script>
const params = new URLSearchParams(location.search);
const days = Number(params.get('last_days')||%DAYS%);
const featureSet = params.get('feature_set') || '%FEATURE_SET%';
const debugFlag = (params.get('debug')||'false') === 'true';
document.getElementById('daysInput').value = days;
document.getElementById('featureSetSel').value = featureSet;
document.getElementById('daysLbl').textContent = days;
document.getElementById('fsLbl').textContent = featureSet;
document.getElementById('debugChk').checked = debugFlag;
document.getElementById('reloadBtn').onclick = () => {
    const d = document.getElementById('daysInput').value || days;
    const fs = document.getElementById('featureSetSel').value;
    const dbg = document.getElementById('debugChk').checked ? 'true' : 'false';
    location.search = '?last_days='+d+'&feature_set='+fs+'&debug='+dbg;
};
document.getElementById('autoLabelBtn').onclick = async () => {
    const btn = document.getElementById('autoLabelBtn');
    const status = document.getElementById('statusLbl');
    btn.disabled = true; status.textContent = 'Bezig met auto-label...';
    try{
        const ow = document.getElementById('overwriteChk').checked ? 'true' : 'false';
        const r = await fetch(`/autolabel?last_days=${days}&feature_set=${featureSet}&overwrite=${ow}`, {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'});
        const j = await r.json();
        status.textContent = `Auto-label klaar: toegepast ${j.applied?.length||0}, overgeslagen ${j.skipped?.length||0}`;
        await load();
    }catch(e){ status.textContent = 'Fout bij auto-label: '+e; }
    finally{ btn.disabled = false; setTimeout(()=> status.textContent='', 5000); }
};

function fmt(v,dec=2){ if(v==null||isNaN(v)) return ''; return Number(v).toFixed(dec); }

async function fetchJson(url){ const r = await fetch(url); if(!r.ok) throw new Error(url+' -> '+r.status); return r.json(); }

async function load(){
    try {
        const [clusters, devices, sessions, scan] = await Promise.all([
            fetchJson(`/clusters?last_days=${days}&feature_set=${featureSet}&debug=${debugFlag}`),
            fetchJson(`/devices?last_days=${days}&feature_set=${featureSet}`),
            fetchJson(`/sessions?last_days=${days}`),
            fetchJson(`/scan?last_days=${days}`)
        ]);
        renderClusters(clusters.clusters||[], debugFlag);
        renderDevices(devices.devices||[]);
        renderSessions(sessions.sessions||[]);
        renderBaseload(scan.baseload||{});
        document.getElementById('foot').textContent = 'Generated '+new Date().toISOString();
    } catch(e){
        document.body.innerHTML = '<h2>Load error</h2><pre>'+e+'</pre>';
    }
}

function renderClusters(list, debugOn){
    const tbl = document.getElementById('clustersTbl');
    if(!list.length){ tbl.innerHTML='<tr><td>Geen clusters</td></tr>'; return; }
    list.sort((a,b)=> (b.total_energy_kWh||0)-(a.total_energy_kWh||0));
    const head = '<tr><th>ID</th><th>Label</th><th>Bron</th><th>Conf</th><th>Count</th><th>ΔP avg</th><th>Dur med</th><th>E tot</th><th>E evt</th>' + (debugOn? '<th>Trace</th>' : '') + '</tr>';
    const rows = list.slice(0,50).map(c=>{
        const label = c.label||c.suggested_label||'';
        const src = c.label_source || (c.suggested_label? 'suggested': '');
        const conf = c.label_confidence!=null ? fmt(c.label_confidence,2) : '';
        let trace = '';
        if(debugOn && Array.isArray(c.rule_trace)){
            const hits = c.rule_trace.filter(r=>r.matched).map(r=> r.reason? `${r.rule} · ${r.reason}` : r.rule);
            trace = hits.join('; ');
        }
        return `<tr><td>${c.cluster}</td><td>${label}</td><td>${src}</td><td>${conf}</td><td>${c.count}</td><td>${fmt(c.avg_dP_kW)}</td><td>${fmt(c.median_duration_min)}</td><td>${fmt(c.total_energy_kWh,3)}</td><td>${fmt(c.avg_event_energy_kWh,3)}</td>` + (debugOn? `<td style="max-width:260px">${trace}</td>`:'') + `</tr>`;
    }).join('');
    tbl.innerHTML = head+rows;
    // donut
    const ctx = document.getElementById('clusterDonut');
    const labels = list.map(c=>'C'+c.cluster);
    const data = list.map(c=> c.total_energy_kWh||0);
    new Chart(ctx,{type:'doughnut',data:{labels,datasets:[{data}]}});
}

function renderDevices(list){
    const tbl = document.getElementById('devicesTbl');
    if(!list.length){ tbl.innerHTML='<tr><td>Geen devices</td></tr>'; return; }
    const head = '<tr><th>Naam</th><th>Clusters</th><th>Events</th><th>ΔP avg</th><th>E kWh</th><th>Share %</th></tr>';
    const rows = list.slice(0,25).map(d=>`<tr><td>${d.name}</td><td>${(d.clusters||[d.cluster]).join(',')}</td><td>${d.events}</td><td>${fmt(d.avg_dP_kW)}</td><td>${fmt(d.total_energy_kWh,3)}</td><td>${fmt(d.energy_share_pct,1)}</td></tr>`).join('');
    tbl.innerHTML = head+rows;
    const ctx = document.getElementById('deviceBar');
    const labels = list.map(d=> d.name);
    const data = list.map(d=> d.total_energy_kWh);
    new Chart(ctx,{type:'bar',data:{labels,datasets:[{label:'kWh',data}]},options:{scales:{y:{beginAtZero:true}}}});
}

function renderSessions(list){
    const tbl = document.getElementById('sessionsTbl');
    if(!list.length){ tbl.innerHTML='<tr><td>Geen sessions</td></tr>'; return; }
    list.sort((a,b)=> (b.total_energy_kWh||0)-(a.total_energy_kWh||0));
    const head = '<tr><th>Label</th><th>Cluster(s)</th><th>Events</th><th>Duur min</th><th>E kWh</th><th>%</th></tr>';
    const rows = list.slice(0,12).map(s=>`<tr><td>${s.label||''}</td><td>${s.cluster}</td><td>${s.n_events}</td><td>${fmt(s.duration_min)}</td><td>${fmt(s.total_energy_kWh,3)}</td><td>${fmt(s.energy_share_pct,1)}</td></tr>`).join('');
    tbl.innerHTML = head+rows;
}

function renderBaseload(base){
    const labels = Object.keys(base).sort();
    const vals = labels.map(k=> base[k]);
    const ctx = document.getElementById('baseLine');
    new Chart(ctx,{type:'line',data:{labels,datasets:[{label:'kW',data:vals,tension:.25,borderColor:'#4ea3ff',backgroundColor:'rgba(78,163,255,0.15)',fill:true}]}});
}

load();
</script>
</body>
</html>
        """
    html = html.replace('%DAYS%', str(last_days)).replace('%FEATURE_SET%', feature_set)
    return HTMLResponse(content=html, headers={"Cache-Control":"no-store, max-age=0"})


# Alias /dashboard (so you can try both) + simple root index
@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
def dashboard_alias(last_days: int = Query(default=7, ge=1, le=30), feature_set: str = Query(default="extended", pattern="^(basic|extended)$")):
    return overview(last_days=last_days, feature_set=feature_set)


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def index():
    return HTMLResponse("""
    <html><head><title>NILM API</title><style>body{font-family:system-ui;background:#111;color:#eee;padding:1.2rem;}</style></head>
    <body>
    <h1>NILM API</h1>
    <ul>
      <li><a href='/overview'>/overview</a> – Dashboard</li>
      <li><a href='/viz'>/viz</a> – Quick viz</li>
      <li><a href='/clusters'>/clusters</a></li>
      <li><a href='/devices'>/devices</a></li>
      <li><a href='/sessions'>/sessions</a></li>
      <li><a href='/autolabel'>/autolabel</a> (GET dry-run)</li>
      <li><a href='/docs'>/docs</a> – OpenAPI</li>
    </ul>
    </body></html>
    """)
