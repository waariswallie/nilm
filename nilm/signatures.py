"""Signature heuristics for appliances.

The goal is not to be perfect but to provide a reasonable baseline that we can
iterate on during experiments. Each signature encodes a preferred phase,
expected power change, and expected duration pattern.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

from .events import Event


@dataclass(slots=True)
class DeviceSignature:
    name: str
    expected_kw: float
    kw_tolerance: float
    min_duration_s: float
    max_duration_s: float
    preferred_phases: Iterable[str]
    notes: str = ""
    base_confidence: float = 0.6

    def score_event(
        self,
        event: Event,
        phase_groups: Optional[Dict[str, List[int]]] = None,
    ) -> float:
        # amplitude similarity
        amp_delta = abs(event.delta_kw - self.expected_kw)
        amp_score = max(0.0, 1.0 - amp_delta / max(self.kw_tolerance, 0.1))

        # duration similarity
        duration = event.duration_s
        if duration < self.min_duration_s:
            dur_score = max(0.0, duration / self.min_duration_s)
        elif duration > self.max_duration_s:
            dur_score = max(0.0, self.max_duration_s / duration)
        else:
            mid = (self.min_duration_s + self.max_duration_s) / 2
            dur_score = 1.0 - abs(duration - mid) / mid if mid else 1.0
            dur_score = max(dur_score, 0.6)

        # phase compatibility
        phase_score = 0.4
        if event.phase in self.preferred_phases:
            phase_score = 1.0
        elif not self.preferred_phases:
            phase_score = 0.7
        elif phase_groups:
            groups = phase_groups.get(event.phase, [])
            # boost if group numbers overlap with known hints from notes keywords
            for group in groups:
                if str(group) in self.notes:
                    phase_score = 0.9
                    break

        score = self.base_confidence * (0.4 * amp_score + 0.35 * dur_score + 0.25 * phase_score)
        return float(min(max(score, 0.0), 1.0))


SIGNATURES: Dict[str, DeviceSignature] = {
    "quooker": DeviceSignature(
        name="Quooker",
        expected_kw=2.0,
        kw_tolerance=1.0,
        min_duration_s=30,
        max_duration_s=300,
        preferred_phases=["l3"],
        notes="groep 4 fase l3",
        base_confidence=0.8,
    ),
    "magnetron": DeviceSignature(
        name="Magnetron",
        expected_kw=1.5,
        kw_tolerance=1.0,
        min_duration_s=30,
        max_duration_s=360,
        preferred_phases=["l2"],
        notes="groep 8",
    ),
    "vaatwasser": DeviceSignature(
        name="Vaatwasser",
        expected_kw=1.8,
        kw_tolerance=1.5,
        min_duration_s=1800,
        max_duration_s=7200,
        preferred_phases=["l1"],
        notes="groep 10",
        base_confidence=0.5,
    ),
    "wasmachine": DeviceSignature(
        name="Wasmachine",
        expected_kw=2.0,
        kw_tolerance=1.5,
        min_duration_s=1800,
        max_duration_s=7200,
        preferred_phases=["l2"],
        notes="groep 7",
    ),
    "droger": DeviceSignature(
        name="Droger",
        expected_kw=2.5,
        kw_tolerance=1.2,
        min_duration_s=1200,
        max_duration_s=5400,
        preferred_phases=["l3"],
        notes="groep 3",
    ),
    "oven": DeviceSignature(
        name="Oven",
        expected_kw=2.2,
        kw_tolerance=1.5,
        min_duration_s=900,
        max_duration_s=5400,
        preferred_phases=["l1", "l2"],
        notes="groep 11/12",
    ),
    "verwarming": DeviceSignature(
        name="Verwarming",
        expected_kw=1.5,
        kw_tolerance=1.0,
        min_duration_s=900,
        max_duration_s=14400,
        preferred_phases=["l2"],
        notes="groep 5",
    ),
    "koelkast": DeviceSignature(
        name="Koelkast",
        expected_kw=0.3,
        kw_tolerance=0.4,
        min_duration_s=300,
        max_duration_s=3600,
        preferred_phases=["l3"],
        notes="groep 2",
        base_confidence=0.4,
    ),
    "bel": DeviceSignature(
        name="Bel",
        expected_kw=0.1,
        kw_tolerance=0.2,
        min_duration_s=10,
        max_duration_s=180,
        preferred_phases=["l1"],
        notes="groep 9",
        base_confidence=0.3,
    ),
    "zonnepanelen": DeviceSignature(
        name="Zonnepanelen",
        expected_kw=-1.0,
        kw_tolerance=2.0,
        min_duration_s=600,
        max_duration_s=86400,
        preferred_phases=["l1"],
        notes="groep 13",
        base_confidence=0.5,
    ),
}


def match_event(event: Event, phase_groups: Optional[Dict[str, List[int]]] = None) -> Dict[str, float]:
    """Return a confidence map device→score for a single event."""
    scores: Dict[str, float] = {}
    for key, sig in SIGNATURES.items():
        scores[key] = sig.score_event(event, phase_groups)
    return scores


def best_device(event: Event, phase_groups: Optional[Dict[str, List[int]]] = None) -> tuple[str, float]:
    scores = match_event(event, phase_groups)
    best = max(scores.items(), key=lambda kv: kv[1])
    return best


__all__ = ["DeviceSignature", "SIGNATURES", "match_event", "best_device"]
