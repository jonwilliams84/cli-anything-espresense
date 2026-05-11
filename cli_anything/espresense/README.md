# cli-anything-espresense

CLI harness for the [ESPresense](https://espresense.com) ecosystem — talks to
both the companion service (REST + WebSocket + MQTT) and individual ESP32
nodes (per-node HTTP web server).

Built for operating a real ESPresense deployment from a terminal or by an AI
agent: read and edit the companion's YAML config, rotate room labels, fix
node `room:` assignments, push the config back to the running pod, restart
ESP devices, and stream live telemetry.

## Install

```bash
pip install -e .
cli-anything-espresense --help
```

Dependencies: `kubectl` (for config push / companion restart against a k8s
deployment), and an MQTT broker the nodes already talk to (for direct
setting publishes).

## First-time config

```bash
cli-anything-espresense config save                       # save defaults
cli-anything-espresense --base-url http://10.32.100.5:8267 config save   # override + save
```

Profile lives at `~/.config/cli-anything-espresense.json`. Every key can
also be passed inline as a `--` flag or set via `CLI_ESPRESENSE_<KEY>` env vars.

## Quick examples

### Health-check the companion

```bash
cli-anything-espresense companion info
cli-anything-espresense companion config-get
```

### List rooms and nodes

```bash
cli-anything-espresense rooms list
cli-anything-espresense rooms list --floor first
cli-anything-espresense nodes list                    # merged config + live
cli-anything-espresense nodes list --no-merge-live    # config-only
```

### Rename one room (and fix all nodes that pointed to it)

```bash
cli-anything-espresense rooms rename "Spare Room" "Office" --restart
```

### Rotate three rooms atomically

This works correctly even when names cycle through each other (handled
internally via temp sentinels):

```bash
cli-anything-espresense rooms rotate \
  --map "Spare Room=Noah Bedroom" \
  --map "Noah Bedroom=Sophie Bedroom" \
  --map "Sophie Bedroom=Spare Room" \
  --restart
```

### Edit one node's `room:` reference

```bash
cli-anything-espresense rooms repoint-node noah-bedroom "Noah Bedroom"
```

### Talk to a single ESP device by IP

```bash
cli-anything-espresense node info 10.32.101.32
cli-anything-espresense node settings 10.32.101.32 --section extras
cli-anything-espresense node set 10.32.101.32 absorption=2.8 --section extras
cli-anything-espresense node rename 10.32.101.32 sophie-bedroom
cli-anything-espresense node restart 10.32.101.32
```

### Push a setting over MQTT (works for any node already on the broker)

```bash
cli-anything-espresense mqtt set-node noah-bedroom absorption 2.8
cli-anything-espresense mqtt watch 'espresense/rooms/+/telemetry' --duration 10
```

### Live device-position stream

```bash
cli-anything-espresense companion stream --duration 30 --type deviceChanged
```

### Backup & push the YAML directly

```bash
cli-anything-espresense companion config-fetch -o ./config.yaml
# edit ...
cli-anything-espresense companion config-push ./config.yaml --restart
```

## Commands

| Group | Purpose |
|---|---|
| `companion api / info / config-get / config-fetch / config-push / restart / stream` | Talk to the companion service |
| `rooms list / rename / rotate / repoint-node` | Edit room polygons + node room references |
| `nodes list / show / rename-in-config / set-point / restart / delete / update-firmware / put-settings` | Manage nodes from the companion side |
| `node info / restart / settings / set / rename / scan-wifi / devices` | Direct HTTP to one ESP node |
| `devices list / show / set / delete` | Tracked devices (phones, tags, beacons) |
| `calibration get / summary / reset / auto-optimize` | Calibration matrix + autocalibration |
| `history get` | Per-device position history |
| `mqtt set-node / pub / watch` | Raw MQTT pub/sub |
| `config show / save` | Local connection profile |
| `repl` | Interactive shell (default if no subcommand) |

Pass `--json` for machine-readable output on every command.

## Architecture

```
cli_anything/espresense/
├── espresense_cli.py        # Click CLI entry-point + REPL
├── core/
│   ├── companion_api.py     # REST endpoints
│   ├── config_yaml.py       # fetch / push YAML via kubectl
│   ├── rooms.py             # polygon rename / rotate (with node fix-up)
│   ├── nodes.py             # node config edits + live-state merge
│   ├── node_direct.py       # per-ESP HTTP client (firmware web server)
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
    └── repl_skin.py         # shared REPL UI
```

The companion REST API can read the YAML but cannot write it — so the
write path is `kubectl exec ... cat > config.yaml` against the running
pod, with a timestamped `.bak` left behind. The companion auto-reloads
the file on start, hence the `--restart` flag on every mutating
command.
