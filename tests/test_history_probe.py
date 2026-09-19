"""Tests for the privacy-preserving historical coverage probe."""

from datetime import date

from ha_garmin.exceptions import GarminAPIError
from ha_garmin.history_probe import (
    _error_category,
    summarize_body_battery_reports,
    summarize_daily_stress,
    summarize_steps,
)


def _scalar_values(value: object) -> list[object]:
    if isinstance(value, dict):
        return [item for child in value.values() for item in _scalar_values(child)]
    if isinstance(value, list | tuple):
        return [item for child in value for item in _scalar_values(child)]
    return [value]


def test_daily_stress_summary_reports_coverage_without_values() -> None:
    payload = {
        "calendarDate": "2026-09-01",
        "stressValuesArray": [
            [1_756_704_000_000, -1],
            [1_756_704_180_000, 25],
            [1_756_704_360_000, 30],
        ],
        "bodyBatteryValuesArray": [
            [1_756_704_000_000, "MEASURED", 80, 1],
            [1_756_704_180_000, "MEASURED", 79, 1],
        ],
    }

    summary = summarize_daily_stress(payload, date(2026, 9, 1))

    assert summary["response_date_matches"] is True
    assert summary["stress"]["usable_samples"] == 2
    assert summary["stress"]["sentinel_rows"] == 1
    assert summary["stress"]["median_interval_seconds"] == 180.0
    assert summary["body_battery"]["usable_samples"] == 2
    scalars = _scalar_values(summary)
    assert 25 not in scalars
    assert 30 not in scalars
    assert 80 not in scalars
    assert 79 not in scalars


def test_body_battery_summary_uses_descriptor_indexes() -> None:
    payload = [
        {
            "date": "2026-09-01",
            "bodyBatteryValueDescriptorDTOList": [
                {
                    "bodyBatteryValueDescriptorKey": "bodyBatteryLevel",
                    "bodyBatteryValueDescriptorIndex": 0,
                },
                {
                    "bodyBatteryValueDescriptorKey": "timestamp",
                    "bodyBatteryValueDescriptorIndex": 1,
                },
            ],
            "bodyBatteryValuesArray": [
                [70, 1_756_704_000_000],
                [68, 1_756_704_300_000],
            ],
        }
    ]

    summary = summarize_body_battery_reports(payload, date(2026, 9, 1))

    assert summary["requested_date_present"] is True
    assert summary["body_battery"]["usable_samples"] == 2
    assert summary["body_battery"]["median_interval_seconds"] == 300.0


def test_steps_summary_omits_step_values() -> None:
    payload = [
        {"startGMT": "2026-09-01T12:00:00Z", "steps": 100},
        {"startGMT": "2026-09-01T12:15:00Z", "steps": 200},
    ]

    summary = summarize_steps(payload)

    assert summary["response_rows"] == 2
    assert summary["rows_with_step_counts"] == 2
    assert summary["median_interval_seconds"] == 900.0
    scalars = _scalar_values(summary)
    assert 100 not in scalars
    assert 200 not in scalars


def test_error_summary_omits_exception_message() -> None:
    summary = _error_category(
        GarminAPIError("request failed with secret-token-value", status_code=503)
    )

    assert summary == {
        "status": "error",
        "category": "api_error",
        "exception_type": "GarminAPIError",
        "http_status": 503,
    }
    assert "secret-token-value" not in repr(summary)
