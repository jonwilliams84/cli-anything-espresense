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
| `companion` | `api / info / config-get / config-fetch / config-push / restart / stream / locator / firmware-types / pod / settings-keys / settings-get / settings-set` — talk to the companion service |
| `rooms` | `list / add / delete / rename / rotate / repoint-node / geometry / locate / overlaps / set-points / move / scale / set-color` — edit room polygons + node room references (atomic, supports cycles) and reason about their geometry |
| `floors` | `list / show / add / rename / retag / set-bounds / fit-bounds / delete` — full floor CRUD in config.yaml |
| `nodes` | `list / show / add / place / remove-from-config / rename-in-config / set-point / restart / delete / update-firmware / put-settings` — manage nodes from the companion side |
| `node` | `info / restart / reboot / settings / set / rename / scan-wifi / devices / config-list / config-set / config-delete` — direct HTTP to one ESP firmware node |
| `devices` | `list / show / set / delete / whereis / occupancy` — tracked devices (phones, tags, beacons), incl. last-known position and live room occupancy |
| `calibration` | `get / summary / reset / auto-optimize` |
| `history` | `get` — per-device position history |
| `mqtt` | `set-node / set-device / set-global / pub / watch / distances / node-status` — raw MQTT pub/sub plus aggregated live snapshots (node→device distances, node online/offline) |
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

`--file` is available on **every** config-reading or config-editing command:
all of `rooms`, all of `floors`, the config-side `nodes` commands and
`config doctor`. Writes leave a timestamped `.bak` next to the file, exactly
as the in-pod writes do.

### `config doctor`

Detects the config drift that silently breaks room tracking — the failure
mode `rooms rename` was built to repair, now catchable *before* it bites:

```bash
$ cli-anything-espresense config doctor --file cfg.yaml
checked 1 floor(s), 1 room(s), 1 node(s)
  ERROR [room_ref_whitespace] node 'lounge-node' `room:` is 'Lounge ' — the surrounding whitespace stops it matching the polygon.
1 error(s), 0 warning(s)
```

Checks come in two families. **Textual:** dangling node `room:` references,
whitespace-padded room names, duplicate room/node names and floor ids,
malformed `point:` values, degenerate polygons, rooms with no node, nodes with
no room, node `floors:` entries naming no declared floor. **Geometric:** a
node whose `point:` is outside the room its `room:` names, two rooms
overlapping on one floor, a room or node escaping its floor `bounds:`.

That second family matters because those configs are *valid* — they load, the
companion starts, and devices simply localise to the wrong place. It is
read-only, and **exits 1 on any error** (or any warning with `--strict`),
so it can gate a push:

```bash
cli-anything-espresense config doctor --file cfg.yaml && \
  cli-anything-espresense companion config-push cfg.yaml --restart
```

### Floor-plan geometry

Rooms are polygons and nodes are 3D points, so the harness can do arithmetic on
them rather than making you edit coordinates by hand:

```bash
# What is each room, and is every node actually inside the room it claims?
cli-anything-espresense rooms geometry --file cfg.yaml
# → area, perimeter, centroid, bbox, nodes_inside, nodes_outside

# Which room is this coordinate in? (exits 1 if none)
cli-anything-espresense rooms locate 5 1 --file cfg.yaml

# Do any two rooms on one floor share area? (exits 1 if so — gates a push)
cli-anything-espresense rooms overlaps --file cfg.yaml

# Redraw / nudge / resize / recolour a room
cli-anything-espresense rooms set-points Office \
  --point 0,0 --point 5,0 --point 5,4 --point 0,4 --file cfg.yaml
cli-anything-espresense rooms move Office 1 -2 --file cfg.yaml
cli-anything-espresense rooms scale Office 1.1 --file cfg.yaml   # about its centroid
cli-anything-espresense rooms set-color Office '#a3c9f9' --file cfg.yaml

# Put a node in the middle of its room — no coordinates to work out
cli-anything-espresense nodes place office-node --file cfg.yaml
cli-anything-espresense nodes place office-node --room Kitchen --file cfg.yaml
```

Build a whole floor from nothing, then let the harness derive the bounds:

```bash
cli-anything-espresense floors add bs --name "Basement" --file cfg.yaml
cli-anything-espresense rooms add bs "Cellar" \
  --point 0,0 --point 3,0 --point 3,3 --point 0,3 --file cfg.yaml
cli-anything-espresense nodes add cellar-node --room Cellar --floor bs --file cfg.yaml
cli-anything-espresense nodes place cellar-node --file cfg.yaml
cli-anything-espresense floors fit-bounds bs --margin 0.25 --file cfg.yaml
cli-anything-espresense config doctor --file cfg.yaml
```

`floors retag <old> <new>` changes a floor's `id` **and** every node `floors:`
entry that referenced it — the floor-level twin of what `rooms rename` does for
`room:`. `floors delete` refuses to strand nodes unless you pass `--force`, and
names them either way.

### Tracked devices and tuning knobs

The other half of config.yaml — *what we track* and *how we localise it* —
is editable too, offline, with the same `--file`:

```bash
# the durable `devices:` registry (as opposed to the companion's runtime view)
cli-anything-espresense devices list-in-config --file cfg.yaml
cli-anything-espresense devices add-to-config 'irk:abc123' \
  --name "Jon Phone" --rssi-at-1m -65 --file cfg.yaml
cli-anything-espresense devices update-in-config 'irk:abc123' --rssi-at-1m -61 --file cfg.yaml
cli-anything-espresense devices remove-from-config 'irk:abc123' --file cfg.yaml

# timeouts, mqtt, gps, locators, optimizers — addressed by dotted path
cli-anything-espresense settings show --file cfg.yaml          # secrets redacted
cli-anything-espresense settings set away_timeout 300 --file cfg.yaml
cli-anything-espresense settings set locators.nelder_mead.enabled false --file cfg.yaml
cli-anything-espresense settings locators --file cfg.yaml
cli-anything-espresense settings locator nadaraya_watson off --file cfg.yaml
cli-anything-espresense settings optimizer absorption off --file cfg.yaml
```

`settings` is deliberately schema-free: dotted paths work against whatever
keys the running companion version understands, instead of an option list that
rots on the next release. It **redacts `mqtt.password` and friends** unless you
pass `--reveal`, auto-types values (`false`, `300`, `[1,2]`; override with
`--type`), and **refuses structural paths** like `settings set nodes.0.room` —
those belong to the `nodes`/`rooms`/`floors`/`devices` commands, which keep
cross-references consistent. `config doctor` grew matching checks: duplicate or
missing device ids and non-numeric `rssi@1m` are errors, unnamed devices and
"every locator disabled" are warnings.

### Global settings (outside config.yaml)

Some knobs apply to the whole deployment rather than one node, room or
device — telemetry cadence, device expiration, the GPS origin, include/exclude
filters. Those are *not* in config.yaml; the companion keeps them in its own
state and serves them at `GET/POST /api/settings`, mirrored on the retained
MQTT topic `espresense/settings/<key>/set`:

```bash
# What's available, with the value kind each key wants
cli-anything-espresense companion settings-keys

# Read (secrets redacted unless --reveal)
cli-anything-espresense companion settings-get
cli-anything-espresense companion settings-get --section expiration

# Set — applied immediately, no --restart needed, no --file (it's not config.yaml)
cli-anything-espresense companion settings-set expiration 300
cli-anything-espresense companion settings-set telemetry false
cli-anything-espresense companion settings-set gps '{"lat":51.5,"lng":-0.1,"elev":30}'

# Broker-side twin, for when the REST API is unreachable; retained, so the
# companion re-applies the value at startup
cli-anything-espresense mqtt set-global expiration 300
cli-anything-espresense mqtt set-global telemetry true
```

Values are coerced the same way `settings set` coerces them (`false` → bool,
`300` → int, JSON text → object); a known key's declared kind is used unless
you override with `--type str|int|float|bool|json`. `settings-get` output is
redacted by default. Unknown keys are still accepted — the companion owns the
schema — but `companion settings-keys` lists the spellings that exist today.

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

# Global settings via MQTT (retained; re-applied at startup)
cli-anything-espresense mqtt set-global expiration 300

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
│   ├── geometry.py          # pure polygon / bounds maths (no I/O)
│   ├── config_yaml.py       # fetch / push YAML via kubectl
│   ├── floors.py            # floor CRUD, retag, bounds fitting
│   ├── rooms.py             # polygon rename / rotate / geometry (with node fix-up)
│   ├── nodes.py             # node config edits, placement + live-state merge
│   ├── node_direct.py       # per-ESP firmware HTTP client
│   ├── devices.py           # tracked-device wrappers (companion runtime)
│   ├── config_devices.py    # the `devices:` block of config.yaml
│   ├── settings.py          # dotted-path tuning edits (timeouts, mqtt, locators)
│   ├── calibration.py
│   ├── history.py
│   ├── stream.py            # /ws WebSocket consumer
│   ├── mqtt.py              # direct MQTT pub/sub
│   ├── global_settings.py   # deployment-wide settings (/api/settings + MQTT)
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

1271 tests, 94% coverage — all against synthetic data on disk, no live
broker, cluster or companion required. They cover the YAML round-trip, room
rename + rotate (including atomic cycles and trailing-whitespace handling),
the polygon/bounds maths (including the cases where two rooms must *not* be
called overlapping), the config validator, the device registry and tuning-path
editors (coercion, secret redaction, structural-path refusal), the per-node
HTTP client, and full end-to-end CLI workflows driven through `--file` against
a real config.yaml.

## License

MIT — see [LICENSE](./LICENSE).
