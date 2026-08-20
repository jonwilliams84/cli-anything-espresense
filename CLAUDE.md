# cli-anything-espresense

Python Click CLI + REPL for operating an ESPresense deployment from the terminal (or by an agent). Abstracts three transports behind one command: the companion service (REST/WebSocket), per-node ESP32 firmware web servers (direct HTTP), and direct MQTT. Part of the `cli-anything-*` family (homeassistant, zigbee2mqtt).

## Stack / layout
- Python >=3.10, packaged with setuptools (namespace package `cli_anything`). Entry point `cli-anything-espresense` -> `cli_anything.espresense.espresense_cli:main`.
- Deps: click, prompt-toolkit, requests, websocket-client, ruamel.yaml, paho-mqtt.
- Source: `cli_anything/espresense/`
  - `espresense_cli.py` — Click CLI + REPL (all command groups)
  - `core/` — one module per concern: `companion_api.py`, `config_source.py`, `config_yaml.py`, `validate.py`, `geometry.py`, `floors.py`, `rooms.py`, `nodes.py`, `node_direct.py`, `devices.py`, `config_devices.py`, `settings.py`, `calibration.py`, `history.py`, `stream.py`, `mqtt.py`, `k8s_backend.py`, `project.py`
  - `utils/` — `companion_client.py` (requests Session), `yaml_io.py` (ruamel round-trip), `repl_skin.py`
  - `tests/` — 1227 tests, 94% coverage, synthetic data, no live services
- `setup.py` reads `cli_anything/espresense/README.md` (NOT the repo-root README) as long_description — keep that file present.
- Skill manifest is duplicated at `skills/cli-anything-espresense/SKILL.md` and `cli_anything/espresense/skills/SKILL.md`.

## Commands
```bash
pip install -e .                                          # install
python3 -m pytest cli_anything/espresense/tests/ -v       # test (1227 tests)
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
- `core/geometry.py` is pure maths (no I/O, no YAML, no config shape) so `validate.check` can call it and stay read-only. It raises `GeometryError` — not `ValueError`/`float()`'s `ValueError` — for non-numeric coordinates precisely so report builders (`rooms geometry`, `rooms locate`) can skip a bad row instead of tracebacking.
- `geometry.overlaps` is a documented heuristic, and its most important property is a NEGATIVE: rooms sharing a wall or corner must never be reported as overlapping, or every real floor plan lights up. Probes are vertices *plus edge midpoints* — vertices alone miss two rectangles that half-overlap on the same y range. Tests in `test_geometry.py::TestOverlaps` pin both directions.
- `floors retag` (change a floor `id`) must rewrite node `floors:` lists in the same call, exactly like `rooms rename` does for `room:`; `validate.DANGLING_FLOOR_REF` detects the half-done version.
- Commands taking signed coordinates as positional args need `context_settings=COORD_SETTINGS` (`ignore_unknown_options`), otherwise click parses `rooms move Office -2 0` as an option `-2` and half the coordinate plane is unreachable. Currently on `rooms move`, `rooms locate`, `floors set-bounds`, `nodes set-point`; `test_cli_geometry.py::TestSignedCoordinateArguments` locks it in.
- config.yaml has two halves and both are now covered: *structural* (`floors`/`rooms`/`nodes`/`devices:`) and *behavioural* (`timeout`, `mqtt`, `gps`, `locators`, `optimizers`, ...). `core/settings.py` owns the second one and is deliberately schema-free — dotted paths, not one Click option per key, because companion releases rename tuning keys and an option list would rot. It REFUSES paths into the structural blocks (`settings set nodes.0.room`) and names the command to use instead; bypassing `rooms.py`/`nodes.py` would skip the cross-reference repair that is the whole point of those modules.
- `settings show`/`settings get` redact `mqtt.password` and anything matching `settings.SECRET_HINTS` unless `--reveal`. That output routinely lands in issues and agent transcripts, so redaction is the default and opting out is explicit.
- `core/config_devices.py` (config.yaml `devices:`) vs `core/devices.py` (companion runtime `/api/device/*`) is the same split as `nodes remove-from-config` vs `nodes delete` — retiring a beacon needs both. The on-disk reference-RSSI key is `rssi@1m`; `RSSI_KEY` is the one place that spelling is written, the CLI spells it `--rssi-at-1m`, and reads also accept the `rssi_at_1m` variant seen in the wild.
- New keys/ids the harness writes go through `yaml_io.dq()` (double-quoted) for the same reason coordinate lists go through `flow_seq()`: hand-authored configs quote `"rssi@1m"` and `"irk:abc"`, and bare `@`/`:` scalars are the ones a reader or a stricter parser has to think twice about.
- `validate.check` gained `duplicate_device_id`/`device_missing_id`/`bad_device_rssi` (errors), `device_without_name`/`no_locator_enabled` (warnings). `counts` deliberately still reports only floors/rooms/nodes/errors/warnings — tests assert that dict by equality, and it is a documented stable surface.
- New geometry findings in `validate` are warnings, not errors: `node_point_outside_room`, `room_overlap`, `room_outside_floor_bounds`, `node_point_outside_bounds`. Deliberate — odd floor plans are legal, so they must not fail a push unless `--strict`. `rooms overlaps` exits 1 on its own for callers who want that one check to gate.

## Conventions
- MIT licensed. Adding a command group = a `core/` module + wiring in `espresense_cli.py` + unit tests for the core module + E2E CLI tests (`CliRunner`) asserting `--json` parses and `--help` works.
- Keep the two SKILL.md copies (`skills/` and `cli_anything/espresense/skills/`) byte-identical.
