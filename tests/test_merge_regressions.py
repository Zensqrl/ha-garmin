"""Combined upstream/fork contracts using only synthetic endpoint data."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from ha_garmin.client import _latest_heart_rate_sample, _sleep_stress_series
from tests.test_provenance import TODAY, client_with_empty_endpoints


@pytest.mark.parametrize(
    "bad",
    [float("nan"), float("inf"), -float("inf"), 1e300, 10**1000, True, "bad", None],
)
def test_sleep_stress_rejects_invalid_time(bad):
    assert _sleep_stress_series([{"startGMT": bad, "value": 20}]) == []


@pytest.mark.parametrize(
    "bad", [float("nan"), float("inf"), -float("inf"), -1, 10**1000, True, "bad", None]
)
def test_sleep_stress_rejects_invalid_value(bad):
    assert _sleep_stress_series([{"startGMT": 1792281600000, "value": bad}]) == []


async def test_combined_sleep_timelines_provenance_and_optional_hr():
    client = client_with_empty_endpoints()
    client._get_user_summary_raw.return_value = {
        "dailyStepGoal": 1,
        "calendarDate": "2026-09-18",
        "lastSyncTimestampGMT": "2026-09-18T01:00:00.000",
    }
    client._get_sleep_data_raw.return_value = {
        "dailySleepDTO": {
            "calendarDate": "2026-09-17",
            "avgSleepStress": 12,
            "sleepScores": {"overall": {"value": 81, "qualifierKey": "GOOD"}},
        },
        "sleepStress": [{"startGMT": 1789689600000, "value": 0}],
    }
    client.get_daily_stress = AsyncMock(
        return_value={
            "calendarDate": "2026-09-18",
            "stressValuesArray": [[1789689600000, 20]],
            "bodyBatteryValuesArray": [[1789689600000, "MEASURED", 70]],
        }
    )
    client.get_latest_heart_rate = AsyncMock(return_value={"latestHeartRate": 57})
    first = await client.fetch_core_data(TODAY)
    assert "latestHeartRate" not in first
    client.get_latest_heart_rate.assert_not_awaited()
    second = await client.fetch_core_data(TODAY, latest_heart_rate=True)
    third = await client.fetch_core_data(TODAY, latest_heart_rate=True)
    client.get_latest_heart_rate.assert_awaited_once()
    assert second["latestHeartRate"] == third["latestHeartRate"] == 57
    assert second["avgSleepStress"] == 12
    assert second["sleepScores"]["overall"]["qualifierKey"] == "GOOD"
    assert second["_sources"]["sleep"]["source_date"] == "2026-09-17"
    assert second["_sources"]["summary"]["last_sync_at"] == "2026-09-18T01:00:00+00:00"
    assert second["stressTimeline"] == [[1789689600000, 20]]
    assert second["bodyBatteryTimeline"] == [[1789689600000, 70]]
    assert second["sleepStress"] == [
        {"time": datetime.fromtimestamp(1789689600, UTC), "value": 0}
    ]
    assert client._get_sleep_data_raw.await_count == 3


async def test_empty_latest_hr_caches_only_with_known_sync():
    client = client_with_empty_endpoints()
    client.get_latest_heart_rate = AsyncMock(return_value={})
    for _ in range(2):
        assert await client._latest_heart_rate_since_sync("known") == {}
    client.get_latest_heart_rate.assert_awaited_once()
    for _ in range(2):
        assert await client._latest_heart_rate_since_sync(None) == {}
    assert client.get_latest_heart_rate.await_count == 3


async def test_missing_goal_success_falls_back_and_daily_negative_survives():
    client = client_with_empty_endpoints()
    client._get_user_summary_raw.side_effect = [
        {"calendarDate": "2026-09-18", "totalSteps": 0},
        {"calendarDate": "2026-09-17", "dailyStepGoal": 9000},
    ]
    client.get_daily_stress = AsyncMock(
        return_value={
            "stressValuesArray": [[1789689600000, -1]],
        }
    )
    result = await client.fetch_core_data(TODAY)
    assert result["_sources"]["summary"]["fallback_used"] is True
    assert result["_sources"]["summary"]["primary_outcome"] == "ok"
    assert result["stressTimeline"] == [[1789689600000, -1]]


@pytest.mark.parametrize(
    "bad",
    [
        float("nan"),
        float("inf"),
        -float("inf"),
        1e300,
        -(10**1000),
        10**1000,
        True,
        "bad",
        None,
    ],
)
def test_latest_hr_skips_invalid_timestamp_and_keeps_earlier_sample(bad):
    assert _latest_heart_rate_sample({"heartRateValues": [[bad, 57]]}) == {}
    assert _latest_heart_rate_sample({"heartRateValues": [[0, 61], [bad, 57]]}) == {
        "latestHeartRate": 61,
        "latestHeartRateTime": datetime(1970, 1, 1, tzinfo=UTC),
    }


@pytest.mark.parametrize(
    "bad", [float("nan"), float("inf"), -float("inf"), 57.0, 0, -1, True, "57", None]
)
def test_latest_hr_preserves_positive_integer_only_contract(bad):
    assert _latest_heart_rate_sample({"heartRateValues": [[0, bad]]}) == {}
    assert (
        _latest_heart_rate_sample({"heartRateValues": [[0, 61], [1000, bad]]})[
            "latestHeartRate"
        ]
        == 61
    )


async def test_optional_hr_invalid_samples_do_not_abort_core_or_cache_retry():
    client = client_with_empty_endpoints()
    client._get_user_summary_raw.return_value = {
        "dailyStepGoal": 1,
        "lastSyncTimestampGMT": "known",
    }
    client.get_daily_stress = AsyncMock(return_value={})
    client.get_daily_heart_rate = AsyncMock(
        return_value={"heartRateValues": [[float("inf"), 57]]}
    )
    first = await client.fetch_core_data(TODAY, latest_heart_rate=True)
    assert "latestHeartRate" not in first
    assert "_sources" in first
    assert client.get_daily_heart_rate.await_count == 2  # today and yesterday
    await client.fetch_core_data(TODAY, latest_heart_rate=True)
    assert (
        client.get_daily_heart_rate.await_count == 2
    )  # successful empty result cached
