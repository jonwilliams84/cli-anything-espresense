# Changelog

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
