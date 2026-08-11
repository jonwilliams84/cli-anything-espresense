# cli-anything-espresense

Python Click CLI + REPL for operating an ESPresense deployment from the terminal (or by an agent). Abstracts three transports behind one command: the companion service (REST/WebSocket), per-node ESP32 firmware web servers (direct HTTP), and direct MQTT. Part of the `cli-anything-*` family (homeassistant, zigbee2mqtt).

## Stack / layout
- Python >=3.10, packaged with setuptools (namespace package `cli_anything`). Entry point `cli-anything-espresense` -> `cli_anything.espresense.espresense_cli:main`.
- Deps: click, prompt-toolkit, requests, websocket-client, ruamel.yaml, paho-mqtt.
- Source: `cli_anything/espresense/`
  - `espresense_cli.py` — Click CLI + REPL (all command groups)
  - `core/` — one module per concern: `companion_api.py`, `config_source.py`, `config_yaml.py`, `validate.py`, `rooms.py`, `nodes.py`, `node_direct.py`, `devices.py`, `calibration.py`, `history.py`, `stream.py`, `mqtt.py`, `k8s_backend.py`, `project.py`
  - `utils/` — `companion_client.py` (requests Session), `yaml_io.py` (ruamel round-trip), `repl_skin.py`
  - `tests/` — 617 tests, 91% coverage, synthetic data, no live services
- `setup.py` reads `cli_anything/espresense/README.md` (NOT the repo-root README) as long_description — keep that file present.
- Skill manifest is duplicated at `skills/cli-anything-espresense/SKILL.md` and `cli_anything/espresense/skills/SKILL.md`.

## Commands
```bash
pip install -e .                                          # install
python3 -m pytest cli_anything/espresense/tests/ -v       # test (617 tests)
ruff check cli_anything/ && ruff format --check cli_anything/   # lint gate
bandit -r cli_anything/ -ll -x '*/tests/*,*/test_*.py'    # security gate
cli-anything-espresense --help                            # CLI help
cli-anything-espresense                                   # REPL (default, no subcommand)
```
CI (`.github/workflows/ci.yml`) gates on all three plus `--cov-fail-under=84`.
Ruff/pytest/coverage config lives in `pyproject.toml`, not in workflow flags.
Every command supports `--json`.

## Config
- Connection profile at `~/.config/cli-anything-espresense.json` (created via `config save`). Env overrides like `CLI_ESPRESENSE_BASE_URL`.
- That profile holds broker creds and is gitignored — never commit it.

## Gotchas
- The companion REST API can READ config.yaml but cannot WRITE it. Writes go through `kubectl exec ... cat > config.yaml` against the running pod (`k8s_backend.py`), leaving a timestamped `.bak`.
- `core/config_source.py` abstracts that: `K8sSource` (default) vs `FileSource` (`--file <path>`). Every config-reading/editing command takes `--file` and then needs no kubectl at all. Add `--file` (via the `config_file_option` decorator) to any NEW config command — `test_cli_refine.py::TestHelpSurface` asserts the option is uniform across the group, because a half-applied escape hatch is worse than none.
- `FileSource.push(restart=True)` cannot restart anything, so it returns `restart_skipped` instead of quietly dropping the flag.
- The companion reloads config.yaml only on start, so mutating commands take `--restart`.
- `rooms rename`/`rooms rotate` also fix every node `room:` reference atomically (rotate supports cycles). `core/validate.py` (`config doctor`) DETECTS that same drift; it is pure/read-only and exits 1 on error so it can gate a push.
- New coordinate sequences must be built with `yaml_io.flow_seq()`, or ruamel dumps them as block ladders (`-   - 5.0`) next to the inline `[[0, 0], [4, 0]]` style the rest of the hand-authored file uses.
- `nodes delete` (companion runtime state) and `nodes remove-from-config` (config.yaml) are different operations; retiring a node needs both.

## Conventions
- MIT licensed. Adding a command group = a `core/` module + wiring in `espresense_cli.py` + unit tests for the core module + E2E CLI tests (`CliRunner`) asserting `--json` parses and `--help` works.
- Keep the two SKILL.md copies (`skills/` and `cli_anything/espresense/skills/`) byte-identical.
