#!/usr/bin/env python
"""Command line entry point for the NILM pipeline."""
from __future__ import annotations

import argparse
import logging
from datetime import datetime
from typing import Iterable, Optional

from nilm.loader import DBConfig, load_power_data
from nilm.prep import PrepConfig, preprocess
from nilm.events import EventConfig, detect_events
from nilm.assign import AssignmentConfig, assign_events, build_device_timeseries, aggregate_daily_usage
from nilm.report import export_daily_usage, export_device_usage_db, export_timeseries, write_summary

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOGGER = logging.getLogger("nilm")


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run NILM pipeline on MeterData table")
    parser.add_argument("--start", required=True, help="Start timestamp (inclusive)")
    parser.add_argument("--end", required=True, help="End timestamp (exclusive)")
    parser.add_argument("--devices", help="Comma separated allow-list of devices", default=None)
    parser.add_argument("--tz", default="Europe/Amsterdam")
    parser.add_argument("--min-event-kw", type=float, default=0.15)
    parser.add_argument("--min-duration-sec", type=float, default=30.0)
    parser.add_argument("--smooth-sec", type=int, default=60)
    parser.add_argument("--export-db", action="store_true", help="Write aggregates to DeviceUsage table")
    parser.add_argument("--dry-run", action="store_true", help="Skip writing outputs")
    args_list = list(argv) if argv is not None else None
    return parser.parse_args(args_list)


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = parse_args(argv)
    LOGGER.info("Loading data from %s to %s", args.start, args.end)
    cfg = DBConfig.from_env()
    df = load_power_data(args.start, args.end, config=cfg, tz=args.tz)
    if df.empty:
        LOGGER.warning("No data returned for window")
        return 1

    LOGGER.info("Loaded %d rows", len(df))

    prep_cfg = PrepConfig(resample_seconds=60, smooth_seconds=args.smooth_sec)
    df_prep = preprocess(
        df,
        phase_columns=cfg.phase_columns,
        config=prep_cfg,
        delivered_col=cfg.delivered_column,
        received_col=cfg.received_column,
    )

    # Quality checks
    sample_diff = df_prep.index.to_series().diff().dropna().dt.total_seconds()
    if sample_diff.median() > 90:
        LOGGER.warning("Median sample interval %.1fs > 90s", sample_diff.median())

    null_ratio = df_prep.isna().mean().max()
    if null_ratio > 0.1:
        LOGGER.warning("High NULL ratio detected (%.2f%%)", null_ratio * 100)

    events_cfg = EventConfig(
        min_event_kw=args.min_event_kw,
        min_duration_s=args.min_duration_sec,
        hysteresis_kw=max(args.min_event_kw * 0.4, 0.05),
    )
    events = detect_events(df_prep, events_cfg)
    LOGGER.info("Detected %d events", len(events))
    if not events:
        LOGGER.warning("No events found")
        return 2

    phase_hints = df_prep.attrs.get("phase_groups", None)
    devices_filter = [d.strip().lower() for d in args.devices.split(",")] if args.devices else None
    assign_cfg = AssignmentConfig(min_confidence=0.25, supported_devices=devices_filter)
    assigned = assign_events(events, phase_groups=phase_hints, config=assign_cfg)
    LOGGER.info("Assigned %d events", len(assigned))
    if not assigned:
        LOGGER.warning("No assignments above confidence threshold")
        return 3

    timeseries = build_device_timeseries(assigned)
    daily = aggregate_daily_usage(timeseries)

    if args.dry_run:
        LOGGER.info("Dry-run mode: skipping file/database writes")
        LOGGER.info("Timeseries sample:\n%s", timeseries.head())
        LOGGER.info("Daily usage:\n%s", daily.head())
        return 0

    export_timeseries(timeseries)
    export_daily_usage(daily)
    write_summary(timeseries, daily)

    if args.export_db:
        LOGGER.info("Writing aggregates to DeviceUsage table")
        export_device_usage_db(daily, config=cfg)

    LOGGER.info("Pipeline finished")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
