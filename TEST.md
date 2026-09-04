# Tests

The suite runs against synthetic data on disk — no live companion, cluster,
broker or node is ever contacted.

```bash
python3 -m pytest cli_anything/espresense/tests/ -v          # 1271 tests
python3 -m pytest cli_anything/espresense/tests/ \
  --cov=cli_anything --cov-fail-under=90 -q                  # CI gate shape
ruff check cli_anything/ && ruff format --check cli_anything/
bandit -r cli_anything/ -ll -x '*/tests/*,*/test_*.py,*/conftest.py'
```

Current state (v0.2.0): **1271 tests, 94% coverage, all four gates green.**

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
| Docs stay in sync with the CLI | `test_docs_sync.py` | — |

Conventions the tests pin (see CLAUDE.md "Gotchas"): the `--file` option is
uniform across config commands, signed coordinates parse everywhere, secret
redaction is the default, and the two SKILL.md copies are byte-identical.
