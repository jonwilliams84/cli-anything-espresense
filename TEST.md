# Tests

The suite runs against synthetic data on disk — no live companion, cluster,
broker or node is ever contacted.

```bash
python3 -m pytest cli_anything/espresense/tests/ -v          # 1714 tests
python3 -m pytest cli_anything/espresense/tests/ \
  --cov=cli_anything --cov-fail-under=90 -q                  # CI gate shape
ruff check cli_anything/ && ruff format --check cli_anything/
bandit -r cli_anything/ -ll -x '*/tests/*,*/test_*.py,*/conftest.py'
```

Current state: **1714 tests, all four gates green.**

## What is covered where

| Area | Unit tests | E2E / workflow tests |
|---|---|---|
| YAML round-trip, rooms rename/rotate, node fix-up | `test_core.py` | `test_cli.py`, `test_cli_refine.py` |
| Polygon/bounds maths, overlaps | `test_geometry.py` | `test_cli_geometry.py` |
| `config doctor` | `test_validate.py` | `test_cli_refine.py` |
| Floor CRUD, retag, bounds | `test_floors_core.py` | `test_cli_refine.py`, `test_cli_geometry.py` |
| Device registry + tuning paths | `test_config_devices.py`, `test_settings_core.py` | `test_cli_settings_devices.py` |
| Per-node firmware HTTP client | `test_mqtt_stream_nodes.py` | `test_cli.py` |
| MQTT pub/sub payload handling | `test_mqtt.py`, `test_mqtt_stream_nodes.py` | `test_cli.py` |
| **Global settings (`/api/settings` + MQTT mirror)** | `test_core.py` (`TestGlobalSettings*`, `TestMqttPublishGlobalSetting`) | `test_cli_refine.py` (`TestCompanionSettings*`, `TestMqttSetGlobal`) |
| **Live presence queries (`devices whereis/occupancy`, `mqtt distances/node-status`)** | `test_core.py` (`TestParseDistancePayload`, `TestTopicParsing`, `TestAggregateDistances`, `TestNearest`, `TestDistanceRows`, `TestDistanceSnapshot`, `TestAggregateStatus`, `TestStatusSnapshot`, `TestOccupancy`, `TestWhereis`) | `test_full_e2e.py` (`TestDevicesWhereisE2E`, `TestDevicesOccupancyE2E`, `TestMqttDistancesE2E`, `TestMqttNodeStatusE2E`, `TestPresenceWorkflow`) |
| **History analytics (`history trail` movement summary)** | `test_core.py` (`TestHistoryTrail`) | `test_full_e2e.py` (`TestHistoryTrailE2E`, `TestHistoryWorkflow`) |
| **Node health snapshots (`mqtt telemetry`)** | `test_core.py` (`TestParseTelemetryPayload`, `TestAggregateNodeTelemetry`, `TestTelemetryRows`, `TestTelemetrySnapshot`) | `test_full_e2e.py` (`TestMqttTelemetryE2E`, `TestNodeHealthWorkflow`) |
| **Fleet room-usage analytics (`history heatmap`)** | `test_core.py` (`TestHistoryHeatmap`) | `test_full_e2e.py` (`TestHistoryHeatmapE2E`, `TestRoomUsageWorkflow`) |
| **Device sighting detail (`mqtt sightings` — rssi/exp_rssi per room per device, `strongest` + `closest` room)** | `test_core.py` (`TestParseSightingPayload` (alias normalisation, numeric-string coercion, non-object/broken/empty rejection), `TestSightingTopicParsing` (bare 4-segment shape only — `/distance`, `/distance_raw`, telemetry/status/settings topics never become sightings), `TestAggregateSightings` (samples/min-max/latest/last_ts, filters, neighbour records dropped, strongest≠closest, rssi-less and distance-less devices), `TestSightingsRows` (closest-first ordering, single flags, no-distance rooms last), `TestSightingsSnapshot` (topic filter + forwarded filters)) | `test_full_e2e.py` (`TestMqttSightingsE2E`: missing-broker abort, JSON snapshot with messages/samples/strongest/closest, filter pass-through, human signal table, nothing-heard line, `--help`; `TestSightingsWorkflow`: sightings and distances agree on devices×rooms for the same records — distances reads the numeric `distance` field only, sightings also coerces string-valued ones and keeps the rssi the distance view drops —, human table covers every device in the JSON snapshot) |
| **Config drift detection (`config diff` running vs deployed)** | `test_core.py` (`TestConfigDiff`: scalar/nested/list diffs, index paths, secret redaction incl. list-index paths, ruamel documents) | `test_full_e2e.py` (`TestConfigDiffE2E`: identical, drift + exit 1, push-without-restart `added`, redaction, human lines, `--against` offline file-vs-file, companion-unreachable abort, `--help`; `TestConfigDiffWorkflow`: push-with-restart matches, push-without-restart drifts, `companion config-get` tells the same story) |
| **Device distance calibration (`node calibrate`, `calibration compute`)** | `test_core.py` (`TestComputeRssiAt1m`: identity at 1 m, formula inversion, absorption scaling, sub-metre readings, ValueError guards, string inputs; `TestNodeCalibrate`: request params, HTTP error → `NodeError`, bad-JSON fallback, guards) | `test_full_e2e.py` (`TestNodeCalibrateE2E`: JSON parse, positive-distance guard, clean `NodeError` abort, `--help`; `TestCalibrationComputeE2E`: JSON shape, negative RSSI positional via `COORD_SETTINGS`, custom `--absorption`, zero-distance abort, `--help`; `TestCalibrationWorkflow`: calibrate → compute → `node config-set --rssi-at-1m`, identity at 1 m) |
| **Retained MQTT read-back + broker inventory (`mqtt settings`, `mqtt devices`, `mqtt get`, `mqtt topics`)** | `test_core.py` (`TestParseMqttPayload`: JSON/scalars/broken-json/empty never raise; `TestRetainedFilter`, `TestLatestByTopic` (retained beats newer live), `TestMqttSettingsView` (device-config and per-node topics never misattribute), `TestWatchCarriesRetainFlag`: `watch()` records carry `msg.retain`) | `test_full_e2e.py` (`TestMqttSettingsE2E`: dump parsed + redacted, `--reveal`, `--key` present/absent + exit 1, nothing-retained message, missing-broker abort, `--help`; `TestMqttGetE2E`: retained readback, live-only warns, not-found exit 1, per-topic filtering, empty-topic abort, `--help`; `TestMqttReadbackWorkflow`: `set-global` → `settings` / `get` verify over an in-memory broker with MQTT `+`-wildcard matching, `--no-retain` writes never become stored state). Per-device fingerprints: unit-tested in `test_core.py` (`TestDeviceConfigsView`: `<...>/settings/<id>/config` shape matching, settings/per-node neighbour topics never misattribution, malformed/empty topics, retained-only and `latest_by_topic` composition) and in `test_full_e2e.py` (`TestMqttDevicesE2E`: parsed dump, in-flight exclusion, human lines, `--device` present/absent + exit 1, nothing-retained, missing-broker abort, custom prefix, `--help`; `TestMqttDeviceReadbackWorkflow`: `set-device` → `devices` / `get` verify, settings- and device-config families never bleed, `--no-retain` write never becomes stored state). The family is complete in v0.14.0: `mqtt topics` inventories every retained topic under `<prefix>/#` in one listen — unit-tested in `test_core.py` (`TestClassifyTopic`: prefix-agnostic family shapes, set-before-bare-suffix rule order, unknown → `other`; `TestTopicsView`: retained-only fold, retained-beats-newer-live composition, foreign-prefix drop, sorted rows + family counts, `parse_payload` never raising, and agreement with `settings_view` / `device_configs_view`) and in `test_full_e2e.py` (`TestMqttTopicsE2E`: JSON snapshot with families, parsed payloads + sizes, secret redaction / `--reveal`, `--family` and `--topic` filters, `--prefix` scoping, human table with no secret leak, nothing-retained hint, missing-broker abort, `--help`; `TestMqttTopicsWorkflow`: `set-node` + `set-global` + `set-device` all verified in one inventory call, `--no-retain` write never inventoried, inventory agrees with the dedicated `mqtt settings` / `mqtt devices` read-backs) |
| Docs stay in sync with the CLI | `test_docs_sync.py` | — |
| REPL loop, prompt-toolkit skin, stream/watch edge cases, direct-node group, remaining CLI error/no-op branches | `test_refine_hardening.py` | — |
| CLI error-path contract (`_abort`: stderr + exit 1, no traceback) and last core gaps (calibration wrappers, `FileSource` backup failure, kubectl-missing, node-direct HTTP errors, rotate validation, transport wrapping, `python -m` entry) | `test_error_paths.py` | — |

Conventions the tests pin (see CLAUDE.md "Gotchas"): the `--file` option is
uniform across config commands, signed coordinates parse everywhere, secret
redaction is the default, and the two SKILL.md copies are byte-identical.
