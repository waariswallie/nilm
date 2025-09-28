"""Reporting helpers for NILM pipeline."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd

from sqlalchemy import text

from .loader import db_engine, DBConfig


OUTPUT_DIR = Path("output")
DEVICE_TIMESERIES_CSV = OUTPUT_DIR / "device_timeseries.csv"
DEVICE_DAILY_CSV = OUTPUT_DIR / "daily_usage.csv"
SUMMARY_MD = OUTPUT_DIR / "summary.md"


def ensure_output_dir(path: Path = OUTPUT_DIR) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def export_timeseries(df: pd.DataFrame, path: Path = DEVICE_TIMESERIES_CSV) -> None:
    ensure_output_dir(path.parent)
    df.to_csv(path, index=False)
    try:
        df.to_parquet(path.with_suffix(".parquet"), index=False)
    except Exception:
        pass


def export_daily_usage(df: pd.DataFrame, path: Path = DEVICE_DAILY_CSV) -> None:
    ensure_output_dir(path.parent)
    df.to_csv(path, index=False)
    try:
        df.to_parquet(path.with_suffix(".parquet"), index=False)
    except Exception:
        pass


def _top_consumers(df: pd.DataFrame, top_n: int = 5) -> pd.DataFrame:
    if df.empty:
        return df
    agg = df.groupby("device", as_index=False)["kwh"].sum()
    agg = agg.sort_values(by="kwh", ascending=False)  # type: ignore[arg-type]
    return agg.head(top_n).reset_index(drop=True)


def _heatmap_hint(timeseries: pd.DataFrame) -> str:
    if timeseries.empty:
        return "Geen data beschikbaar."
    ts = timeseries.copy()
    ts["date"] = ts["timestamp"].dt.date
    ts["hour"] = ts["timestamp"].dt.hour
    heat = ts.groupby(["device", "hour"], as_index=False)["power_kw"].mean()
    lines = ["### Gemiddeld vermogen per uur"]
    for device, grp in heat.groupby("device"):
        top_hours = grp.sort_values(by="power_kw", ascending=False)  # type: ignore[arg-type]
        top_hours = top_hours.head(3)
        hour_str = ", ".join(
            f"{int(row['hour']):02d}u ({row['power_kw']:.2f} kW)" for _, row in top_hours.iterrows()
        )
        lines.append(f"- {device}: {hour_str}")
    return "\n".join(lines)


def write_summary(timeseries: pd.DataFrame, daily: pd.DataFrame, path: Path = SUMMARY_MD) -> None:
    ensure_output_dir(path.parent)
    lines = ["# NILM samenvatting", ""]
    if daily.empty:
        lines.append("Geen detecties gevonden in deze periode.")
    else:
        top = _top_consumers(daily)
        lines.append("## Top verbruikers")
        for _, row in top.iterrows():
            lines.append(f"- {row['device']}: {row['kwh']:.2f} kWh")
        lines.append("")
        lines.append(_heatmap_hint(timeseries))
        lines.append("")
        lines.append("## Onzekerheden")
        lines.append("Let op: heuristische toewijzing; combineer met handmatige validatie.")
    path.write_text("\n".join(lines), encoding="utf-8")


@dataclass(slots=True)
class DeviceUsageRecord:
    day: str
    device: str
    kwh: float
    duration_min: float
    confidence_mean: float


def export_device_usage_db(
    daily: pd.DataFrame,
    config: Optional[DBConfig] = None,
    table_name: str = "DeviceUsage",
) -> None:
    if daily.empty:
        return
    cfg = config or DBConfig.from_env()
    create_sql = text(
        f"""
        CREATE TABLE IF NOT EXISTS `{table_name}` (
            `day` DATE NOT NULL,
            `device` VARCHAR(64) NOT NULL,
            `kwh` DOUBLE NOT NULL,
            `duration_min` DOUBLE NOT NULL,
            `confidence_mean` DOUBLE NULL,
            PRIMARY KEY (`day`, `device`)
        ) ENGINE=InnoDB
        """
    )
    insert_sql = text(
        f"""
        INSERT INTO `{table_name}` (`day`, `device`, `kwh`, `duration_min`, `confidence_mean`)
        VALUES (:day, :device, :kwh, :duration_min, :confidence_mean)
        ON DUPLICATE KEY UPDATE
            `kwh` = VALUES(`kwh`),
            `duration_min` = VALUES(`duration_min`),
            `confidence_mean` = VALUES(`confidence_mean`)
        """
    )
    with db_engine(cfg) as engine:
        with engine.begin() as conn:
            conn.execute(create_sql)
            rows = daily.to_dict("records")
            for row in rows:
                conn.execute(insert_sql, {
                    "day": row["date"],
                    "device": row["device"],
                    "kwh": float(row["kwh"]),
                    "duration_min": float(row["duration_min"]),
                    "confidence_mean": float(row.get("confidence_mean", 0.0)),
                })


__all__ = [
    "ensure_output_dir",
    "export_timeseries",
    "export_daily_usage",
    "write_summary",
    "export_device_usage_db",
]
