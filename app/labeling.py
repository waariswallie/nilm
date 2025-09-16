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
    """Add ratios & derived features for heuristic rules.

    Adds:
    - active_hours_ratio: fraction of 24h where events appear
    - evening_ratio: fraction of events starting 19–23h
    - afternoon_ratio: 12–17h
    - morning_ratio: 6–10h
    - night_ratio: 0–5h plus 23h
    - weekend_ratio: events on Sat/Sun (requires 'dows')
    - pulses_per_day: if not provided and first/last timestamps present
    - dP_std_kW alias to std (if missing)
    """
    for s in stats:
        hours = s.get("hours", []) or []
        dows = s.get("dows", []) or []
        if hours:
            uniq_hours = len(set(hours))
            s["active_hours_ratio"] = uniq_hours / 24.0
            evening = [h for h in hours if 19 <= h <= 23]
            afternoon = [h for h in hours if 12 <= h <= 17]
            morning = [h for h in hours if 6 <= h <= 10]
            night = [h for h in hours if h <= 5 or h == 23]
            s["evening_ratio"] = len(evening) / max(1, len(hours))
            s["afternoon_ratio"] = len(afternoon) / max(1, len(hours))
            s["morning_ratio"] = len(morning) / max(1, len(hours))
            s["night_ratio"] = len(night) / max(1, len(hours))
        else:
            s["active_hours_ratio"] = 0.0
            s["evening_ratio"] = 0.0
            s["afternoon_ratio"] = 0.0
            s["morning_ratio"] = 0.0
            s["night_ratio"] = 0.0
        if dows:
            weekend = [d for d in dows if d >= 5]
            s["weekend_ratio"] = len(weekend) / max(1, len(dows))
        else:
            s["weekend_ratio"] = 0.0
        # pulses_per_day fallback
        if "pulses_per_day" not in s or s.get("pulses_per_day") in (None, 0):
            first_ts = s.get("first_ts")
            last_ts = s.get("last_ts")
            if first_ts and last_ts:
                try:
                    # Expect ISO strings
                    ft = datetime.fromisoformat(str(first_ts).replace("Z", ""))
                    lt = datetime.fromisoformat(str(last_ts).replace("Z", ""))
                    span_days = (lt - ft).days + 1
                    if span_days > 0:
                        cnt = s.get("count", 0)
                        s["pulses_per_day"] = cnt / span_days
                except Exception:  # noqa: BLE001
                    pass
        # alias
        if "dP_std_kW" not in s and "dP_std" in s:
            s["dP_std_kW"] = s["dP_std"]
    return stats


def _build_rules(stat: Dict[str, Any], extended: bool) -> List[Tuple[str, float, str, bool]]:
    dp = stat.get("avg_dP_kW") or 0.0
    dur = stat.get("median_duration_min") or 0.0
    cnt = stat.get("count", 0)
    evening_ratio = stat.get("evening_ratio", 0.0)
    active_hours_ratio = stat.get("active_hours_ratio", 0.0)
    afternoon_ratio = stat.get("afternoon_ratio", 0.0)
    night_ratio = stat.get("night_ratio", 0.0)
    weekend_ratio = stat.get("weekend_ratio", 0.0)
    dP_max = stat.get("dP_max_kW", stat.get("dP_max", 0.0)) or 0.0
    dP_std = stat.get("dP_std_kW", 0.0) or 0.0
    pulses_per_day = stat.get("pulses_per_day", 0.0) or 0.0
    rules: List[Tuple[str, float, str, bool]] = []

    # Specific / high-power come first (ordering matters)
    # EV-lader: heel hoog, lange duur, veel 's nachts
    rules.append(("EV-lader", 0.9, "zeer hoog vermogen lange nacht-duren", dP_max >= 3.0 and dur >= 60 and night_ratio > 0.4))
    # Quooker / Waterkoker (Quooker al bekend)
    rules.append(("Quooker", 0.9, "hoge ΔP, korte duur", 1.5 <= dp <= 2.5 and dur < 8 and active_hours_ratio > 0.3))
    rules.append(("Waterkoker", 0.6, "korte hoge pieken", 2.0 <= dP_max <= 3.5 and dur < 5 and pulses_per_day >= 1 and cnt >= 3))
    # Oven vs Inductie
    rules.append(("Oven", 0.7, "stabiele hoge ΔP lange duur", 1.5 <= dp <= 3.5 and 20 <= dur <= 120 and dP_std < 0.25 and evening_ratio > 0.25))
    rules.append(("Inductie", 0.65, "hoge variatie korte avondduur", dP_max >= 2.5 and dP_std >= 0.5 and dur < 25 and evening_ratio > 0.4))
    # Droger varianten
    rules.append(("Droger-lang", 0.75, "lage ΔP lange duur", 0.4 <= dp <= 1.05 and dur >= 30 and cnt >= 5))
    rules.append(("Droger-pulse", 0.7, "lage ΔP middellange duur", 0.4 <= dp <= 1.05 and 8 <= dur < 30 and cnt >= 8))
    # Wasmachine / vaatwasser
    rules.append(("Vaatwasser", 0.68, "avond heating pulses", 1.5 <= dp <= 2.5 and 8 <= dur <= 40 and evening_ratio > 0.35))
    rules.append(("Wasmachine-pulse", 0.6, "mid ΔP korte-middellange duur", 1.1 <= dp <= 2.2 and 5 <= dur <= 25 and evening_ratio < 0.55 and active_hours_ratio > 0.25))
    # Koelkast / vriezer (heel veel korte, kleine pulsen)
    rules.append(("Koelkast/Vriezer", 0.55, "kleine korte frequente pulsen", 0.03 <= dp <= 0.15 and dur <= 25 and pulses_per_day >= 20 and active_hours_ratio > 0.7))
    if extended:
        rules.append(("Vaatwasser-alt", 0.55, "avond + hoge piek (dp_max)", dP_max >= 1.6 and dp < 1.5 and 8 <= dur <= 60 and evening_ratio > 0.4))
        rules.append(("Wasmachine-lang", 0.55, "lage-middellange ΔP lange median", 0.3 <= dp <= 1.2 and 30 <= dur <= 140 and pulses_per_day <= 6))
        rules.append(("Standby-blok", 0.5, "laag verbruik breed actief", 0.05 <= dp <= 0.25 and dur >= 30 and active_hours_ratio >= 0.8 and cnt >= 5))
        rules.append(("Warmtepomp/CV", 0.55, "middel ΔP brede activiteit", 0.3 <= dp <= 1.2 and dur >= 20 and active_hours_ratio > 0.6 and pulses_per_day >= 8))
        rules.append(("Airco", 0.5, "middag/avond koelen", 0.6 <= dp <= 2.0 and 15 <= dur <= 180 and afternoon_ratio > 0.35 and evening_ratio > 0.25))
        rules.append(("Koken/Inductie", 0.5, "hoge variatie korte duur", dP_max >= 2.0 and dP_std >= 0.35 and dur < 25 and evening_ratio > 0.4))
    rules.append(("Hoog-vermogen-onbekend", 0.4, "hoge ΔP maar geen andere match", dP_max > 2.5 and dur >= 4))
    return rules


def suggest_label(stat: Dict[str, Any], extended: bool = False, trace: bool = False) -> Optional[Dict[str, Any]]:
    cid = stat.get("cluster")
    if cid in (-1, None):
        return None
    rules = _build_rules(stat, extended=extended)
    rule_trace: List[Dict[str, Any]] = []
    for label, conf, reason, cond in rules:
        matched = bool(cond)
        if trace:
            rule_trace.append({"rule": label, "matched": matched, "reason": reason if matched else None})
        if matched:
            out = {"label": label, "confidence": conf, "reason": reason}
            if trace:
                out["rule_trace"] = rule_trace
            return out
    if trace:
        return {"label": None, "rule_trace": rule_trace}
    return None


def apply_suggestions(stats: List[Dict[str, Any]], store: LabelStore, extended: bool = False, debug: bool = False) -> List[Dict[str, Any]]:
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
        suggestion = suggest_label(s, extended=extended, trace=debug)
        s_out = dict(s)
        if existing:
            s_out["label"] = existing.label
            s_out["label_source"] = existing.source
            s_out["label_confidence"] = existing.confidence
            s_out["label_updated_at"] = existing.updated_at
        elif suggestion and suggestion.get("label"):
            s_out["suggested_label"] = suggestion["label"]
            s_out["suggested_confidence"] = suggestion["confidence"]
            s_out["suggested_reason"] = suggestion["reason"]
        if suggestion and "rule_trace" in suggestion:
            s_out["rule_trace"] = suggestion["rule_trace"]
        enriched.append(s_out)
    return enriched


def auto_label_clusters(stats: List[Dict[str, Any]], store: LabelStore, overwrite: bool = False, extended: bool = False) -> Dict[str, Any]:
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
        suggestion = suggest_label(s, extended=extended, trace=False)
        if not suggestion:
            continue
        if existing and not overwrite and existing.source == "manual":
            skipped.append({"cluster": cid, "label": existing.label, "reason": "manual_exists"})
            continue
        cl = store.set(cluster=cid, label=suggestion["label"], source="rule", confidence=suggestion.get("confidence"))
        applied.append({"cluster": cid, "label": cl.label, "confidence": cl.confidence, "reason": suggestion.get("reason")})
    return {"applied": applied, "skipped": skipped}
