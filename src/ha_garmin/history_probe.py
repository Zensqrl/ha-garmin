"""Read-only probe for Garmin's historical intraday data coverage.

The probe deliberately emits metadata only. It never writes raw Garmin payloads
or metric values to stdout or its optional JSON report.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from getpass import getpass
from itertools import pairwise
from pathlib import Path
from typing import Any

from .auth import GarminAuth
from .client import GarminClient
from .exceptions import (
    GarminAPIError,
    GarminAuthError,
    GarminConnectError,
    GarminMFARequired,
    GarminRateLimitError,
)

DEFAULT_OFFSETS = (1, 7, 27, 28, 29, 30, 31, 32, 33, 90, 180, 365)
_STRESS_COLUMNS = ("timestamp", "stressLevel")
_BODY_BATTERY_COLUMNS = (
    "timestamp",
    "bodyBatteryStatus",
    "bodyBatteryLevel",
    "bodyBatteryVersion",
)


def _descriptor_index(
    descriptors: Any, wanted: str, fallback: tuple[str, ...]
) -> int | None:
    if isinstance(descriptors, list):
        for descriptor in descriptors:
            if not isinstance(descriptor, dict):
                continue
            key = next(
                (
                    value
                    for name, value in descriptor.items()
                    if name.endswith("DescriptorKey")
                ),
                None,
            )
            index = next(
                (
                    value
                    for name, value in descriptor.items()
                    if name.endswith("DescriptorIndex")
                ),
                None,
            )
            if key == wanted and isinstance(index, int) and index >= 0:
                return index
    return fallback.index(wanted) if wanted in fallback else None


def _epoch_ms(value: Any, *, assume_utc: bool = False) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        timestamp = int(value)
        return timestamp * 1000 if abs(timestamp) < 100_000_000_000 else timestamp
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if stripped.isdigit():
        return _epoch_ms(int(stripped))
    try:
        parsed = datetime.fromisoformat(stripped.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        if not assume_utc:
            return None
        parsed = parsed.replace(tzinfo=UTC)
    return int(parsed.timestamp() * 1000)


def _iso_utc(timestamp_ms: int | None) -> str | None:
    if timestamp_ms is None:
        return None
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC).isoformat()


def _series_summary(
    rows: Any,
    timestamp_index: int | None,
    value_index: int | None,
    *,
    negative_values_are_sentinels: bool = False,
) -> dict[str, Any]:
    total_rows = len(rows) if isinstance(rows, list) else 0
    points: list[tuple[int, float]] = []
    sentinel_rows = 0
    malformed_rows = 0
    timestamp_rows = 0
    numeric_value_rows = 0
    null_value_rows = 0
    other_value_rows = 0
    row_widths: set[int] = set()

    if (
        isinstance(rows, list)
        and timestamp_index is not None
        and value_index is not None
    ):
        required_index = max(timestamp_index, value_index)
        for row in rows:
            if not isinstance(row, list | tuple) or len(row) <= required_index:
                malformed_rows += 1
                continue
            row_widths.add(len(row))
            timestamp = _epoch_ms(row[timestamp_index])
            value = row[value_index]
            if timestamp is not None:
                timestamp_rows += 1
            if value is None:
                null_value_rows += 1
            elif isinstance(value, int | float) and not isinstance(value, bool):
                numeric_value_rows += 1
            else:
                other_value_rows += 1
            if (
                timestamp is None
                or isinstance(value, bool)
                or not isinstance(value, int | float)
            ):
                malformed_rows += 1
                continue
            if negative_values_are_sentinels and value < 0:
                sentinel_rows += 1
                continue
            points.append((timestamp, float(value)))

    points.sort(key=lambda point: point[0])
    timestamps = sorted({point[0] for point in points})
    intervals = [
        (later - earlier) / 1000
        for earlier, later in pairwise(timestamps)
        if later > earlier
    ]
    first = timestamps[0] if timestamps else None
    last = timestamps[-1] if timestamps else None
    return {
        "response_rows": total_rows,
        "usable_samples": len(points),
        "sentinel_rows": sentinel_rows,
        "malformed_rows": malformed_rows,
        "timestamp_rows": timestamp_rows,
        "numeric_value_rows": numeric_value_rows,
        "null_value_rows": null_value_rows,
        "other_value_rows": other_value_rows,
        "row_widths": sorted(row_widths),
        "first_sample_utc": _iso_utc(first),
        "last_sample_utc": _iso_utc(last),
        "span_hours": round((last - first) / 3_600_000, 3)
        if first is not None and last is not None
        else None,
        "median_interval_seconds": round(statistics.median(intervals), 3)
        if intervals
        else None,
        "largest_gap_seconds": round(max(intervals), 3) if intervals else None,
    }


def summarize_daily_stress(payload: Any, requested_date: date) -> dict[str, Any]:
    """Summarize a dailyStress response without returning metric values."""
    if not isinstance(payload, dict):
        payload = {}
    stress_descriptors = payload.get("stressValueDescriptorsDTOList")
    battery_descriptors = payload.get("bodyBatteryValueDescriptorDTOList")
    response_date = payload.get("calendarDate")
    return {
        "response_present": bool(payload),
        "response_date_matches": response_date == requested_date.isoformat()
        if isinstance(response_date, str)
        else None,
        "stress": _series_summary(
            payload.get("stressValuesArray"),
            _descriptor_index(stress_descriptors, "timestamp", _STRESS_COLUMNS),
            _descriptor_index(stress_descriptors, "stressLevel", _STRESS_COLUMNS),
            negative_values_are_sentinels=True,
        ),
        "body_battery": _series_summary(
            payload.get("bodyBatteryValuesArray"),
            _descriptor_index(battery_descriptors, "timestamp", _BODY_BATTERY_COLUMNS),
            _descriptor_index(
                battery_descriptors, "bodyBatteryLevel", _BODY_BATTERY_COLUMNS
            ),
        ),
    }


def summarize_body_battery_reports(
    payload: Any, requested_date: date
) -> dict[str, Any]:
    """Summarize Body Battery report rows without returning metric values."""
    reports = (
        [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, list)
        else []
    )
    requested = next(
        (item for item in reports if item.get("date") == requested_date.isoformat()),
        reports[0] if len(reports) == 1 else {},
    )
    descriptors = requested.get("bodyBatteryValueDescriptorDTOList")
    return {
        "reports_returned": len(reports),
        "requested_date_present": any(
            item.get("date") == requested_date.isoformat() for item in reports
        ),
        "body_battery": _series_summary(
            requested.get("bodyBatteryValuesArray"),
            _descriptor_index(descriptors, "timestamp", _BODY_BATTERY_COLUMNS),
            _descriptor_index(descriptors, "bodyBatteryLevel", _BODY_BATTERY_COLUMNS),
        ),
    }


def summarize_steps(payload: Any, daily_total: Any = None) -> dict[str, Any]:
    """Summarize dailySummaryChart rows without returning step values."""
    rows = (
        [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, list)
        else []
    )
    timestamps: list[int] = []
    rows_with_step_counts = 0
    step_counts: list[float] = []
    timestamp_field_counts = {
        key: sum(row.get(key) is not None for row in rows)
        for key in ("startGMT", "endGMT", "timestampGMT", "timestamp")
    }
    for row in rows:
        if isinstance(row.get("steps"), int | float) and not isinstance(
            row.get("steps"), bool
        ):
            rows_with_step_counts += 1
            step_counts.append(float(row["steps"]))
        timestamp = next(
            (
                parsed
                for key in ("startGMT", "endGMT", "timestampGMT", "timestamp")
                if (
                    parsed := _epoch_ms(
                        row.get(key), assume_utc=key.casefold().endswith("gmt")
                    )
                )
                is not None
            ),
            None,
        )
        if timestamp is not None:
            timestamps.append(timestamp)

    timestamps = sorted(set(timestamps))
    intervals = [
        (later - earlier) / 1000
        for earlier, later in pairwise(timestamps)
        if later > earlier
    ]
    decreasing_transitions = sum(
        later < earlier for earlier, later in pairwise(step_counts)
    )
    valid_daily_total = (
        float(daily_total)
        if isinstance(daily_total, int | float) and not isinstance(daily_total, bool)
        else None
    )
    return {
        "response_rows": len(rows),
        "rows_with_step_counts": rows_with_step_counts,
        "rows_with_utc_timestamps": len(timestamps),
        "timestamp_field_counts": timestamp_field_counts,
        "nonnegative_step_rows": sum(value >= 0 for value in step_counts),
        "decreasing_step_transitions": decreasing_transitions,
        "all_step_rows_non_decreasing": decreasing_transitions == 0
        if len(step_counts) > 1
        else None,
        "daily_total_available": valid_daily_total is not None,
        "sum_matches_daily_total": sum(step_counts) == valid_daily_total
        if valid_daily_total is not None and step_counts
        else None,
        "last_matches_daily_total": step_counts[-1] == valid_daily_total
        if valid_daily_total is not None and step_counts
        else None,
        "max_matches_daily_total": max(step_counts) == valid_daily_total
        if valid_daily_total is not None and step_counts
        else None,
        "first_sample_utc": _iso_utc(timestamps[0] if timestamps else None),
        "last_sample_utc": _iso_utc(timestamps[-1] if timestamps else None),
        "median_interval_seconds": round(statistics.median(intervals), 3)
        if intervals
        else None,
        "largest_gap_seconds": round(max(intervals), 3) if intervals else None,
    }


def _error_category(error: Exception) -> dict[str, Any]:
    category = "unexpected_error"
    if isinstance(error, GarminRateLimitError):
        category = "rate_limited"
    elif isinstance(error, GarminAuthError):
        category = "authentication_error"
    elif isinstance(error, GarminAPIError):
        category = "api_error"
    return {
        "status": "error",
        "category": category,
        "exception_type": type(error).__name__,
        "http_status": getattr(error, "status_code", None),
    }


async def _probe_date(client: GarminClient, target_date: date) -> dict[str, Any]:
    result: dict[str, Any] = {"date": target_date.isoformat()}
    calls: tuple[tuple[str, Callable[[date], Awaitable[Any]]], ...] = (
        ("daily_stress", client.get_daily_stress),
        ("body_battery_report", client.get_body_battery),
    )
    for name, fetch in calls:
        try:
            payload = await fetch(target_date)
            if name == "daily_stress":
                summary = summarize_daily_stress(payload, target_date)
            else:
                summary = summarize_body_battery_reports(payload, target_date)
            result[name] = {"status": "ok", **summary}
        except Exception as error:
            result[name] = _error_category(error)

    try:
        steps_payload = await client.get_steps_data(target_date)
        daily_total: Any = None
        daily_total_lookup: dict[str, Any] = {"status": "ok", "date_present": False}
        try:
            daily_rows = await client.get_daily_steps(target_date, target_date)
            matching_row = next(
                (
                    row
                    for row in daily_rows
                    if row.get("calendarDate") == target_date.isoformat()
                ),
                daily_rows[0] if len(daily_rows) == 1 else {},
            )
            daily_total = matching_row.get("totalSteps")
            daily_total_lookup["date_present"] = bool(matching_row)
        except Exception as error:
            daily_total_lookup = _error_category(error)
        result["intraday_steps"] = {
            "status": "ok",
            **summarize_steps(steps_payload, daily_total),
            "daily_total_lookup": daily_total_lookup,
        }
    except Exception as error:
        result["intraday_steps"] = _error_category(error)
    return result


async def probe_history(
    client: GarminClient, target_dates: list[date]
) -> dict[str, Any]:
    """Probe dates sequentially to avoid creating a burst of Garmin requests."""
    rows = [await _probe_date(client, target_date) for target_date in target_dates]
    endpoint_status = {
        endpoint: {
            "ok_dates": sum(row[endpoint]["status"] == "ok" for row in rows),
            "error_dates": sum(row[endpoint]["status"] == "error" for row in rows),
        }
        for endpoint in ("daily_stress", "body_battery_report", "intraday_steps")
    }
    return {
        "schema_version": 1,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "privacy": "aggregate coverage metadata only; no health values or account identifiers",
        "dates": rows,
        "endpoint_status": endpoint_status,
    }


def _parse_dates(args: argparse.Namespace) -> list[date]:
    today = date.today()
    if args.dates:
        return sorted(
            {date.fromisoformat(item.strip()) for item in args.dates.split(",")}
        )
    offsets = (
        [int(item.strip()) for item in args.offsets.split(",")]
        if args.offsets
        else list(DEFAULT_OFFSETS)
    )
    if any(offset < 0 for offset in offsets):
        raise ValueError("Offsets must be zero or positive")
    return sorted({today - timedelta(days=offset) for offset in offsets})


def _authenticate(args: argparse.Namespace) -> GarminAuth:
    auth = GarminAuth(is_cn=args.cn)
    if auth.load_session(args.token_store):
        return auth

    email = os.getenv("GARMIN_EMAIL") or input("Garmin email: ").strip()
    password = os.getenv("GARMIN_PASSWORD") or getpass("Garmin password: ")
    if not email or not password:
        raise GarminAuthError("Credentials were not supplied")
    try:
        auth.login(email, password)
    except GarminMFARequired:
        auth.complete_mfa(getpass("Garmin MFA code: "))
    finally:
        password = ""
    auth.save_session(args.token_store)
    return auth


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only historical coverage probe. Output contains counts and "
            "timestamps, never raw Garmin health values."
        )
    )
    parser.add_argument(
        "--token-store",
        default=os.getenv("GARMIN_TOKEN_STORE"),
        required=os.getenv("GARMIN_TOKEN_STORE") is None,
        help="Private directory or JSON file used to reuse Garmin session tokens",
    )
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument(
        "--offsets",
        help="Comma-separated days before today (default samples 1 to 365 days)",
    )
    selection.add_argument(
        "--dates", help="Comma-separated explicit dates in YYYY-MM-DD format"
    )
    parser.add_argument("--output", type=Path, help="Optional sanitized JSON output")
    parser.add_argument(
        "--discard-session",
        action="store_true",
        help="Delete the locally cached session token after the probe",
    )
    parser.add_argument("--cn", action="store_true", help="Use Garmin's China domain")
    return parser


def main() -> int:
    """Run the command-line probe."""
    parser = _build_parser()
    args = parser.parse_args()
    try:
        target_dates = _parse_dates(args)
        auth = _authenticate(args)
        report = asyncio.run(
            probe_history(GarminClient(auth, is_cn=args.cn), target_dates)
        )
    except GarminConnectError as error:
        print(json.dumps(_error_category(error), indent=2))
        return 2
    except (OSError, ValueError) as error:
        print(
            json.dumps(
                {
                    "status": "error",
                    "category": "local_input_error",
                    "exception_type": type(error).__name__,
                },
                indent=2,
            )
        )
        return 2

    rendered = json.dumps(report, indent=2)
    print(rendered)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    if args.discard_session:
        auth.logout()
    total_successes = sum(
        counts["ok_dates"] for counts in report["endpoint_status"].values()
    )
    return 0 if total_successes else 1


if __name__ == "__main__":
    raise SystemExit(main())
