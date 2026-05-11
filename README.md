# cli-anything-espresense

A command-line + Python harness for the [ESPresense](https://espresense.com)
ecosystem — controls both the **companion service** (REST + WebSocket + MQTT)
and **individual ESP32 nodes** (per-node firmware web server).

Built for operating a real ESPresense deployment from a terminal or by an AI
agent: read and edit the companion's YAML config, rotate room labels, fix
node `room:` assignments, push the config back to the running pod, restart
ESP devices, stream live telemetry, manage external converters.

Sibling of [`cli-anything-homeassistant`](https://github.com/jonwilliams84/cli-anything-homeassistant)
and [`cli-anything-zigbee2mqtt`](https://github.com/jonwilliams84/cli-anything-zigbee2mqtt)
in the same `cli-anything-*` family.

## Install

```bash
git clone https://github.com/jonwilliams84/cli-anything-espresense.git
cd cli-anything-espresense
pip install -e .
cli-anything-espresense --help
```

External deps:
- `kubectl` (for `companion config-push` / `companion restart` against a
  Kubernetes-deployed companion). The MQTT-only and per-node HTTP commands
  work without it.
- MQTT broker the nodes already publish to (for `mqtt` subcommands).

## First-time setup

```bash
cli-anything-espresense \
  --base-url http://10.43.24.245:8267 \
  --k8s-namespace espresense \
  config save
```

Profile is stored at `~/.config/cli-anything-espresense.json`. Per-key env
overrides also work: `CLI_ESPRESENSE_BASE_URL`, etc.

## Command groups

| Group | Purpose |
|---|---|
| `companion` | `api / info / config-get / config-fetch / config-push / restart / stream` — talk to the companion service |
| `rooms` | `list / rename / rotate / repoint-node` — edit room polygons + node room references (atomic, supports cycles) |
| `nodes` | `list / show / rename-in-config / set-point / restart / delete / update-firmware / put-settings` — manage nodes from the companion side |
| `node` | `info / restart / settings / set / rename / scan-wifi / devices` — direct HTTP to one ESP firmware node |
| `devices` | `list / show / set / delete` — tracked devices (phones, tags, beacons) |
| `calibration` | `get / summary / reset / auto-optimize` |
| `history` | `get` — per-device position history |
| `mqtt` | `set-node / pub / watch` — raw MQTT pub/sub |
| `config` | `show / save` (local connection profile) |
| `repl` | Interactive shell (default with no subcommand) |

All commands support `--json` for machine-readable output.

## Quick examples

```bash
# Health-check the companion
cli-anything-espresense companion info
cli-anything-espresense companion config-get

# List rooms + nodes (merges config.yaml with live API state)
cli-anything-espresense rooms list
cli-anything-espresense nodes list

# Rename one room (also fixes every node that referenced it)
cli-anything-espresense rooms rename "Spare Room" "Office" --restart

# Rotate three rooms atomically (works for cycles)
cli-anything-espresense rooms rotate \
  --map "Spare Room=Noah Bedroom" \
  --map "Noah Bedroom=Sophie Bedroom" \
  --map "Sophie Bedroom=Spare Room" \
  --restart

# Talk to a single ESP node by IP
cli-anything-espresense node info 10.32.101.32
cli-anything-espresense node rename 10.32.101.32 sophie-bedroom
cli-anything-espresense node restart 10.32.101.32

# Push a setting over MQTT (works even for offline nodes via retained)
cli-anything-espresense mqtt set-node noah-bedroom absorption 2.8
cli-anything-espresense mqtt watch 'espresense/rooms/+/telemetry' --duration 10

# Live device-position stream
cli-anything-espresense companion stream --duration 30 --type deviceChanged
```

## Architecture

```
cli_anything/espresense/
├── espresense_cli.py        # Click CLI + REPL
├── core/
│   ├── companion_api.py     # REST endpoints (/api/state/*, /api/node/*, ...)
│   ├── config_yaml.py       # fetch / push YAML via kubectl
│   ├── rooms.py             # polygon rename / rotate (with node fix-up)
│   ├── nodes.py             # node config edits + live-state merge
│   ├── node_direct.py       # per-ESP firmware HTTP client
│   ├── devices.py           # tracked-device wrappers
│   ├── calibration.py
│   ├── history.py
│   ├── stream.py            # /ws WebSocket consumer
│   ├── mqtt.py              # direct MQTT pub/sub
│   ├── k8s_backend.py       # kubectl exec helpers
│   └── project.py           # local connection profile
└── utils/
    ├── companion_client.py  # requests Session wrapper
    ├── yaml_io.py           # ruamel.yaml round-trip
    └── repl_skin.py
```

The companion's REST API can read the YAML but cannot write it — so writes
go through `kubectl exec ... cat > config.yaml` against the running pod,
with a timestamped `.bak` left behind for rollback. The companion auto-reloads
the file on start, hence the `--restart` flag on every mutating command.

## Tests

```bash
python3 -m pytest cli_anything/espresense/tests/ -v
```

16 unit tests cover the YAML round-trip, room rename + rotate (including
atomic cycles and trailing-whitespace handling), and the per-node HTTP
client — all against synthetic data, no live broker required.

## License

MIT — see [LICENSE](./LICENSE).
