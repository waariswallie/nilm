from __future__ import annotations
import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any
import math


@dataclass
class ClusterLabel:
    cluster: int
    label: str
    source: str = "manual"  # manual|rule
    confidence: float | None = None
    updated_at: str | None = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if d["updated_at"] is None:
            d["updated_at"] = datetime.now(timezone.utc).isoformat()
        return d


class LabelStore:
    """Very lightweight JSON file based label store.

    Not concurrent safe for heavy multi-writes, but fine for this use case.
    """

    def __init__(self, path: str | Path = "labels.json") -> None:
        self.path = Path(path)
        self._cache: Dict[int, ClusterLabel] = {}
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                for item in data:
                    try:
                        cl = ClusterLabel(**item)
                        self._cache[cl.cluster] = cl
                    except Exception:
                        continue
            except Exception:
                pass
        self._loaded = True

    def all(self) -> Dict[int, ClusterLabel]:
        self._load()
        return self._cache

    def get(self, cluster: int) -> Optional[ClusterLabel]:
        self._load()
        return self._cache.get(cluster)

    def set(self, cluster: int, label: str, source: str = "manual", confidence: float | None = None) -> ClusterLabel:
        self._load()
        cl = ClusterLabel(cluster=cluster, label=label, source=source, confidence=confidence,
                          updated_at=datetime.now(timezone.utc).isoformat())
        self._cache[cluster] = cl
        self._persist()
        return cl

    def _persist(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        data = [c.to_dict() for c in self._cache.values()]
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self.path)


def enrich_cluster_stats(stats: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Add ratios for heuristic rules: active_hours_ratio, evening_ratio."""
    for s in stats:
        hours = s.get("hours", [])
        if hours:
            unique_hours = len(set(hours))
            s["active_hours_ratio"] = unique_hours / 24.0
            evening = [h for h in hours if 19 <= h <= 23]
            s["evening_ratio"] = len(evening) / max(1, len(hours))
        else:
            s["active_hours_ratio"] = 0.0
            s["evening_ratio"] = 0.0
    return stats


def suggest_label(stat: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Heuristic label suggestions based on aggregated cluster stats.

    Expected keys: cluster, avg_dP_kW, median_duration_min, count, evening_ratio, active_hours_ratio.
    Returns suggestion dict or None.
    """
    cid = stat.get("cluster")
    if cid in (-1, None):  # skip noise
        return None
    dp = stat.get("avg_dP_kW") or 0.0
    dur = stat.get("median_duration_min") or 0.0
    cnt = stat.get("count", 0)
    evening_ratio = stat.get("evening_ratio", 0.0)
    active_hours_ratio = stat.get("active_hours_ratio", 0.0)

    # Rules (simple initial set)
    # Quooker: high dp ~1.5-2.3 kW, short duration (<6), widely distributed hours
    if 1.5 <= dp <= 2.3 and dur < 6 and active_hours_ratio > 0.4:
        return {"label": "Quooker", "confidence": 0.85, "reason": "dp & short & many hours"}
    # Droger (warmtepomp/laag): dp 0.4-0.9, long median (>40)
    if 0.4 <= dp <= 0.95 and dur >= 40 and cnt <= 50:
        return {"label": "Droger", "confidence": 0.7, "reason": "low dp long"}
    # Vaatwasser: dp 1.6-2.4, median 8-25, avond georienteerd
    if 1.6 <= dp <= 2.4 and 8 <= dur <= 25 and evening_ratio > 0.4:
        return {"label": "Vaatwasser", "confidence": 0.65, "reason": "evening heating pulses"}
    # Wasmachine heating pulse: dp 1.2-2.2, median 5-20, mid active hours
    if 1.2 <= dp <= 2.2 and 5 <= dur <= 20 and active_hours_ratio > 0.25 and evening_ratio < 0.5:
        return {"label": "Wasmachine-pulse", "confidence": 0.55, "reason": "mid dp heating"}

    return None


def apply_suggestions(stats: List[Dict[str, Any]], store: LabelStore) -> List[Dict[str, Any]]:
    enriched = []
    for s in stats:
        cid_raw = s.get("cluster")
        if cid_raw is None:
            existing = None
        else:
            try:
                cid_int = int(cid_raw)
            except Exception:
                cid_int = None
            existing = store.get(cid_int) if cid_int is not None else None
        suggestion = suggest_label(s)
        s_out = dict(s)
        if existing:
            s_out["label"] = existing.label
            s_out["label_source"] = existing.source
            s_out["label_confidence"] = existing.confidence
            s_out["label_updated_at"] = existing.updated_at
        elif suggestion:
            s_out["suggested_label"] = suggestion["label"]
            s_out["suggested_confidence"] = suggestion["confidence"]
            s_out["suggested_reason"] = suggestion["reason"]
        enriched.append(s_out)
    return enriched
