# AGENTS.md

Python client library for the Garmin Connect cloud API, published to PyPI as `ha-garmin`. Its only consumer is the [home-assistant-garmin_connect](https://github.com/cyberjunky/home-assistant-garmin_connect) custom integration (sibling folder in this workspace).

See [README.md](README.md) for install/usage examples and [SECURITY.md](SECURITY.md) for vulnerability reporting.

## Commands

All targets assume a `.venv` in the repo root (the Makefile hardcodes `.venv/bin/*`).

```bash
make install     # pip install -e ".[dev]"
make format      # ruff format + ruff check --fix
make lint        # ruff check + ruff format --check + mypy src
make test        # pytest with coverage
make test-quick  # pytest, no coverage
make all         # format + lint + test (CI simulation)
```

Single test: `.venv/bin/pytest tests/test_client.py::test_name -v`

CI ([.github/workflows/ci.yml](.github/workflows/ci.yml)) runs lint on 3.11 and tests on 3.11–3.13. Minimum supported Python is 3.11 — do not use 3.12+ syntax.

## Architecture

| Module | Role |
|---|---|
| [auth.py](src/ha_garmin/auth.py) | Login, MFA, token refresh, session persistence |
| [client.py](src/ha_garmin/client.py) | All API calls + response normalization |
| [models.py](src/ha_garmin/models.py) | Pydantic models (`GarminModel` base, `extra="ignore"`, `populate_by_name=True`) |
| [const.py](src/ha_garmin/const.py) | Endpoint URLs |
| [exceptions.py](src/ha_garmin/exceptions.py) | `GarminConnectError` base; `GarminAuthError`, `GarminMFARequired`, `GarminRateLimitError`, `GarminAPIError` |
| [fit.py](src/ha_garmin/fit.py) | Binary FIT encoder for weight/body-composition uploads |

### Sync/async split

`GarminAuth` is **sync only**. `GarminClient` is **async only** — every HTTP call goes through `_request()`, which runs blocking `requests`/`curl_cffi` in `asyncio.to_thread`. Do not add `async` methods to auth, and do not call `requests` directly from client code.

### Auth strategy chain

`login()` walks an ordered list of strategies (mobile+cffi, mobile+requests, SSO widget+cffi, portal web+cffi, portal web+requests), each with its own anti-WAF delay. Only `GarminAuthError` and `GarminMFARequired` abort the chain — rate limits and 5xx fall through to the next strategy. When adding a strategy, preserve that contract.

### Client method naming

- `get_*()` — one endpoint, returns the (lightly processed) API response.
- `fetch_*_data()` — aggregate method the integration calls once per poll. Combines several `get_*()` calls into one flat dict of sensor-ready keys. Nine exist: core, activity, training, body, goals, gear, blood_pressure, menstrual, nutrition.
- `set_*` / `add_*` / `upload_*` — write operations backing HA services.

Adding an endpoint: URL in `const.py` → `get_*()` in `client.py` → surface it in the relevant `fetch_*_data()` → test in `tests/test_client.py`.

### Normalization contract

The integration depends on these transformations; changing them is a breaking change for downstream sensors:

- `startTimeGMT` is renamed to `startTime` (UTC datetime); **`startTimeLocal` is dropped**.
- `activityType` is flattened from a nested dict to a plain string (`"running"`).
- Activity payloads are filtered through `ACTIVITY_ESSENTIAL_KEYS`; device registration payloads are trimmed similarly. New fields the integration needs must be added to that set or they will be stripped.
- `*InSecs` fields are converted to minutes (e.g. `estimatedDurationMinutes`).
- `polyline` is exposed under `lastActivityRoute`, not `lastActivity`.

Garmin mixes GMT strings, local strings, timezone offsets and epoch milliseconds in the same payload. Never infer which one a field is from its value — check the field name and the neighbouring `*GMT`/`*Local`/offset fields. Emit either a UTC `datetime` or epoch **milliseconds**, document which, and cover the day boundary and a non-UTC timezone in tests.

### Safety helpers

Path parameters must go through `_assert_safe_url()`, `_validate_positive_int()`, or `_validate_uuid()` before being interpolated into a URL. Session files are written with symlink rejection — keep those checks when touching `save_session()`/`load_session()`.

## Conventions

- `mypy --strict` on `src/` with `py.typed` shipped — every public function needs full annotations. `fit.py` is the only module with broad `type: ignore`.
- Ruff line length 88, `E501` ignored (formatter handles it).
- Raise the specific exception subclass; callers distinguish transient (`GarminRateLimitError`) from fatal (`GarminAuthError`).
- Bump `version` in [pyproject.toml](pyproject.toml) and `__version__` in [\_\_init\_\_.py](src/ha_garmin/__init__.py) together when releasing.

## Releasing

This is a personal fork. Branching, merge-strategy, sync and upstream-PR rules come from the **fork-maintenance** skill; the cross-repo pipeline is automated by the `garmin-release` skill in the integration folder. This section records only what is specific to this repo.

```yaml
fork-profile:
  upstream: cyberjunky/ha-garmin
  upstream-default-branch: main
  fork-owner: Zensqrl
  deploy-target: wheel attached to a GitHub release, pinned by direct reference in the integration's manifest.json
  version-scheme: PEP 440 local version — <upstream base>+zs<n>, e.g. 0.1.38+zs1
  overlay:
    - .github/workflows/release.yml
    - .github/workflows/upstream-sync.yml
    - .github/prompts/          # all agent customization is fork-only
    - .github/agents/
    - .github/skills/
    - AGENTS.md                 # the Releasing section only
  verify:
    - make lint
    - make test
  release: garmin-release skill
```

**Never published to PyPI** — do not run `make publish` or `twine upload`. Pushing a `v<version>` tag triggers [release.yml](.github/workflows/release.yml), which verifies the tag matches `pyproject.toml`, builds the wheel and sdist on GitHub's runners, and attaches them to a GitHub release. The integration consumes that wheel URL directly from its `manifest.json`.

Fork builds carry a local version so they are unmistakable and sort above the upstream release they are based on: upstream `0.1.38` → `0.1.38+zs1`, tag `v0.1.38+zs1`, wheel `ha_garmin-0.1.38+zs1-py3-none-any.whl`. Reset the counter when the upstream base moves. If GitHub mangles the `+` in an asset filename, fall back to `.post1`.

Version bumps live on `main` only and never appear in an upstream PR.

## Tests

- `unittest.mock` only — no `responses`/`respx`/`aioresponses`. Client tests patch `_request` with `AsyncMock`; auth tests patch the strategy methods or `cffi_requests.get/post`.
- `asyncio_mode = "auto"` — do not add `@pytest.mark.asyncio`.
- [tests/conftest.py](tests/conftest.py) is intentionally empty; fixtures live in the test modules.
- Test payloads must be synthetic or sanitized. Never commit real Garmin responses, tokens, profile IDs, or personal health data.
- **`test_fetch_data.py` and `test_add_data.py` in the repo root are not pytest files** — they are manual scripts run against real credentials (`GARMIN_EMAIL`/`GARMIN_PASSWORD` or `.garmin_tokens.json`). Never collect them in CI or convert them to tests.
