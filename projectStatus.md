# Personal Dashboard v0.1 — Garmin Status

- **Last updated:** 2026-09-22
- **Repository:** https://github.com/Zensqrl/ha-garmin
- **Companion HA integration:** https://github.com/Zensqrl/home-assistant-garmin_connect
- **Current goal:** Provide trustworthy Garmin recovery, activity, and training-load inputs for Personal Dashboard v0.1.
- **Status:** The Garmin P0 data contract is implemented, released, deployed, and live-verified in Home Assistant.
- **Readiness:** **READY**

## Completed work

- **Implemented:** Source/query dates, fetch outcomes, freshness and retained-value provenance; normalized acute/chronic training load and ratio/status; numeric HRV balanced-range bounds. Existing Body Battery/stress timeline support and Recorder exclusions were preserved.
- **Released (2026-09-19):** `ha-garmin 0.1.48+zs1` (`e93fd9f`) and Garmin Connect integration `3.0.18.0` (`992cec4`). The integration pins the published fork wheel.
- **Tested:** Library 172 passed (5 Windows-inapplicable POSIX skips); integration 170 passed; lint, type checks, GitHub CI, wheel inspection, and HACS validation passed. Hassfest's direct-reference warning is an expected fork-packaging exception.
- **Deployed (2026-09-19):** HACS installed `v3.0.18.0`; Home Assistant restarted; the existing config entry loaded without reinstall, reauthentication, entity migration, or Recorder changes.
- **Live-verified (2026-09-19):** All 148 prior entity IDs remained, six new entities populated, provenance reported successful current-source reads, scalar history was recorded, timeline arrays stayed out of Recorder, and no Garmin errors appeared in the post-restart system log.

## Available HA data contract

- New entities: `sensor.garmin_connect_acute_training_load`, `sensor.garmin_connect_chronic_training_load`, `sensor.garmin_connect_training_load_ratio`, `sensor.garmin_connect_training_load_ratio_status`, `sensor.garmin_connect_hrv_balanced_range_lower`, and `sensor.garmin_connect_hrv_balanced_range_upper`.
- Confirmed P0 inputs include Body Battery, charged/drained values, sleep score/duration, HRV and status, resting heart rate, steps/goal, training readiness, recovery time, training status, VO2 max, and recent activity/workout summaries.
- Applicable sensors expose `data_provenance` with source, requested/query/source dates, fetch time/outcome, fallback/retained state, last successful fetch, coordinator availability, and primary-device selection where relevant.
- Numeric scalar sensors are Recorder-compatible. `sensor.garmin_connect_body_battery_and_stress_timeline` exposes current live arrays, while its large `body_battery` and `stress` attributes are deliberately excluded from Recorder.

## Remaining work for v0.1

- **Coding:** None in the Garmin repositories. Dashboard logic must consume provenance and degrade gracefully when readiness/recovery values are absent or retained.
- **Testing:** Verify the assembled dashboard's recommendation behavior with fresh, stale, retained, and unavailable Garmin inputs. Long-term statistics need time to accumulate and were not backfilled.
- **Deployment:** None for the Garmin source. Dashboard implementation and deployment belong to the dashboard workstream.
- **Remaining gaps:** No precise training-duration algorithm, steps-versus-normal-by-time-of-day baseline, exact wake Body Battery derivation, or historical raw-timeline archive. These do not block v0.1.

## Deferred enhancements

- Dated historical retrieval services and repeat retention-boundary checks.
- Bounded/persistent normalized sample storage and historical backfill policy.
- Intraday pacing baselines, overnight recovery derivation, historical timeline charts, fuller load-focus analytics, and complete activity-window analytics.
- Garmin nutrition remains intentionally excluded; nutrition comes from MyFitnessPal.

## Actions needed from user

None.
