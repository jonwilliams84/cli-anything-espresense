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
cli-anything-espresense mqtt set-device apple:1005:9-12 '{"name":"Jon Watch"}'
cli-anything-espresense mqtt watch 'espresense/rooms/+/telemetry' --duration 10
```

### Live device-position stream

```bash
cli-anything-espresense companion stream --duration 30 --type deviceChanged
```

### Backup, edit offline, validate, push

Every config-reading/editing command takes `--file`, so the whole edit loop
runs against a local YAML with **no kubectl and no cluster**:

```bash
cli-anything-espresense companion config-fetch -o ./config.yaml   # pull
cli-anything-espresense rooms rename "Spare Room" "Office" --file ./config.yaml
cli-anything-espresense config doctor --file ./config.yaml        # validate
cli-anything-espresense companion config-push ./config.yaml --restart
```

Local writes leave a timestamped `.bak` beside the file, just like in-pod
writes do. `--file` works on `rooms list/add/delete/rename/rotate/repoint-node`,
`nodes list/add/remove-from-config/rename-in-config/set-point`,
`floors list/show`, and `config doctor`.

### Validate the config

```bash
cli-anything-espresense config doctor --file ./config.yaml
```

Flags dangling node `room:` references, whitespace-padded room names,
duplicate room/node/floor ids, malformed `point:` values, degenerate
polygons, and unassigned rooms. Read-only, and exits 1 on any error
(`--strict` also fails on warnings) so it can gate a push.

### Add rooms and nodes

```bash
cli-anything-espresense rooms add gf "Study" \
  --point 5,0 --point 9,0 --point 9,4 --point 5,4 --file ./config.yaml
cli-anything-espresense nodes add study-node --room "Study" \
  --point 7,2,1.5 --file ./config.yaml
cli-anything-espresense floors list --file ./config.yaml
```

`rooms delete` refuses to orphan a node unless you pass `--force`, and tells
you exactly which nodes to repoint first.

### Per-device config on one ESP node

```bash
cli-anything-espresense node config-list 10.32.101.32
cli-anything-espresense node config-set 10.32.101.32 apple:1005:9-12 \
  --name "Jon Watch" --rssi-at-1m -59
cli-anything-espresense node config-delete 10.32.101.32 apple:1005:9-12
```

## Commands

| Group | Purpose |
|---|---|
| `companion api / info / config-get / config-fetch / config-push / restart / stream / locator / firmware-types / pod` | Talk to the companion service |
| `rooms list / add / delete / rename / rotate / repoint-node` | Edit room polygons + node room references |
| `floors list / show` | Inspect floors declared in config.yaml |
| `nodes list / show / add / remove-from-config / rename-in-config / set-point / restart / delete / update-firmware / put-settings` | Manage nodes from the companion side |
| `node info / restart / reboot / settings / set / rename / scan-wifi / devices / config-list / config-set / config-delete` | Direct HTTP to one ESP node |
| `devices list / show / set / delete` | Tracked devices (phones, tags, beacons) |
| `calibration get / summary / reset / auto-optimize` | Calibration matrix + autocalibration |
| `history get` | Per-device position history |
| `mqtt set-node / set-device / pub / watch` | Raw MQTT pub/sub |
| `config show / save / doctor` | Local connection profile + config.yaml validation |
| `repl` | Interactive shell (default if no subcommand) |

`nodes delete` clears the companion's runtime settings for a node;
`nodes remove-from-config` removes it from `config.yaml`. Use both to fully
retire a node.

Pass `--json` for machine-readable output on every command.

## Architecture

```
cli_anything/espresense/
├── espresense_cli.py        # Click CLI entry-point + REPL
├── core/
│   ├── companion_api.py     # REST endpoints
│   ├── config_source.py     # config.yaml location: pod (kubectl) or local --file
│   ├── validate.py          # config.yaml consistency checks (`config doctor`)
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

`core/config_source.py` puts that write path behind an interface with a
second implementation backed by a plain local file, which is what `--file`
selects. Both honour the same `.bak` and summary contract; the local one
reports `restart_skipped` rather than pretending a `--restart` took effect.
