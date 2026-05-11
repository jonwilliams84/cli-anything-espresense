---
name: cli-anything-espresense
description: CLI harness for the ESPresense ecosystem — read/edit the companion's YAML config, rotate or rename rooms, manage nodes, talk to individual ESP firmware web servers, push MQTT settings, stream live device telemetry.
---

# cli-anything-espresense

`cli-anything-espresense` is the agent-facing CLI for managing an ESPresense
deployment. It abstracts three transports — the companion's REST/WS API, the
per-node ESP32 web UI, and direct MQTT — behind a single Click CLI with full
`--json` output.

## When to use

- Auditing or editing the companion's `config.yaml` (rooms, polygons, nodes,
  devices, calibration, optimization).
- Renaming or rotating room labels — and fixing every node `room:`
  reference in the same operation.
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
| `companion` | `companion info`, `companion config-get`, `companion config-fetch -o cfg.yaml`, `companion config-push cfg.yaml --restart`, `companion restart`, `companion stream --duration 30 --type deviceChanged` |
| `rooms` | `rooms list`, `rooms rename "Spare" "Office" --restart`, `rooms rotate --map "A=B" --map "B=A" --restart`, `rooms repoint-node noah-bedroom "Noah Bedroom"` |
| `nodes` | `nodes list`, `nodes show <id>`, `nodes rename-in-config <old> <new>`, `nodes set-point <name> X Y Z`, `nodes restart <id>`, `nodes delete <id>`, `nodes update-firmware <id> <url>`, `nodes put-settings <id> '{"calibration":{"absorption":2.8}}'` |
| `node` | `node info <ip>`, `node restart <ip>`, `node settings <ip> --section extras`, `node set <ip> absorption=2.8`, `node rename <ip> <new-name>`, `node devices <ip>` |
| `devices` | `devices list`, `devices show <id>`, `devices set <id> --name "Jon Phone" --ref-rssi -59` |
| `calibration` | `calibration get`, `calibration summary`, `calibration reset`, `calibration auto-optimize on` |
| `history` | `history get <device-id> --start 2026-05-10T00:00Z --limit 50` |
| `mqtt` | `mqtt set-node <id> absorption 2.8`, `mqtt pub <topic> <payload>`, `mqtt watch 'espresense/rooms/+/telemetry' --duration 10` |
| `config` | `config show`, `config save` |
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

**Config writes** require `kubectl` to be available and the user to have
exec permission on the companion's deployment. Each write leaves a
`config.yaml.<unix-ts>.bak` next to the file in the pod.

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
