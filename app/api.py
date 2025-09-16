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
from .labeling import LabelStore, enrich_cluster_stats, apply_suggestions
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
def scan(last_days: int = Query(default=settings.lookback_days, ge=1, le=30)):
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
        ef = cluster_events(ef)

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
def events_endpoint(last_days: int = Query(default=7, ge=1, le=30), limit: int = Query(default=200, ge=1, le=2000)):
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
        ef = cluster_events(ef)
    out = []
    if not ef.empty:
        for r in ef.tail(limit).to_dict(orient="records"):
            r["t_on"] = r["t_on"].isoformat() if r.get("t_on") else None
            r["t_off"] = r["t_off"].isoformat() if r.get("t_off") else None
            out.append(r)
    return {"count": len(out), "events": out}


@router.get("/clusters", summary="Cluster summaries with (suggested) labels")
def clusters_endpoint(last_days: int = Query(default=7, ge=1, le=30)):
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
        return {"clusters": []}
    ef = cluster_events(ef)
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
                "hours": hours,
            })
    cluster_stats = enrich_cluster_stats(cluster_stats)
    enriched = apply_suggestions(cluster_stats, _label_store)
    return {"clusters": enriched}


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
def devices_endpoint(last_days: int = Query(default=7, ge=1, le=30), include_noise: bool = False):
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
    ef = cluster_events(ef)

    # Aggregate per cluster
    clusters = {}
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
        }

    # Enrich with heuristics & suggestions
    raw_list = list(clusters.values())
    raw_list = enrich_cluster_stats(raw_list)
    enriched = apply_suggestions(raw_list, _label_store)

    # Build device list
    total_energy_all = sum(d.get("total_energy_kWh", 0.0) for d in enriched)
    devices_out = []
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
    return HTMLResponse(content=html)
