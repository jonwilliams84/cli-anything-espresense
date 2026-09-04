---
name: cli-anything-espresense
description: CLI harness for the ESPresense ecosystem — read/edit/validate the companion's YAML config, rotate or rename rooms, add and reshape room polygons, manage floors and their bounds, place nodes by geometry, curate the tracked-device registry, tune timeouts/locators/optimizers by dotted path, set deployment-wide global settings over REST or MQTT, talk to individual ESP firmware web servers, push MQTT settings, stream live device telemetry.
---

# cli-anything-espresense

`cli-anything-espresense` is the agent-facing CLI for managing an ESPresense
deployment. It abstracts three transports — the companion's REST/WS API, the
per-node ESP32 web UI, and direct MQTT — behind a single Click CLI with full
`--json` output.

## When to use

- Auditing, validating or editing the companion's `config.yaml` (rooms,
  polygons, nodes, devices, calibration, optimization) — against the live pod
  or a local file with `--file`.
- Diagnosing "the node is in the wrong room" / "device isn't being located"
  with `config doctor`, which names the exact broken reference — including the
  *geometric* cases (node placed outside the room it names, overlapping rooms,
  rooms escaping the floor bounds) where nothing textual is wrong at all.
- Building or reshaping a floor plan: adding floors and rooms, redrawing,
  moving, scaling polygons, fitting floor `bounds` to the rooms on them, and
  placing nodes at room centroids instead of hand-computed coordinates.
- Renaming or rotating room labels — and fixing every node `room:`
  reference in the same operation.
- Curating which beacons are tracked (`devices ...-config`) and what their
  reference RSSI is, durably in `config.yaml` rather than in runtime state.
- Tuning behaviour: timeouts, MQTT connection, GPS origin, which localisation
  algorithm runs, which autocalibration optimizers run (`settings`).
- Reading or setting the companion's **global settings** — telemetry,
  expiration, availability timeout, GPS origin, include/exclude filters —
  which live outside config.yaml (`companion settings-keys/get/set`, or
  `mqtt set-global` when the REST API is unreachable).
- Inspecting one ESP node's status, settings, or seen-devices list by IP.
- Renaming the OTA hostname of a physical ESP node.
- Streaming live device-position events from the companion.
- Pushing per-node BLE settings (absorption, tx_ref_rssi, …) over MQTT.

## Install

```bash
pip install -e /path/to/cli-anything-espresense
```

External deps: `kubectl` (for config-push and restart commands), and the
broker the nodes already use (for MQTT commands).

## Configure once

```bash
cli-anything-espresense --base-url http://<companion-ip>:8267 config save
# then edit ~/.config/cli-anything-espresense.json to add mqtt_host etc.
```

## Command groups

| Group | Examples |
|---|---|
| `companion` | `companion info`, `companion config-get`, `companion config-fetch -o cfg.yaml`, `companion config-push cfg.yaml --restart`, `companion restart`, `companion stream --duration 30 --type deviceChanged`, `companion locator`, `companion firmware-types`, `companion pod`, `companion settings-keys`, `companion settings-get --section expiration`, `companion settings-set expiration 300` |
| `rooms` | `rooms list`, `rooms add gf "Study" --point 5,0 --point 9,0 --point 9,4`, `rooms delete "Study"`, `rooms rename "Spare" "Office" --restart`, `rooms rotate --map "A=B" --map "B=A" --restart`, `rooms repoint-node noah-bedroom "Noah Bedroom"` |
| `rooms` (geometry) | `rooms geometry`, `rooms locate 5 1`, `rooms overlaps`, `rooms set-points Office --point 0,0 --point 5,0 --point 5,4 --point 0,4`, `rooms move Office 1 -2`, `rooms scale Office 1.1`, `rooms set-color Office '#a3c9f9'` |
| `floors` | `floors list`, `floors show gf`, `floors add bs --name "Basement" --bounds "0,0,0 6,4,2.4"`, `floors rename bs "Cellar"`, `floors retag bs basement`, `floors set-bounds gf 0,0,0 10,8,2.4`, `floors fit-bounds gf --margin 0.25`, `floors delete bs --force` |
| `nodes` | `nodes list`, `nodes show <id>`, `nodes add <name> --room "Office" --point 1,2,3`, `nodes place <name> --room "Office"`, `nodes remove-from-config <name>`, `nodes rename-in-config <old> <new>`, `nodes set-point <name> X Y Z`, `nodes restart <id>`, `nodes delete <id>`, `nodes update-firmware <id> <url>`, `nodes put-settings <id> '{"calibration":{"absorption":2.8}}'` |
| `node` | `node info <ip>`, `node restart <ip>`, `node reboot <ip>`, `node settings <ip> --section extras`, `node set <ip> absorption=2.8`, `node rename <ip> <new-name>`, `node devices <ip>`, `node config-list <ip>`, `node config-set <ip> <device-id> --name X --rssi-at-1m -59`, `node config-delete <ip> <device-id>` |
| `devices` (runtime) | `devices list`, `devices show <id>`, `devices set <id> --name "Jon Phone" --ref-rssi -59`, `devices delete <id>` |
| `devices` (config.yaml) | `devices list-in-config`, `devices show-in-config <id>`, `devices add-to-config 'irk:abc' --name "Jon Phone" --rssi-at-1m -65`, `devices update-in-config 'irk:abc' --rssi-at-1m -61`, `devices remove-from-config 'irk:abc'` |
| `settings` | `settings show`, `settings show --section mqtt`, `settings get locators.nelder_mead.enabled`, `settings set away_timeout 300`, `settings unset weighting.algorithm`, `settings locators`, `settings locator nadaraya_watson off`, `settings optimizers`, `settings optimizer absorption off` |
| `calibration` | `calibration get`, `calibration summary`, `calibration reset`, `calibration auto-optimize on` |
| `history` | `history get <device-id> --start 2026-05-10T00:00Z --limit 50` |
| `mqtt` | `mqtt set-node <id> absorption 2.8`, `mqtt set-device <device-id> '{"name":"Watch"}'`, `mqtt pub <topic> <payload>`, `mqtt watch 'espresense/rooms/+/telemetry' --duration 10, `mqtt set-global expiration 300` |
| `config` | `config show`, `config save`, `config doctor --file cfg.yaml` |
| `repl` | Interactive shell (default with no subcommand) |

## Agent guidance

**Every command supports `--json`** — always use it for machine parsing:

```bash
cli-anything-espresense --json rooms list
cli-anything-espresense --json nodes list --merge-live
```

**Mutating commands default to no-restart and no-push** so an agent can call
`--dry-run` first, inspect the summary, then re-run without `--dry-run` once
satisfied. `rooms rename` and `rooms rotate` return a JSON summary like:

```json
{
  "floor_id": "first",
  "rooms_renamed": 1,
  "nodes_repointed": 2,
  "whitespace_fixes": 4,
  "dry_run": false,
  "pushed": {"bytes_written": 4521, "backed_up": true, "restarted": true}
}
```

**Room rotation is atomic.** Use `rooms rotate --map` (repeatable) for any
swap or cycle — internal sentinel-renaming handles the case where two
rooms swap names without a collision.

**Renaming a physical node** (`node rename <ip> <name>`) sets the firmware's
`room` setting and triggers a restart. The node's hostname will then be
`espresense-<kebab-of-new-name>`. Expect ~30–60s offline.

**MQTT setting publishes** target `espresense/rooms/<node_id>/<key>/set` and
are retained by default — the node applies the new value on next message
processing. The companion will also pick up the value into its
`NodeSettings` state.

**Config writes** against the live pod require `kubectl` and exec permission
on the companion's deployment. Each write leaves a
`config.yaml.<unix-ts>.bak` next to the file in the pod.

**`--file <path>` removes that requirement.** Every command that reads or
edits `config.yaml` accepts it and operates on a local YAML instead — no
kubectl, no cluster. Prefer this shape when you can, because it lets you
validate before anything reaches production:

**Global settings are not config.yaml.** `telemetry`, `expiration`,
`availability_timeout`, `gps`, `include`/`exclude` and friends live in the
companion's own state — `settings set` will not reach them. Use
`companion settings-get` (secrets redacted unless `--reveal`) and
`companion settings-set`, or the retained MQTT twin `mqtt set-global` when
the REST API is unreachable. Both coerce values identically, and known key
spellings are listed by `companion settings-keys`:

```bash
cli-anything-espresense --json companion settings-set expiration 300
cli-anything-espresense --json mqtt set-global telemetry true
```

```bash
cli-anything-espresense companion config-fetch -o cfg.yaml    # needs kubectl
cli-anything-espresense --json rooms rename "Spare" "Office" --file cfg.yaml
cli-anything-espresense --json config doctor --file cfg.yaml  # exits 1 if broken
cli-anything-espresense companion config-push cfg.yaml --restart
```

Local writes leave a `.bak` beside the file too. `--restart` is meaningless
for a local file, so it is reported back as `restart_skipped` rather than
silently ignored.

**`config doctor` is the first thing to run** when a user reports a node in
the wrong room, a device not being located, or a room missing from Home
Assistant. It exits 1 on any error and returns machine-readable findings
with stable `code` values you can branch on:

```json
{
  "ok": false,
  "errors": [{"level": "error", "code": "dangling_room_ref",
              "node": "office-node", "room": "Ghost",
              "message": "node 'office-node' points at room 'Ghost', which no floor declares"}],
  "warnings": [],
  "counts": {"floors": 1, "rooms": 2, "nodes": 2, "errors": 1, "warnings": 0}
}
```

Codes (textual): `dangling_room_ref`, `room_ref_whitespace`,
`duplicate_room_name`, `duplicate_node_name`, `duplicate_floor_id`,
`node_missing_room`, `node_missing_name`, `bad_node_point`,
`degenerate_polygon`, `room_without_node`, `no_floors`, `no_nodes`.

Codes (devices/tuning): `duplicate_device_id`, `device_missing_id` and
`bad_device_rssi` are errors; `device_without_name` and `no_locator_enabled`
are warnings. `no_locator_enabled` means every entry in `locators:` is
switched off, so nothing will ever be positioned — `settings locator <name>
on` is the fix.

Codes (geometric): `dangling_floor_ref` and `bad_floor_bounds` are errors;
`node_point_outside_room`, `room_overlap`, `room_outside_floor_bounds` and
`node_point_outside_bounds` are warnings. They are warnings on purpose —
unusual floor plans are legal — so gate on them with `--strict` when you want
them to block, and note that `rooms overlaps` exits 1 on its own if you only
care about that one check.

**The geometric codes have a command-shaped fix each**, so an agent can repair
without computing coordinates:

| Finding | Fix |
|---|---|
| `node_point_outside_room` | `nodes place <node>` (snaps to the room centroid) |
| `room_outside_floor_bounds` | `floors fit-bounds <floor> [--margin N]` |
| `node_point_outside_bounds` | `floors fit-bounds <floor>` or `nodes place <node>` |
| `room_overlap` | `rooms move` / `rooms scale` / `rooms set-points` |
| `dangling_floor_ref` | `floors retag <old> <new>` (never edit the id alone) |

**Deleting a room is guarded.** `rooms delete` refuses to run while any node
still references it, and returns `orphaned_nodes` naming them — repoint
those with `rooms repoint-node` first, or pass `--force` and accept that
`config doctor` will then report `dangling_room_ref`.

**Retiring a node takes two commands**: `nodes delete <id>` clears the
companion's runtime settings/telemetry, `nodes remove-from-config <name>`
removes the entry from `config.yaml`. Beacons work the same way: `devices
delete <id>` is runtime, `devices remove-from-config <id>` is config.yaml.

**`settings` is the schema-free half of `config.yaml`.** Address any tuning
key by dotted path (`settings set locators.nelder_mead.enabled false`) — it
works against whatever keys the running companion version has. Three rules
worth knowing before calling it:

  * secrets are **redacted** in `settings show` / `settings get` output; pass
    `--reveal` only when the value is genuinely needed;
  * values are auto-typed (`false` -> bool, `300` -> int, `[1,2]` -> list);
    force with `--type str|int|float|bool|json`;
  * structural paths (`nodes.*`, `rooms.*`, `floors.*`, `devices.*`) are
    **refused** — use the dedicated commands, which repair cross-references.

**Retiring a floor is guarded the same way as a room.** `floors delete` reports
`rooms_removed`, `orphaned_nodes` and `nodes_referencing`, and refuses to write
while any of them are non-empty unless `--force` is passed.

**Signed coordinates work as positional arguments** on `rooms move`,
`rooms locate`, `floors set-bounds` and `nodes set-point` — e.g.
`rooms move Office -1 -2`. Elsewhere, prefer `--option=-2` form.

**Prefer `nodes place` over `nodes set-point`** unless you have a surveyed
coordinate: `place` puts the node at the room's centroid, which is guaranteed
to satisfy `node_point_outside_room`. Use `rooms locate X Y` first if you do
have coordinates and want to confirm which room they land in.

## Typical workflows

### Rotate rooms after a kids-switch-bedrooms day

```bash
# 1. inspect current rooms + which nodes reference each
cli-anything-espresense --json rooms list

# 2. dry-run the rotation
cli-anything-espresense rooms rotate --dry-run \
  --map "Spare Room=Noah Bedroom" \
  --map "Noah Bedroom=Sophie Bedroom" \
  --map "Sophie Bedroom=Spare Room"

# 3. apply for real, restart the companion
cli-anything-espresense rooms rotate \
  --map "Spare Room=Noah Bedroom" \
  --map "Noah Bedroom=Sophie Bedroom" \
  --map "Sophie Bedroom=Spare Room" --restart
```

### Add a new node to a new room, safely

```bash
# 1. pull the live config and work on it locally
cli-anything-espresense companion config-fetch -o cfg.yaml

# 2. carve out the room polygon and place the node
cli-anything-espresense --json rooms add gf "Study" \
  --point 5,0 --point 9,0 --point 9,4 --point 5,4 --file cfg.yaml
cli-anything-espresense --json nodes add study-node \
  --room "Study" --point 7,2,1.5 --file cfg.yaml

# 3. prove it is coherent BEFORE it reaches the cluster (exits 1 if not)
cli-anything-espresense --json config doctor --file cfg.yaml

# 4. ship it
cli-anything-espresense companion config-push cfg.yaml --restart
```

### Build a new floor from nothing, geometry-checked

```bash
cli-anything-espresense companion config-fetch -o cfg.yaml

cli-anything-espresense --json floors add bs --name "Basement" --file cfg.yaml
cli-anything-espresense --json rooms add bs "Cellar" \
  --point 0,0 --point 3,0 --point 3,3 --point 0,3 --file cfg.yaml
cli-anything-espresense --json nodes add cellar-node \
  --room "Cellar" --floor bs --file cfg.yaml

# no coordinates needed: centre the node, then size the floor to its rooms
cli-anything-espresense --json nodes place cellar-node --file cfg.yaml
cli-anything-espresense --json floors fit-bounds bs --margin 0.25 --file cfg.yaml

cli-anything-espresense --json rooms overlaps --file cfg.yaml   # exits 1 on clash
cli-anything-espresense --json config doctor --file cfg.yaml    # exits 1 on error
cli-anything-espresense companion config-push cfg.yaml --restart
```

### Fix a floor plan that drifted

```bash
# 1. what does the geometry actually say?
cli-anything-espresense --json rooms geometry --file cfg.yaml   # nodes_outside
cli-anything-espresense --json config doctor --file cfg.yaml    # coded findings

# 2. apply the fix that matches each code
cli-anything-espresense --json nodes place kitchen-node --file cfg.yaml
cli-anything-espresense --json floors fit-bounds gf --file cfg.yaml

# 3. re-check: warnings should be empty now
cli-anything-espresense --json config doctor --strict --file cfg.yaml
```

### Diagnose "my device is never located in the study"

```bash
cli-anything-espresense --json config doctor          # against the live pod
cli-anything-espresense --json rooms list             # node_names per room
cli-anything-espresense --json nodes list             # online? correct room?
cli-anything-espresense --json calibration summary    # R / RMSE sane?
```

A `dangling_room_ref` or `room_ref_whitespace` error explains it outright;
`rooms repoint-node` or `rooms rename` is the fix. If doctor is clean, check
the geometry: `rooms geometry` shows `nodes_outside` per room, and
`rooms locate <x> <y>` says which polygon a coordinate really belongs to.

### Track a new beacon and tune how it is located

```bash
cli-anything-espresense companion config-fetch -o cfg.yaml

# 1. add it to the durable registry with a friendly name + reference RSSI
cli-anything-espresense --json devices add-to-config 'irk:abc123' \
  --name "Jon Phone" --rssi-at-1m -65 --file cfg.yaml

# 2. check what is doing the locating, and switch algorithms if needed
cli-anything-espresense --json settings locators --file cfg.yaml
cli-anything-espresense --json settings locator nelder_mead on --file cfg.yaml
cli-anything-espresense --json settings set away_timeout 300 --file cfg.yaml

# 3. validate (catches duplicate ids, bad rssi@1m, all-locators-off) and ship
cli-anything-espresense --json config doctor --file cfg.yaml
cli-anything-espresense companion config-push cfg.yaml --restart
```

### Recalibrate a single node remotely

```bash
cli-anything-espresense mqtt set-node noah-bedroom absorption 2.6
cli-anything-espresense mqtt set-node noah-bedroom rx_adj_rssi -3
cli-anything-espresense calibration summary    # check R/RMSE moved the right way
```

### Watch one device's distance reports

```bash
cli-anything-espresense mqtt watch 'espresense/devices/phone:jon/+' --duration 30
```
