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
| `companion` | `api / info / config-get / config-fetch / config-push / restart / stream / locator / firmware-types / pod` — talk to the companion service |
| `rooms` | `list / add / delete / rename / rotate / repoint-node` — edit room polygons + node room references (atomic, supports cycles) |
| `floors` | `list / show` — inspect the floors declared in config.yaml |
| `nodes` | `list / show / add / remove-from-config / rename-in-config / set-point / restart / delete / update-firmware / put-settings` — manage nodes from the companion side |
| `node` | `info / restart / reboot / settings / set / rename / scan-wifi / devices / config-list / config-set / config-delete` — direct HTTP to one ESP firmware node |
| `devices` | `list / show / set / delete` — tracked devices (phones, tags, beacons) |
| `calibration` | `get / summary / reset / auto-optimize` |
| `history` | `get` — per-device position history |
| `mqtt` | `set-node / set-device / pub / watch` — raw MQTT pub/sub |
| `config` | `show / save` (local connection profile) + `doctor` (validate config.yaml) |
| `repl` | Interactive shell (default with no subcommand) |

All commands support `--json` for machine-readable output.

### Working offline with `--file`

Every command that reads or edits `config.yaml` accepts `--file <path>` to
work against a local YAML instead of the running pod. **kubectl is not
required in this mode.** That makes the documented fetch → edit → push loop
completable, and lets you review a change before it ever reaches the cluster:

```bash
cli-anything-espresense companion config-fetch -o cfg.yaml   # pull (kubectl)
cli-anything-espresense rooms rename "Spare Room" "Office" --file cfg.yaml
cli-anything-espresense config doctor --file cfg.yaml        # validate
cli-anything-espresense companion config-push cfg.yaml --restart
```

`--file` is available on `rooms list/add/delete/rename/rotate/repoint-node`,
`nodes list/add/remove-from-config/rename-in-config/set-point`,
`floors list/show` and `config doctor`. Writes leave a timestamped `.bak`
next to the file, exactly as the in-pod writes do.

### `config doctor`

Detects the config drift that silently breaks room tracking — the failure
mode `rooms rename` was built to repair, now catchable *before* it bites:

```bash
$ cli-anything-espresense config doctor --file cfg.yaml
checked 1 floor(s), 1 room(s), 1 node(s)
  ERROR [room_ref_whitespace] node 'lounge-node' `room:` is 'Lounge ' — the surrounding whitespace stops it matching the polygon.
1 error(s), 0 warning(s)
```

Checks: dangling node `room:` references, whitespace-padded room names,
duplicate room/node names and floor ids, malformed `point:` values,
degenerate polygons, rooms with no node, nodes with no room. It is
read-only, and **exits 1 on any error** (or any warning with `--strict`),
so it can gate a push:

```bash
cli-anything-espresense config doctor --file cfg.yaml && \
  cli-anything-espresense companion config-push cfg.yaml --restart
```

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

# Add a room and a node to a floor (offline, then push when happy)
cli-anything-espresense rooms add gf "Study" \
  --point 5,0 --point 9,0 --point 9,4 --point 5,4 --file cfg.yaml
cli-anything-espresense nodes add study-node --room "Study" \
  --point 7,2,1.5 --file cfg.yaml

# Per-device config on one ESP node
cli-anything-espresense node config-list 10.32.101.32
cli-anything-espresense node config-set 10.32.101.32 apple:1005:9-12 \
  --name "Jon Watch" --rssi-at-1m -59

# Push a setting over MQTT (works even for offline nodes via retained)
cli-anything-espresense mqtt set-node noah-bedroom absorption 2.8
cli-anything-espresense mqtt set-device apple:1005:9-12 '{"name":"Jon Watch"}'
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
│   ├── config_source.py     # where config.yaml lives: pod (kubectl) or local file
│   ├── validate.py          # config.yaml consistency checks (`config doctor`)
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

617 tests, 91% coverage — all against synthetic data on disk, no live
broker, cluster or companion required. They cover the YAML round-trip, room
rename + rotate (including atomic cycles and trailing-whitespace handling),
the config validator, the per-node HTTP client, and full end-to-end CLI
workflows driven through `--file` against a real config.yaml.

## License

MIT — see [LICENSE](./LICENSE).
