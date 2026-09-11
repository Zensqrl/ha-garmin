---
name: "Add Garmin endpoint"
description: "Add a Garmin Connect API endpoint to the ha-garmin library: URL constant, get_*/write client method, fetch_*_data wiring, normalization, and tests."
argument-hint: "Endpoint to add, e.g. 'race predictions metrics' or 'delete an activity'"
agent: agent
---

Add a Garmin Connect API endpoint to the `ha_garmin` library. The endpoint is described in the user's request; if it is missing or ambiguous, ask for the Garmin URL path and whether it is a read or a write before writing code.

Work only inside the `ha-garmin` workspace folder. The downstream Home Assistant integration consumes this library as an exact PyPI pin, so nothing here is wired into sensors directly.

## 1. Decide the surface

- **Read** → a `get_*()` method, then surfaced through the matching `fetch_*_data()` aggregate so the integration gets it in one poll.
- **Write** (backs a Home Assistant action) → a `set_*` / `add_*` / `upload_*` method; no `fetch_*_data()` wiring.

## 2. URL constant

Add to `src/ha_garmin/const.py` under the matching comment section (`# Wellness endpoints`, `# Gear endpoints`, …), built from `GARMIN_CONNECT_API`:

```python
RACE_PREDICTIONS_URL = f"{GARMIN_CONNECT_API}/metrics-service/metrics/racepredictions"
```

Do not hardcode `connect.garmin.com` or the China domain — `_get_url()` handles CN routing at request time. Use `{{placeholders}}` in the constant only when the path is templated (see `MENSTRUAL_CALENDAR_URL`).

## 3. Client method

In `src/ha_garmin/client.py`, add the method next to its topical neighbours.

Read:

```python
async def get_race_predictions(
    self, target_date: date | None = None
) -> dict[str, Any]:
    """Get predicted race times."""
    if target_date is None:
        target_date = date.today()
    url = f"{RACE_PREDICTIONS_URL}/{target_date.isoformat()}"
    data = await self._request("GET", url)
    return data if isinstance(data, dict) else {}
```

Write: build the payload dict and return `await self._post_request(URL, payload)` or `self._put_request(URL, payload)`.

Rules:
- `_request()` is GET/params only — never pass a JSON body to it; use `_post_request` / `_put_request`.
- Always narrow the response: `data if isinstance(data, dict) else {}` (or `else []`). `_request` returns `{}` for 204/404.
- Interpolate path parameters only after `_validate_positive_int()` / `_validate_uuid()`. Any URL you build outside `_request` needs an explicit `_assert_safe_url()`.
- Never call `requests` / `curl_cffi` directly and never add `async` to `auth.py` — all blocking I/O goes through the existing `asyncio.to_thread` helpers.
- Full type annotations: `mypy --strict` runs on `src/`.
- Raise `GarminAPIError` for HTTP failures; let `GarminRateLimitError` and `GarminAuthError` propagate unchanged so callers can distinguish transient from fatal.
- Validate obviously bad input with `ValueError` before spending a request (see `set_hydration`).

## 4. Wire into a `fetch_*_data()` aggregate (reads only)

Pick the aggregate the integration already polls for that domain (core, activity, training, body, goals, gear, blood_pressure, menstrual, nutrition) and add the call there:

- Use `await self._safe_call(self.get_race_predictions, target_date)` — it logs and returns `None` on `GarminAPIError`, so one premium-only endpoint failing never blanks the whole poll.
- Flatten the result into **top-level, sensor-ready keys** on the returned dict. Do not nest, and do not add a new aggregate method unless the user asks.
- Apply the existing normalization contract, which downstream sensors depend on: rename `startTimeGMT` → `startTime` (UTC) and drop `startTimeLocal`; flatten `activityType` to a plain string; convert `*InSecs` fields to minutes; add any new activity field to `ACTIVITY_ESSENTIAL_KEYS` or the trimming step will strip it.
- Keep the docstring's `API calls:` line accurate — it records the per-poll request budget.

## 5. Tests

In `tests/test_client.py`, add tests to the relevant class following the existing style:

```python
async def test_get_race_predictions(self):
    """Test get_race_predictions returns the payload."""
    auth = _make_auth()
    client = GarminClient(auth)

    with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = {"time5K": 1500}
        result = await client.get_race_predictions(date(2025, 1, 1))

    assert result["time5K"] == 1500
    assert "2025-01-01" in mock_req.call_args[0][1]
```

Cover the non-dict/empty response path too. `unittest.mock` only — no `responses`/`respx`/`aioresponses`. `asyncio_mode = "auto"`, so no `@pytest.mark.asyncio`.

## 6. Verify and release

```bash
make lint
make test
```

Then bump `version` in `pyproject.toml`. Remind the user that the Home Assistant integration only picks the change up once that version is on PyPI and the pin is updated in **both** its `requirements.txt` and `manifest.json`; `pip install -e ../ha-garmin` works for local testing meanwhile.

## 7. Report

Summarize as a table of touched files, show the exact key(s) now appearing in the `fetch_*_data()` output, and state the new library version.
