from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Mapping, Tuple

from .events import Event


@dataclass
class DeviceSignature:
    name: str
    delta_kw: Tuple[float, float]
    duration_s: Tuple[int, int]
    phase_codes: Tuple[int, ...] = ()

    def score(self, event: Event, phase_groups: Mapping[str, Iterable[int]] | None) -> float:
        amp_score = _range_score(event.delta_kw, self.delta_kw)
        dur_score = _range_score(event.duration_s, self.duration_s)
        phase_score = 0.2
        if phase_groups is not None:
            codes = set(int(c) for c in self.phase_codes)
            hints = set(int(c) for c in phase_groups.get(event.phase, [])) if event.phase in phase_groups else set()
            if codes and hints:
                overlap = len(codes.intersection(hints))
                if overlap:
                    phase_score = 1.0
                else:
                    phase_score = 0.1
        return 0.45 * amp_score + 0.35 * dur_score + 0.2 * phase_score


def _range_score(value: float, bounds: Tuple[float, float]) -> float:
    lo, hi = bounds
    if lo <= value <= hi:
        return 1.0
    if value < lo:
        return max(0.0, 1.0 - ((lo - value) / max(lo, 1e-6)))
    return max(0.0, 1.0 - ((value - hi) / max(hi, 1e-6)))


def default_signatures() -> Dict[str, DeviceSignature]:
    return {
        "quooker": DeviceSignature("quooker", delta_kw=(1.2, 2.5), duration_s=(30, 900), phase_codes=(3, 4)),
        "droger": DeviceSignature("droger", delta_kw=(1.5, 3.5), duration_s=(180, 5400), phase_codes=(3, 4, 5)),
        "vaatwasser": DeviceSignature("vaatwasser", delta_kw=(1.2, 2.2), duration_s=(1800, 7200), phase_codes=(6, 7)),
        "wasmachine": DeviceSignature("wasmachine", delta_kw=(0.8, 2.0), duration_s=(2400, 8400), phase_codes=(8, 9, 10)),
        "baseload": DeviceSignature("baseload", delta_kw=(0.0, 0.5), duration_s=(60, 86400)),
    }


def best_device(event: Event, phase_groups: Mapping[str, Iterable[int]] | None = None,
                signatures: Mapping[str, DeviceSignature] | None = None) -> tuple[str, float]:
    sigs = signatures or default_signatures()
    best_name = "unknown"
    best_score = 0.0
    for name, sig in sigs.items():
        score = sig.score(event, phase_groups)
        if score > best_score:
            best_name = name
            best_score = score
    return best_name, best_score
