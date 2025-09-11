from __future__ import annotations
import pandas as pd


def simple_threshold(df: pd.DataFrame, threshold: float = 50.0, min_duration: int = 2) -> pd.DataFrame:
    """Detecteer 'aan' segmenten boven drempel.

    Retourneert DataFrame met kolom 'on' (bool) en 'segment_id'.
    'min_duration' = minimaal aantal opeenvolgende samples.
    """
    mask = df["power_w"] >= threshold
    seg_id = 0
    segment_ids = []
    run_len = 0
    for is_on in mask:
        if is_on:
            if run_len == 0:
                seg_id += 1
            run_len += 1
            segment_ids.append(seg_id)
        else:
            if run_len and run_len < min_duration:
                # retroactively nullify short run
                for i in range(run_len):
                    segment_ids[-(i+1)] = 0
            run_len = 0
            segment_ids.append(0)
    out = pd.DataFrame(index=df.index)
    out["on"] = [sid > 0 for sid in segment_ids]
    out["segment_id"] = segment_ids
    return out
