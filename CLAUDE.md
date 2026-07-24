# cli-anything-espresense

Python Click CLI + REPL for operating an ESPresense deployment from the terminal (or by an agent). Abstracts three transports behind one command: the companion service (REST/WebSocket), per-node ESP32 firmware web servers (direct HTTP), and direct MQTT. Part of the `cli-anything-*` family (homeassistant, zigbee2mqtt).

## Stack / layout
- Python >=3.10, packaged with setuptools (namespace package `cli_anything`). Entry point `cli-anything-espresense` -> `cli_anything.espresense.espresense_cli:main`.
- Deps: click, prompt-toolkit, requests, websocket-client, ruamel.yaml, paho-mqtt.
- Source: `cli_anything/espresense/`
  - `espresense_cli.py` — Click CLI + REPL (all command groups)
  - `core/` — one module per concern: `companion_api.py`, `config_yaml.py`, `rooms.py`, `nodes.py`, `node_direct.py`, `devices.py`, `calibration.py`, `history.py`, `stream.py`, `mqtt.py`, `k8s_backend.py`, `project.py`
  - `utils/` — `companion_client.py` (requests Session), `yaml_io.py` (ruamel round-trip), `repl_skin.py`
  - `tests/test_core.py` — 16 unit tests, synthetic data, no live services
- `setup.py` reads `cli_anything/espresense/README.md` (NOT the repo-root README) as long_description — keep that file present.
- Skill manifest is duplicated at `skills/cli-anything-espresense/SKILL.md` and `cli_anything/espresense/skills/SKILL.md`.

## Commands
```bash
pip install -e .                                          # install
python3 -m pytest cli_anything/espresense/tests/ -v       # test (16 tests)
cli-anything-espresense --help                            # CLI help
cli-anything-espresense                                   # REPL (default, no subcommand)
```
No Makefile, no CI config, no lint setup. Every command supports `--json`.

## Config
- Connection profile at `~/.config/cli-anything-espresense.json` (created via `config save`). Env overrides like `CLI_ESPRESENSE_BASE_URL`.
- That profile holds broker creds and is gitignored — never commit it.

## Gotchas
- The companion REST API can READ config.yaml but cannot WRITE it. Writes go through `kubectl exec ... cat > config.yaml` against the running pod (`config_yaml.py` / `k8s_backend.py`), leaving a timestamped `.bak`. So `kubectl` is required for `companion config-push`, `companion restart`, and node/room mutations; MQTT-only and per-node HTTP commands work without it.
- The companion reloads config.yaml only on start, so mutating commands take `--restart`.
- `rooms rename`/`rooms rotate` also fix every node `room:` reference atomically (rotate supports cycles).

## Conventions
- MIT licensed. Adding a command group = a `core/` module + wiring in `espresense_cli.py` + tests in `test_core.py`.
