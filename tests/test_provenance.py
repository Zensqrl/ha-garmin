"""Provenance and training-load regression tests with synthetic endpoint data."""

from datetime import date
from unittest.mock import AsyncMock

import pytest

from ha_garmin import GarminAuth, GarminClient
from ha_garmin.exceptions import GarminAPIError, GarminAuthError, GarminRateLimitError
from ha_garmin.provenance import FetchProvenance, normalize_training_load

TODAY = date(2026, 9, 18)


def client_with_empty_endpoints():
    client = GarminClient(GarminAuth())
    client._request = AsyncMock(return_value={})
    for method in (
        "_get_user_summary_raw",
        "get_daily_steps",
        "_get_sleep_data_raw",
        "get_training_readiness",
        "get_morning_training_readiness",
        "get_lactate_threshold",
        "get_training_status",
        "get_endurance_score",
        "get_hill_score",
        "_get_hrv_data_raw",
        "get_power_to_weight",
        "get_activities",
    ):
        setattr(client, method, AsyncMock(return_value={}))
    return client


def status(day, primary=True, load=100):
    return {
        "calendarDate": day,
        "primaryTrainingDevice": primary,
        "acuteTrainingLoadDTO": {
            "dailyTrainingLoadAcute": load,
            "dailyTrainingLoadChronic": 80,
            "dailyAcuteChronicWorkloadRatio": 1.25,
            "acwrStatus": "OPTIMAL",
            "minTrainingLoadChronic": 50,
            "maxTrainingLoadChronic": 150,
        },
    }


def payload(entries):
    return {"mostRecentTrainingStatus": {"latestTrainingStatusData": entries}}


async def test_outcomes_do_not_invent_source_date():
    tracker = FetchProvenance(TODAY)
    await tracker.read("summary", AsyncMock(return_value={"totalSteps": 0}), TODAY)
    assert tracker.sources["summary"]["outcome"] == "ok"
    assert tracker.sources["summary"]["source_date"] is None
    await tracker.read("sleep", AsyncMock(return_value={}), TODAY)
    assert tracker.sources["sleep"]["outcome"] == "empty"
    await tracker.read(
        "hrv", AsyncMock(side_effect=GarminAPIError("private detail")), TODAY
    )
    assert tracker.sources["hrv"]["outcome"] == "error"
    assert "private detail" not in str(tracker.sources)


@pytest.mark.parametrize("error", [GarminAuthError, GarminRateLimitError])
async def test_auth_and_rate_limit_propagate(error):
    with pytest.raises(error):
        await FetchProvenance(TODAY).read(
            "summary", AsyncMock(side_effect=error()), TODAY
        )


async def test_summary_fallback_and_independent_sleep_date():
    client = client_with_empty_endpoints()
    client._get_user_summary_raw.side_effect = [
        {},
        {"calendarDate": "2026-09-17", "dailyStepGoal": 9000},
    ]
    client._get_sleep_data_raw.return_value = {
        "dailySleepDTO": {"calendarDate": "2026-09-18"}
    }
    data = await client.fetch_core_data(TODAY)
    assert data["_sources"]["summary"]["source_date"] == "2026-09-17"
    assert data["_sources"]["summary"]["primary_outcome"] == "empty"
    assert data["_sources"]["summary"]["fallback_used"] is True
    assert data["_sources"]["sleep"]["source_date"] == "2026-09-18"
    assert data["_sources"]["dailySteps"]["fallback_used"] is False


async def test_failed_summary_does_not_fetch_yesterday():
    client = client_with_empty_endpoints()
    client._get_user_summary_raw.side_effect = GarminAPIError("temporary")
    data = await client.fetch_core_data(TODAY)
    client._get_user_summary_raw.assert_awaited_once_with(TODAY)
    assert data["_sources"]["summary"]["outcome"] == "error"


async def test_last_device_sync_is_not_fetch_time():
    client = client_with_empty_endpoints()
    client._get_user_summary_raw.return_value = {
        "calendarDate": "2026-09-18",
        "dailyStepGoal": 9000,
        "lastSyncTimestampGMT": "2026-09-18T01:00:00.000",
    }
    result = await client.fetch_core_data(TODAY)
    assert result["_sources"]["summary"]["last_sync_at"] == "2026-09-18T01:00:00+00:00"
    assert (
        result["_sources"]["summary"]["fetched_at"]
        != result["_sources"]["summary"]["last_sync_at"]
    )


def test_primary_device_selection_and_zero_are_preserved():
    data = normalize_training_load(
        payload(
            {
                "quiet": None,
                "old": status("2026-09-17", load=900),
                "secondary": status("2026-09-18", False, 500),
                "primary": status("2026-09-18", True, 0),
            }
        ),
        TODAY,
    )
    assert data["acuteTrainingLoad"] == 0
    assert data["trainingLoadPrimaryDevice"] is True
    assert data["trainingLoadSourceDate"] == "2026-09-18"
    assert data["trainingLoadChronicMin"] == 50


def test_current_secondary_beats_old_primary_without_mixing():
    data = normalize_training_load(
        payload(
            {"old": status("2026-09-17"), "current": status("2026-09-18", False, 200)}
        ),
        TODAY,
    )
    assert data["acuteTrainingLoad"] == 200
    assert data["trainingLoadPrimaryDevice"] is False


@pytest.mark.parametrize("bad", [None, True, "100", float("nan")])
def test_invalid_numbers_never_become_zero(bad):
    result = normalize_training_load(
        payload({"watch": status("2026-09-18", load=bad)}), TODAY
    )
    assert result["acuteTrainingLoad"] is None


async def test_todays_load_survives_legacy_vo2_fallback():
    client = client_with_empty_endpoints()
    yesterday = payload({"watch": status("2026-09-17", load=50)})
    yesterday["mostRecentVO2Max"] = {"generic": {"vo2MaxValue": 45}}
    client.get_training_status.side_effect = [
        payload({"watch": status("2026-09-18", load=200)}),
        yesterday,
    ]
    result = await client.fetch_training_data(TODAY)
    assert result["vo2MaxValue"] == 45
    assert result["acuteTrainingLoad"] == 200
    assert result["_sources"]["trainingStatus"]["fallback_used"] is True
    assert result["_sources"]["trainingLoad"]["fallback_used"] is False
    assert result["_sources"]["trainingLoad"]["source_date"] == "2026-09-18"


async def test_hrv_fallback_keeps_returned_date():
    client = client_with_empty_endpoints()
    client._get_hrv_data_raw.side_effect = [
        GarminAPIError("temporary"),
        {"hrvSummary": {"calendarDate": "2026-09-16", "lastNightAvg": 50}},
    ]
    result = await client.fetch_training_data(TODAY)
    metadata = result["_sources"]["hrv"]
    assert metadata["query_date"] == "2026-09-17"
    assert metadata["source_date"] == "2026-09-16"
    assert metadata["primary_outcome"] == "error"
