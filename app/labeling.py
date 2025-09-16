from __future__ import annotations
import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
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

    def delete(self, cluster: int) -> bool:
        self._load()
        if cluster in self._cache:
            del self._cache[cluster]
            self._persist()
            return True
        return False


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
    Returns suggestion dict: {label, confidence, reason} or None.
    Priority order matters: first matching rule is returned.
    """
    cid = stat.get("cluster")
    if cid in (-1, None):  # skip noise
        return None
    dp = stat.get("avg_dP_kW") or 0.0
    dur = stat.get("median_duration_min") or 0.0
    cnt = stat.get("count", 0)
    evening_ratio = stat.get("evening_ratio", 0.0)
    active_hours_ratio = stat.get("active_hours_ratio", 0.0)
    # Rule order defines priority
    rules: List[Tuple[str, float, str, bool]] = []
    # Quooker / waterkoker (korte hoge pieken)
    rules.append(("Quooker", 0.9, "hoge ΔP, korte duur", 1.5 <= dp <= 2.5 and dur < 8 and active_hours_ratio > 0.3))
    # Droger (warmtepomp) langere runs (geclusterd tot pulses) -> langere median
    rules.append(("Droger-lang", 0.75, "lage ΔP lange duur", 0.4 <= dp <= 1.05 and dur >= 30 and cnt >= 5))
    # Droger korte modulatie blokken
    rules.append(("Droger-pulse", 0.7, "lage ΔP middellange duur", 0.4 <= dp <= 1.05 and 8 <= dur < 30 and cnt >= 8))
    # Vaatwasser heating (avond georienteerd)
    rules.append(("Vaatwasser", 0.68, "avond heating pulses", 1.5 <= dp <= 2.5 and 8 <= dur <= 40 and evening_ratio > 0.35))
    # Wasmachine heating pulse
    rules.append(("Wasmachine-pulse", 0.6, "mid ΔP korte-middellange duur", 1.1 <= dp <= 2.2 and 5 <= dur <= 25 and evening_ratio < 0.55 and active_hours_ratio > 0.25))
    # Onbekend groot apparaat (fallback high)
    rules.append(("Hoog-vermogen-onbekend", 0.4, "hoge ΔP maar geen andere match", dp > 2.5 and dur >= 4))
    for label, conf, reason, cond in rules:
        if cond:
            return {"label": label, "confidence": conf, "reason": reason}
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


def auto_label_clusters(stats: List[Dict[str, Any]], store: LabelStore, overwrite: bool = False) -> Dict[str, Any]:
    """Persist suggestions into label store.

    Returns dict with applied & skipped lists.
    overwrite=False will not replace existing manual labels.
    """
    applied: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for s in stats:
        cid_raw = s.get("cluster")
        if cid_raw in (-1, None):
            continue
        try:
            cid = int(cid_raw)
        except Exception:  # noqa: BLE001
            continue
        existing = store.get(cid)
        suggestion = suggest_label(s)
        if not suggestion:
            continue
        if existing and not overwrite and existing.source == "manual":
            skipped.append({"cluster": cid, "label": existing.label, "reason": "manual_exists"})
            continue
        cl = store.set(cluster=cid, label=suggestion["label"], source="rule", confidence=suggestion.get("confidence"))
        applied.append({"cluster": cid, "label": cl.label, "confidence": cl.confidence, "reason": suggestion.get("reason")})
    return {"applied": applied, "skipped": skipped}
