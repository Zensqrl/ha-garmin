"""Compact, per-fetch provenance; never contains raw payloads or account IDs."""

from __future__ import annotations

from datetime import UTC, date, datetime
from math import isfinite
from typing import Any

from .exceptions import GarminAPIError


class FetchProvenance:
    """Track individual endpoint outcomes without masking authentication failures."""

    def __init__(self, requested_date: date) -> None:
        self.requested_date = requested_date.isoformat()
        self.sources: dict[str, dict[str, Any]] = {}

    async def read(self, source: str, func: Any, *args: Any) -> Any:
        """Read once; keep an empty response distinct from a failed request."""
        fetched_at = datetime.now(UTC).isoformat()
        query_date = next((a.isoformat() for a in args if isinstance(a, date)), None)
        metadata: dict[str, Any] = {
            "source": source.removesuffix("Fallback"),
            "requested_date": self.requested_date,
            "query_date": query_date,
            "source_date": None,
            "fetched_at": fetched_at,
            "outcome": "empty",
            "fallback_used": source.endswith("Fallback"),
        }
        try:
            result = await func(*args)
        except GarminAPIError:
            metadata["outcome"] = "error"
            self.sources[source] = metadata
            return None
        if result:
            metadata["outcome"] = "ok"
            if isinstance(result, dict):
                payload = (
                    result.get("dailySleepDTO") or result.get("hrvSummary") or result
                )
                if isinstance(payload, dict):
                    metadata["source_date"] = payload.get("calendarDate")
                if source.removesuffix("Fallback") == "trainingStatus":
                    # Existing status phrase selects the latest dated device,
                    # independently of the new primary-device load selection.
                    status = result.get("mostRecentTrainingStatus") or {}
                    entries = status.get("latestTrainingStatusData") or {}
                    dates = [
                        str(v["calendarDate"])
                        for v in entries.values()
                        if isinstance(v, dict)
                        and isinstance(v.get("calendarDate"), str)
                    ]
                    metadata["source_date"] = max(dates) if dates else None
        self.sources[source] = metadata
        return result

    def select(self, source: str, fallback: str) -> None:
        """Select an explicitly used fallback, retaining the first outcome."""
        original = self.sources[source]
        self.sources[source] = {
            **self.sources[fallback],
            "source": original["source"],
            "primary_outcome": original["outcome"],
        }


def normalize_training_load(
    payload: dict[str, Any], target_date: date
) -> dict[str, Any]:
    """Select a single device/date, preserving Garmin's field semantics."""
    status = payload.get("mostRecentTrainingStatus")
    entries = (
        status.get("latestTrainingStatusData") if isinstance(status, dict) else None
    )
    if not isinstance(entries, dict):
        return {}
    candidates = [
        (str(key), value) for key, value in entries.items() if isinstance(value, dict)
    ]
    if not candidates:
        return {}
    # Prefer the requested date, then primary device, then latest source date.
    # The stable map key is a tie-breaker only and is not exposed to consumers.
    _, selected = max(
        candidates,
        key=lambda pair: (
            pair[1].get("calendarDate") == target_date.isoformat(),
            pair[1].get("primaryTrainingDevice") is True,
            pair[1].get("calendarDate") or "",
            pair[0],
        ),
    )
    load = selected.get("acuteTrainingLoadDTO")
    if not isinstance(load, dict):
        load = {}

    def number(key: str) -> int | float | None:
        value = load.get(key)
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and isfinite(value)
        ):
            return value
        return None

    return {
        "acuteTrainingLoad": number("dailyTrainingLoadAcute"),
        "chronicTrainingLoad": number("dailyTrainingLoadChronic"),
        "trainingLoadRatio": number("dailyAcuteChronicWorkloadRatio"),
        "trainingLoadRatioStatus": load.get("acwrStatus")
        if isinstance(load.get("acwrStatus"), str)
        else None,
        "trainingLoadSourceDate": selected.get("calendarDate"),
        "trainingLoadPrimaryDevice": selected.get("primaryTrainingDevice") is True,
        "trainingLoadChronicMin": number("minTrainingLoadChronic"),
        "trainingLoadChronicMax": number("maxTrainingLoadChronic"),
    }
