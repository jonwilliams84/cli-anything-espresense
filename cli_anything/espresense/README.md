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

### Global settings (outside config.yaml)

Deployment-wide knobs — telemetry cadence, expiration, GPS origin,
include/exclude filters — are not in config.yaml at all. The companion serves
them at `GET/POST /api/settings` and re-applies the retained MQTT topic
`espresense/settings/<key>/set` at startup:

```bash
cli-anything-espresense companion settings-keys              # known keys + kinds
cli-anything-espresense companion settings-get               # secrets redacted
cli-anything-espresense companion settings-set expiration 300
cli-anything-espresense mqtt set-global telemetry true       # broker-side twin
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
writes do. `--file` works on **every** config-reading or config-editing
command: all of `rooms`, all of `floors`, the config-side `nodes` and `devices`
commands, all of `settings`, and `config doctor`.

### Validate the config

```bash
cli-anything-espresense config doctor --file ./config.yaml
```

Flags two families of problem. *Textual:* dangling node `room:` references,
whitespace-padded room names, duplicate room/node/floor ids, malformed
`point:` values, degenerate polygons, unassigned rooms, node `floors:`
entries naming no declared floor. *Geometric:* a node sitting outside the
room it claims, two rooms overlapping on one floor, a room or node escaping
its floor `bounds:` — configs that are valid, start fine, and localise to the
wrong place. Read-only, and exits 1 on any error (`--strict` also fails on
warnings) so it can gate a push.

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

### Measure and reshape the floor plan

```bash
# per-room area / perimeter / centroid / bbox, and which nodes are really inside
cli-anything-espresense rooms geometry --file ./config.yaml

# which room contains this coordinate? (exits 1 if none)
cli-anything-espresense rooms locate 5 1 --file ./config.yaml

# do any two rooms on a floor share area? (exits 1 if so — use it as a gate)
cli-anything-espresense rooms overlaps --file ./config.yaml

# redraw, nudge, resize, recolour
cli-anything-espresense rooms set-points Office \
  --point 0,0 --point 5,0 --point 5,4 --point 0,4 --file ./config.yaml
cli-anything-espresense rooms move Office 1 -2 --file ./config.yaml
cli-anything-espresense rooms scale Office 1.1 --file ./config.yaml
cli-anything-espresense rooms set-color Office '#a3c9f9' --file ./config.yaml

# snap a node to the centre of its room instead of guessing coordinates
cli-anything-espresense nodes place office-node --file ./config.yaml
```

Negative coordinates work as positional arguments (`rooms move Office -1 -2`,
`rooms locate -2 -1.5`, `floors set-bounds gf -5,-5,0 5,5,3`,
`nodes set-point n -1 -2 2.5`).

### Add, retag and retire floors

```bash
cli-anything-espresense floors add bs --name "Basement" \
  --bounds "0,0,0 6,4,2.4" --file ./config.yaml
cli-anything-espresense floors rename bs "Cellar Level" --file ./config.yaml
cli-anything-espresense floors retag bs basement --file ./config.yaml
cli-anything-espresense floors fit-bounds basement --margin 0.25 --file ./config.yaml
cli-anything-espresense floors delete basement --file ./config.yaml
```

`floors retag` rewrites the floor `id` *and* every node `floors:` entry that
referenced it, so the config never passes through a state where nodes point at
a floor that no longer exists. `floors fit-bounds` derives the floor box from
the room polygons on it — run it after any `rooms add/move/scale`.
`floors delete` refuses to strand nodes unless you pass `--force`.

### Tracked devices in config.yaml

The `devices` group now spans both halves of device management. `devices
list / show / set / delete` talk to the companion's **runtime** store;
the `-config` commands edit the **`devices:` block of config.yaml**, which
is what survives a restart:

```bash
cli-anything-espresense devices list-in-config --file ./config.yaml
cli-anything-espresense devices add-to-config 'irk:abc123' \
  --name "Jon Phone" --rssi-at-1m -65 --file ./config.yaml
cli-anything-espresense devices update-in-config 'irk:abc123' \
  --rssi-at-1m -61 --file ./config.yaml
cli-anything-espresense devices remove-from-config 'irk:abc123' --file ./config.yaml
```

Same split as nodes: `devices delete` drops the companion's runtime record,
`devices remove-from-config` drops the config entry — retiring a beacon for
good needs both. Reference RSSI is written as the companion's `rssi@1m:` key
(the CLI spells it `--rssi-at-1m` because `@` is awkward in a shell), and
`config doctor` now flags duplicate device ids, missing ids and non-numeric
`rssi@1m` values as errors, unnamed devices as a warning.

### Tune timeouts, MQTT, locators and optimizers

Everything in config.yaml that is *not* a floor, room, node or device is
reachable by dotted path, so it keeps working as companion releases add keys:

```bash
cli-anything-espresense settings show --file ./config.yaml         # secrets redacted
cli-anything-espresense settings get mqtt.port --file ./config.yaml
cli-anything-espresense settings set away_timeout 300 --file ./config.yaml
cli-anything-espresense settings set locators.nelder_mead.enabled false --file ./config.yaml
cli-anything-espresense settings unset weighting.algorithm --file ./config.yaml

# localisation algorithms + autocalibration optimizers
cli-anything-espresense settings locators --file ./config.yaml
cli-anything-espresense settings locator nadaraya_watson off --file ./config.yaml
cli-anything-espresense settings optimizers --file ./config.yaml
cli-anything-espresense settings optimizer absorption off --file ./config.yaml
```

`settings show` and `settings get` **redact `mqtt.password` and anything else
that looks like a secret** unless you pass `--reveal` — that output tends to
end up in issues and agent transcripts. Values are auto-typed (`false` becomes
a bool, `300` an int, `[1,2]` a list); override with `--type str|int|float|bool|json`.
Structural blocks are deliberately refused: `settings set nodes.0.room X`
errors and points you at the `nodes` commands, which keep cross-references
consistent. Turning off every locator is caught by `config doctor` as
`no_locator_enabled`.

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
| `companion api / info / config-get / config-fetch / config-push / restart / stream / locator / firmware-types / pod / settings-keys / settings-get / settings-set` | Talk to the companion service |
| `rooms list / add / delete / rename / rotate / repoint-node` | Edit room polygons + node room references |
| `rooms geometry / locate / overlaps / set-points / move / scale / set-color` | Measure and reshape room polygons |
| `floors list / show / add / rename / retag / set-bounds / fit-bounds / delete` | Full floor CRUD in config.yaml |
| `nodes list / show / add / place / remove-from-config / rename-in-config / set-point / restart / delete / update-firmware / put-settings` | Manage nodes from the companion side |
| `node info / restart / reboot / settings / set / rename / scan-wifi / devices / config-list / config-set / config-delete` | Direct HTTP to one ESP node |
| `devices list / show / set / delete` | Tracked devices, companion runtime view (phones, tags, beacons) |
| `devices list-in-config / show-in-config / add-to-config / update-in-config / remove-from-config` | The durable `devices:` block of config.yaml |
| `settings show / get / set / unset / locators / locator / optimizers / optimizer` | Tuning half of config.yaml: timeouts, mqtt, gps, locators, optimizers |
| `companion settings-keys / settings-get / settings-set` + `mqtt set-global` | Global settings *outside* config.yaml (`/api/settings`, mirrored on MQTT) |
| `calibration get / summary / reset / auto-optimize` | Calibration matrix + autocalibration |
| `history get` | Per-device position history |
| `mqtt set-node / set-device / set-global / pub / watch` | Raw MQTT pub/sub |
| `config show / save / doctor` | Local connection profile + config.yaml validation |
| `repl` | Interactive shell (default if no subcommand) |

`nodes delete` clears the companion's runtime settings for a node;
`nodes remove-from-config` removes it from `config.yaml`. Use both to fully
retire a node. `devices delete` vs `devices remove-from-config` is the same
distinction for beacons.

Pass `--json` for machine-readable output on every command.

## Architecture

```
cli_anything/espresense/
├── espresense_cli.py        # Click CLI entry-point + REPL
├── core/
│   ├── companion_api.py     # REST endpoints
│   ├── config_source.py     # config.yaml location: pod (kubectl) or local --file
│   ├── geometry.py          # pure polygon / bounds maths (no I/O)
│   ├── floors.py            # floor CRUD, retag, bounds fitting
│   ├── validate.py          # config.yaml consistency checks (`config doctor`)
│   ├── config_yaml.py       # fetch / push YAML via kubectl
│   ├── rooms.py             # polygon rename / rotate / geometry (with node fix-up)
│   ├── nodes.py             # node config edits, placement + live-state merge
│   ├── node_direct.py       # per-ESP HTTP client (firmware web server)
│   ├── devices.py           # tracked-device wrappers (companion runtime)
│   ├── config_devices.py    # the `devices:` block of config.yaml
│   ├── settings.py          # dotted-path tuning edits (timeouts, mqtt, locators)
│   ├── global_settings.py   # deployment-wide settings: /api/settings + MQTT mirror
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
