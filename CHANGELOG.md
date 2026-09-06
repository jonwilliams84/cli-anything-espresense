# Changelog

## [0.6.0] — 2026-09-06

- `history trail DEVICE_ID` — movement summary for one device, built from the
  same `/api/history/<id>` rows as `history get` (which stays the raw
  firehose). Folds the rows into consecutive room segments — one per visit,
  not one per room — each with first/last-seen timestamps and a point count,
  plus the overall first/last seen and the list of rooms visited in order.
  `--start/--end/--limit` match `history get`; `--json` emits the full
  summary. Accepts both row spellings the companion has shipped
  (`roomName`/`room`, `unixTs`/`ts`/`timestamp`); rows without a room
  attribution stay as `room: null` segments so point counts stay honest but
  don't count as visits.
- New pure `core/history.trail(rows)` aggregator, unit-tested without a
  companion in `test_core.py::TestHistoryTrail`; CLI behaviour pinned in
  `test_full_e2e.py::TestHistoryTrailE2E` and a
  get/trail-consistency workflow in `TestHistoryWorkflow`.

## [0.5.0] — 2026-09-06

- Updated `claude.md`, `test.md`. (2 files changed, 5 insertions(+), 4 deletions(-))

## [0.4.0] — 2026-09-06

- Updated `claude.md`, `test.md`. (2 files changed, 5 insertions(+), 4 deletions(-))

## v0.3.0 — live presence queries

The harness could configure an ESPresense deployment end to end but could
barely *query* it: the everyday operational questions of a presence system
were only reachable by hand-rolling `mqtt watch` and scraping raw history
rows. v0.3.0 makes them first-class commands (all `--json`-capable):

- `devices whereis DEVICE_ID` — last known position of one tracked device
  (room, floor, coordinates, when) from the companion's history API; exits 1
  with `{"found": false}` when the device has never been seen, so it gates a
  script the way `rooms locate` does.
- `devices occupancy [--floor F] [--show-all]` — which tracked devices are
  currently in which room, grouped from the companion's live device list,
  with unplaceable devices surfaced separately.
- `mqtt distances [--device ID] [--node ID] [--duration S]` — subscribes to
  `<prefix>/rooms/+/devices/+` for a bounded window and aggregates it: per
  device and node, the most recent distance plus min/max/sample count, with
  the closest node flagged. The readable counterpart to `mqtt watch`.
- `mqtt node-status [--duration S]` — which nodes report online/offline on
  the retained `<prefix>/rooms/<node>/status` topic.

Core:

- new `core/telemetry.py`: `whereis`, `distance_snapshot` /
  `aggregate_distances` / `nearest` / `distance_rows`,
  `status_snapshot` / `aggregate_status`, `occupancy`, and
  `parse_distance_payload` (accepts both the bare-number and
  `{"distance": ...}` payload shapes nodes have shipped). All aggregation
  is pure over collected records, so it is unit-testable without a broker;
  only the `mqtt.watch` calls touch the network.

Also fixed in passing: `mqtt watch` was documented everywhere but its
`@mqtt.command("watch")` registration had been lost, making the documented
command unreachable — restored (and now pinned by a workflow test).

## v0.2.0 — global settings, both transports

The companion's deployment-wide settings were the last major surface the
harness could not touch. They are *not* config.yaml — the companion keeps
them in its own state (telemetry cadence, device expiration, availability
timeout, the GPS origin, include/exclude filters) and serves them at
`GET/POST /api/settings`, mirrored on the retained MQTT topic
`espresense/settings/<key>/set`. Neither transport was reachable before.

New commands (all `--json`-capable):

- `companion settings-keys` — the known global setting keys with the value
  kind each wants, so an agent can discover spellings before writing.
- `companion settings-get [--section KEY] [--reveal]` — read the full
  mapping or one key; secrets redacted by default.
- `companion settings-set KEY VALUE [--type …]` — coerce and POST one key;
  applied immediately by the companion (no `--restart`, and no `--file` —
  these settings have no YAML home).
- `mqtt set-global KEY VALUE [--retain/--no-retain] [--prefix]` — the
  broker-side twin; the retained message is re-applied at startup.

Core:

- new `core/global_settings.py`: `fetch` (redacted read), `update` (coerce
  via the same coercion `settings set` uses, with the key's declared kind as
  the default), `describe`, and `KNOWN_SETTINGS`.
- `core/mqtt.py` gained `publish_global_setting()`; the payload
  stringification the publish helpers share was factored into `_stringify`.

Docs, tests: unit tests for the new module and the MQTT helper, E2E tests
for all four commands, README/SKILL.md/CLAUDE.md updated. Version 0.1.0 →
0.2.0 (MINOR: new commands).
