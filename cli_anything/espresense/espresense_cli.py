"""cli-anything-espresense — companion + per-node ESPresense control."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from cli_anything.espresense.core import (
    calibration as calibration_core,
    config_devices as config_devices_core,
    config_source as config_source_core,
    config_yaml as config_core,
    devices as devices_core,
    floors as floors_core,
    geometry,
    global_settings as global_settings_core,
    history as history_core,
    k8s_backend,
    mqtt as mqtt_core,
    node_direct,
    nodes as nodes_core,
    project,
    rooms as rooms_core,
    settings as settings_core,
    stream as stream_core,
    telemetry as telemetry_core,
    validate as validate_core,
)
from cli_anything.espresense.core import companion_api
from cli_anything.espresense.utils.companion_client import CompanionClient, CompanionError

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}

# Commands that take signed coordinates as positional arguments need this:
# without `ignore_unknown_options`, click parses `rooms move Office -2 0` as an
# option `-2` and refuses the command, which makes half the coordinate plane
# unreachable from the shell. Declared options are still parsed normally; only
# unrecognised dash-tokens fall through to the arguments.
COORD_SETTINGS = {**CONTEXT_SETTINGS, "ignore_unknown_options": True}


# ──────────────────────────────────────────────────────── helpers


def make_client(ctx: click.Context) -> CompanionClient:
    obj = ctx.obj
    return CompanionClient(
        base_url=obj["base_url"],
        timeout=obj["timeout"],
        verify_ssl=obj["verify_ssl"],
    )


def make_k8s_target(ctx: click.Context) -> k8s_backend.K8sTarget:
    obj = ctx.obj
    return k8s_backend.K8sTarget(
        namespace=obj["k8s_namespace"],
        deployment=obj["k8s_deployment"],
        container=obj["k8s_container"],
        config_path=obj["k8s_config_path"],
    )


def make_config_source(ctx: click.Context, file: str | None = None):
    """Resolve where config.yaml is read from / written to.

    `--file` selects a local YAML; otherwise the running pod via kubectl.
    Keeping this in one place means every structured edit (rooms, nodes)
    gained offline operation at once, with no per-command branching.
    """
    return config_source_core.from_options(make_k8s_target(ctx), file)


def config_file_option(fn):
    """Shared `--file` option for every command that edits config.yaml."""
    return click.option(
        "--file",
        "config_file",
        default=None,
        type=click.Path(dir_okay=False),
        help="Operate on a local YAML file instead of the pod (no kubectl needed).",
    )(fn)


def emit(ctx: click.Context, data) -> None:
    if ctx.obj.get("as_json"):
        click.echo(json.dumps(data, indent=2, default=str, sort_keys=True))
        return
    if data is None:
        return
    if isinstance(data, str):
        click.echo(data)
        return
    if isinstance(data, list):
        if data and isinstance(data[0], dict):
            _print_table(data)
        else:
            for item in data:
                click.echo(str(item))
        return
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, (dict, list)):
                click.echo(f"{k}: {json.dumps(v, default=str)}")
            else:
                click.echo(f"{k}: {v}")
        return
    click.echo(str(data))


def _print_table(rows: list[dict]) -> None:
    """Render a list of dicts as a fixed-width text table."""
    if not rows:
        return
    keys: list[str] = []
    for r in rows:
        for k in r.keys():
            if k not in keys and not str(k).startswith("_") and k != "raw":
                keys.append(k)
    # truncate excessive fields
    keys = keys[:10]

    def fmt(v):
        if v is None:
            return "-"
        if isinstance(v, float):
            return f"{v:.2f}"
        if isinstance(v, (list, dict)):
            s = json.dumps(v, default=str)
            return s if len(s) <= 40 else s[:37] + "..."
        s = str(v)
        return s if len(s) <= 40 else s[:37] + "..."

    widths = {k: max(len(k), max(len(fmt(r.get(k))) for r in rows)) for k in keys}
    header = "  ".join(k.ljust(widths[k]) for k in keys)
    sep = "  ".join("-" * widths[k] for k in keys)
    click.echo(header)
    click.echo(sep)
    for r in rows:
        click.echo("  ".join(fmt(r.get(k)).ljust(widths[k]) for k in keys))


def _abort(message: str) -> None:
    click.echo(f"error: {message}", err=True)
    sys.exit(1)


def _parse_xy(entry: str, flag: str = "--point") -> list[float]:
    """Parse one `x,y` coordinate pair from a CLI option value."""
    parts = str(entry).replace(" ", "").split(",")
    if len(parts) != 2:
        _abort(f"{flag} expected x,y, got {entry!r}")
    try:
        return [float(parts[0]), float(parts[1])]
    except ValueError:
        _abort(f"{flag} coordinates must be numbers, got {entry!r}")
    return []  # unreachable; _abort exits


def _parse_xyz(entry: str, label: str = "corner") -> list[float]:
    """Parse one `x,y,z` triple (floor bounds corners, node points)."""
    parts = str(entry).replace(" ", "").split(",")
    if len(parts) != 3:
        _abort(f"{label} expected x,y,z, got {entry!r}")
    try:
        return [float(parts[0]), float(parts[1]), float(parts[2])]
    except ValueError:
        _abort(f"{label} coordinates must be numbers, got {entry!r}")
    return []  # unreachable; _abort exits


# ──────────────────────────────────────────────────────── root


@click.group(context_settings=CONTEXT_SETTINGS, invoke_without_command=True)
@click.option(
    "--base-url", default=None, help="Companion HTTP base URL (default http://localhost:8267)"
)
@click.option("--timeout", default=None, type=int, help="HTTP timeout in seconds (default 30)")
@click.option("--verify-ssl/--no-verify-ssl", default=None, help="Verify TLS cert (default: on)")
@click.option("--k8s-namespace", default=None, help="Kubernetes namespace (default espresense)")
@click.option(
    "--k8s-deployment",
    default=None,
    help="Companion deployment name (default espresense-companion)",
)
@click.option(
    "--k8s-container",
    default=None,
    help="Container name inside the pod (default espresense-companion)",
)
@click.option(
    "--k8s-config-path",
    default=None,
    help="Path to config.yaml inside the pod (default /config/espresense/config.yaml)",
)
@click.option(
    "--config",
    "config_path",
    default=None,
    type=click.Path(),
    help="Path to connection profile (default ~/.config/cli-anything-espresense.json)",
)
@click.option(
    "--json", "as_json", is_flag=True, default=False, help="Emit machine-readable JSON output"
)
@click.pass_context
def cli(
    ctx,
    base_url,
    timeout,
    verify_ssl,
    k8s_namespace,
    k8s_deployment,
    k8s_container,
    k8s_config_path,
    config_path,
    as_json,
):
    """cli-anything-espresense — control ESPresense (companion + per-node firmware)."""
    ctx.ensure_object(dict)
    cfg_path_obj = Path(config_path).expanduser() if config_path else None
    cfg = project.load_config(cfg_path_obj)
    cfg = project.merge_cli_overrides(
        cfg,
        base_url=base_url,
        timeout=timeout,
        verify_ssl=verify_ssl,
        k8s_namespace=k8s_namespace,
        k8s_deployment=k8s_deployment,
        k8s_container=k8s_container,
        k8s_config_path=k8s_config_path,
    )
    ctx.obj.update(cfg)
    ctx.obj["as_json"] = as_json
    ctx.obj["config_path"] = cfg_path_obj
    if ctx.invoked_subcommand is None:
        ctx.invoke(repl)


# ──────────────────────────────────────────────────────── config (profile)


@cli.group()
def config():
    """Local connection profile, plus validation of the espresense config.yaml."""


@config.command("show")
@click.pass_context
def config_show(ctx):
    """Print the resolved profile (merged file + env + flags)."""
    safe = {k: v for k, v in ctx.obj.items() if k not in ("config_path", "as_json")}
    emit(ctx, safe)


@config.command("save")
@click.pass_context
def config_save(ctx):
    """Write the current resolved profile back to disk."""
    safe = {k: v for k, v in ctx.obj.items() if k not in ("config_path", "as_json")}
    out = project.save_config(safe, ctx.obj.get("config_path"))
    emit(ctx, {"saved": str(out)})


@config.command("doctor")
@click.option("--strict", is_flag=True, help="Treat warnings as failures too")
@config_file_option
@click.pass_context
def config_doctor(ctx, strict, config_file):
    """Check config.yaml for the drift that breaks room tracking.

    Detects dangling node `room:` references, whitespace-padded room names,
    duplicate room/node/floor ids, malformed `point:` values and degenerate
    polygons. Read-only — it never writes or restarts anything.

    Exits 1 if any error is found (or any warning, with --strict), so it can
    gate a push:

      cli-anything-espresense config doctor --file cfg.yaml && \
        cli-anything-espresense companion config-push cfg.yaml --restart
    """
    source = make_config_source(ctx, config_file)
    _, parsed = source.fetch()
    report = validate_core.check(parsed)
    report["source"] = source.describe()

    if ctx.obj.get("as_json"):
        emit(ctx, report)
    else:
        counts = report["counts"]
        click.echo(
            f"checked {counts['floors']} floor(s), {counts['rooms']} room(s), "
            f"{counts['nodes']} node(s)"
        )
        for finding in report["errors"] + report["warnings"]:
            marker = "ERROR" if finding["level"] == "error" else "warn "
            click.echo(f"  {marker} [{finding['code']}] {finding['message']}")
        if report["ok"] and not report["warnings"]:
            click.echo("config is clean")
        else:
            click.echo(f"{counts['errors']} error(s), {counts['warnings']} warning(s)")

    if report["errors"] or (strict and report["warnings"]):
        sys.exit(1)


# ──────────────────────────────────────────────────────── companion


@cli.group()
def companion():
    """Talk to the running ESPresense-companion service."""


@companion.command("api")
@click.argument("method")
@click.argument("path")
@click.option("--data", default=None, help="JSON body for POST/PUT")
@click.pass_context
def companion_api_cmd(ctx, method, path, data):
    """Raw API call. Example: companion api GET /api/state/devices"""
    client = make_client(ctx)
    payload = json.loads(data) if data else None
    if method.upper() in ("POST", "PUT"):
        resp = client.request(method, path, json=payload)
    else:
        resp = client.request(method, path)
    try:
        emit(ctx, resp.json())
    except ValueError:
        emit(ctx, resp.text)


@companion.command("info")
@click.pass_context
def companion_info(ctx):
    """High-level health/version summary."""
    client = make_client(ctx)
    try:
        cfg = companion_api.get_config(client)
        nodes = companion_api.list_nodes(client, include_telemetry=True)
        cal = companion_api.get_calibration(client)
    except CompanionError as exc:
        _abort(str(exc))
        return
    online = sum(1 for n in nodes if n.get("online"))
    emit(
        ctx,
        {
            "companion_url": ctx.obj["base_url"],
            "node_count": len(nodes),
            "online": online,
            "offline": len(nodes) - online,
            "device_track_count": len(cfg.get("devices") or []),
            "calibration_r": cal.get("r") if isinstance(cal, dict) else None,
            "calibration_rmse": cal.get("rmse") if isinstance(cal, dict) else None,
            "optimization_enabled": (cfg.get("optimization") or {}).get("enabled"),
        },
    )


@companion.command("config-get")
@click.option(
    "--format",
    "fmt",
    default="yaml",
    type=click.Choice(["yaml", "json"]),
    help="Output format (default yaml from /api/state/config)",
)
@click.pass_context
def companion_config_get(ctx, fmt):
    """Fetch the running companion's parsed config (read-only via API)."""
    client = make_client(ctx)
    cfg = companion_api.get_config(client)
    if fmt == "json" or ctx.obj.get("as_json"):
        click.echo(json.dumps(cfg, indent=2, default=str))
    else:
        from cli_anything.espresense.utils import yaml_io

        click.echo(yaml_io.dumps(cfg))


@companion.command("config-fetch")
@click.option("-o", "--out", type=click.Path(), help="Write to file instead of stdout")
@click.pass_context
def companion_config_fetch(ctx, out):
    """Fetch the on-disk config.yaml from the pod (with comments/order preserved)."""
    target = make_k8s_target(ctx)
    raw = k8s_backend.read_config(target)
    if out:
        Path(out).write_text(raw, encoding="utf-8")
        emit(ctx, {"fetched": out, "bytes": len(raw)})
    else:
        click.echo(raw)


@companion.command("config-push")
@click.argument("file", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--no-backup", is_flag=True, help="Don't leave a timestamped .bak in the pod (default: do)"
)
@click.option(
    "--restart", is_flag=True, help="Rollout-restart the companion deployment after writing"
)
@click.pass_context
def companion_config_push(ctx, file, no_backup, restart):
    """Push a local YAML file to the pod's config.yaml. Optionally restart."""
    target = make_k8s_target(ctx)
    text = Path(file).read_text(encoding="utf-8")
    k8s_backend.write_config(target, text, backup=not no_backup)
    summary = {"bytes_written": len(text.encode("utf-8")), "restarted": False}
    if restart:
        k8s_backend.restart(target)
        summary["restarted"] = True
    emit(ctx, summary)


@companion.command("restart")
@click.option("--wait/--no-wait", default=True, help="Wait for rollout to complete (default: wait)")
@click.option("--timeout", default="120s", help="kubectl rollout timeout (default 120s)")
@click.pass_context
def companion_restart(ctx, wait, timeout):
    """Trigger a rolling restart of the companion deployment."""
    target = make_k8s_target(ctx)
    k8s_backend.restart(target)
    out = {"restarted": True}
    if wait:
        out["rollout"] = k8s_backend.rollout_status(target, timeout=timeout)
    emit(ctx, out)


@companion.command("stream")
@click.option("--duration", default=10.0, type=float, help="Seconds to listen (default 10)")
@click.option("--type", "types", multiple=True, help="Filter to specific event types (repeatable)")
@click.option("--show-all", is_flag=True, help="Include all devices, not just tracked")
@click.pass_context
def companion_stream(ctx, duration, types, show_all):
    """Subscribe to the /ws live event stream for N seconds."""
    base = ctx.obj["base_url"]
    events = stream_core.stream(
        base_url=base,
        show_all=show_all,
        duration=duration,
        types=set(types) if types else None,
    )
    emit(ctx, events)


@companion.command("locator")
@click.pass_context
def companion_locator(ctx):
    """Show the locator/solver state (/api/state/locator)."""
    client = make_client(ctx)
    emit(ctx, companion_api.get_locator_state(client))


@companion.command("firmware-types")
@click.pass_context
def companion_firmware_types(ctx):
    """List the firmware flavors/versions the companion can flash to a node.

    Pair with `nodes update-firmware <id> <url>` to pick a valid target.
    """
    client = make_client(ctx)
    emit(ctx, companion_api.list_firmware_types(client))


@companion.command("pod")
@click.pass_context
def companion_pod(ctx):
    """Resolve the companion's running pod name via kubectl.

    Useful for confirming the kubectl target is right before a config-push,
    and for hand-running `kubectl logs` against the same pod.
    """
    target = make_k8s_target(ctx)
    name = k8s_backend.pod_name(target)
    emit(
        ctx,
        {
            "namespace": target.namespace,
            "deployment": target.deployment,
            "pod": name or None,
            "resolved": bool(name),
        },
    )


# ── global settings ──────────────────────────────────────────────────────
# Deployment-wide knobs that live OUTSIDE config.yaml (telemetry cadence,
# expiration, GPS origin, include/exclude filters, ...). Served at
# GET/POST /api/settings and mirrored on espresense/settings/<key>/set —
# `mqtt set-global` is the broker-side twin of `companion settings-set`.


@companion.command("settings-keys")
@click.pass_context
def companion_settings_keys(ctx):
    """List the global settings the companion understands (key/kind/description).

    These are the deployment-wide knobs *outside* config.yaml — the
    `settings` group edits that file, not these. Unknown keys are still
    accepted by `companion settings-set`; the companion owns the schema.
    """
    emit(ctx, global_settings_core.describe())


@companion.command("settings-get")
@click.option("--section", default=None, help="Only this key, e.g. expiration")
@click.option("--reveal", is_flag=True, help="Do not redact secret values")
@click.pass_context
def companion_settings_get(ctx, section, reveal):
    """Read the companion's global settings (GET /api/settings).

    Secrets are redacted by default — this output routinely lands in
    transcripts and issues.

    Example:
      companion settings-get --section expiration
    """
    client = make_client(ctx)
    try:
        emit(ctx, global_settings_core.fetch(client, key=section, reveal=reveal))
    except CompanionError as exc:
        _abort(str(exc))
        return
    except global_settings_core.GlobalSettingsError as exc:
        _abort(str(exc))
        return


@companion.command("settings-set")
@click.argument("key")
@click.argument("value")
@click.option(
    "--type",
    "value_type",
    type=click.Choice(["auto", "str", "int", "float", "bool", "json"]),
    default="auto",
    help="How to read VALUE (default: the key's declared kind, else auto-detect)",
)
@click.pass_context
def companion_settings_set(ctx, key, value, value_type):
    """Set one global setting via POST /api/settings.

    These live outside config.yaml, so there is no --file / --restart here —
    the companion applies the value immediately and on restart.

    Example:
      companion settings-set expiration 300
      companion settings-set gps '{"lat":51.5,"lng":-0.1,"elev":30}'
    """
    client = make_client(ctx)
    try:
        out = global_settings_core.update(client, key, value, kind=value_type)
    except CompanionError as exc:
        _abort(str(exc))
        return
    except global_settings_core.GlobalSettingsError as exc:
        _abort(str(exc))
        return
    emit(ctx, out)


# ──────────────────────────────────────────────────────── floors


@cli.group()
def floors():
    """Inspect and edit the floors declared in config.yaml (incl. 3D bounds)."""


@floors.command("list")
@config_file_option
@click.pass_context
def floors_list(ctx, config_file):
    """List every floor with its room and node counts."""
    source = make_config_source(ctx, config_file)
    _, parsed = source.fetch()
    emit(ctx, config_core.list_floors(parsed))


@floors.command("show")
@click.argument("floor_id", required=False)
@config_file_option
@click.pass_context
def floors_show(ctx, floor_id, config_file):
    """Show one floor in full (defaults to the first floor)."""
    source = make_config_source(ctx, config_file)
    _, parsed = source.fetch()
    try:
        floor = (
            config_core.find_floor(parsed, floor_id)
            if floor_id
            else config_core.first_floor(parsed)
        )
    except KeyError as exc:
        _abort(str(exc))
        return
    emit(ctx, json.loads(json.dumps(floor, default=str)))


@floors.command("add")
@click.argument("floor_id")
@click.option("--name", default=None, help="Human-readable floor name, e.g. 'First Floor'")
@click.option("--bounds", default=None, help="Two corners as 'x,y,z x,y,z' (space separated)")
@click.option("--restart/--no-restart", default=False)
@click.option("--dry-run", is_flag=True)
@config_file_option
@click.pass_context
def floors_add(ctx, floor_id, name, bounds, restart, dry_run, config_file):
    """Create an empty floor to hang rooms off.

    Example:
      floors add ff --name "First Floor" --bounds "0,0,0 10,10,2.4"
    """
    parsed_bounds = None
    if bounds:
        corners = str(bounds).split()
        if len(corners) != 2:
            _abort("--bounds expects two corners: 'x,y,z x,y,z'")
        parsed_bounds = [_parse_xyz(c, "--bounds corner") for c in corners]
    source = make_config_source(ctx, config_file)
    _, parsed = source.fetch()
    try:
        out = floors_core.add_floor(parsed, floor_id, name=name, bounds=parsed_bounds)
    except (ValueError, geometry.GeometryError) as exc:
        _abort(str(exc))
        return
    out["dry_run"] = dry_run
    if not dry_run:
        out["pushed"] = source.push(parsed, restart=restart)
    emit(ctx, out)


@floors.command("rename")
@click.argument("floor_id")
@click.argument("name")
@click.option("--restart/--no-restart", default=False)
@click.option("--dry-run", is_flag=True)
@config_file_option
@click.pass_context
def floors_rename(ctx, floor_id, name, restart, dry_run, config_file):
    """Change a floor's display `name:` (the `id` is left alone — see `floors retag`)."""
    source = make_config_source(ctx, config_file)
    _, parsed = source.fetch()
    try:
        out = floors_core.rename_floor(parsed, floor_id, name)
    except KeyError as exc:
        _abort(str(exc))
        return
    out["dry_run"] = dry_run
    if not dry_run:
        out["pushed"] = source.push(parsed, restart=restart)
    emit(ctx, out)


@floors.command("retag")
@click.argument("old_id")
@click.argument("new_id")
@click.option("--restart/--no-restart", default=False)
@click.option("--dry-run", is_flag=True)
@config_file_option
@click.pass_context
def floors_retag(ctx, old_id, new_id, restart, dry_run, config_file):
    """Change a floor's `id` AND every node `floors:` entry pointing at it.

    Doing only half of this leaves nodes referencing a floor that no longer
    exists; `config doctor` reports that as dangling_floor_ref.
    """
    source = make_config_source(ctx, config_file)
    _, parsed = source.fetch()
    try:
        out = floors_core.retag(parsed, old_id, new_id)
    except (KeyError, ValueError) as exc:
        _abort(str(exc))
        return
    out["dry_run"] = dry_run
    if not dry_run and out["id_changed"]:
        out["pushed"] = source.push(parsed, restart=restart)
    emit(ctx, out)


@floors.command("set-bounds", context_settings=COORD_SETTINGS)
@click.argument("floor_id")
@click.argument("min_corner")
@click.argument("max_corner")
@click.option("--restart/--no-restart", default=False)
@click.option("--dry-run", is_flag=True)
@config_file_option
@click.pass_context
def floors_set_bounds(ctx, floor_id, min_corner, max_corner, restart, dry_run, config_file):
    """Set a floor's 3D bounds explicitly.

    Example:
      floors set-bounds gf 0,0,0 10,8,2.4
    """
    bounds = [_parse_xyz(min_corner, "MIN_CORNER"), _parse_xyz(max_corner, "MAX_CORNER")]
    source = make_config_source(ctx, config_file)
    _, parsed = source.fetch()
    try:
        out = floors_core.set_bounds(parsed, floor_id, bounds)
    except (KeyError, geometry.GeometryError) as exc:
        _abort(str(exc))
        return
    out["dry_run"] = dry_run
    if not dry_run:
        out["pushed"] = source.push(parsed, restart=restart)
    emit(ctx, out)


@floors.command("fit-bounds")
@click.argument("floor_id")
@click.option("--margin", default=0.0, type=float, help="Padding in metres around the rooms")
@click.option("--z-min", default=None, type=float, help="Floor height (default: keep/derive)")
@click.option("--z-max", default=None, type=float, help="Ceiling height (default: keep/derive)")
@click.option("--restart/--no-restart", default=False)
@click.option("--dry-run", is_flag=True)
@config_file_option
@click.pass_context
def floors_fit_bounds(ctx, floor_id, margin, z_min, z_max, restart, dry_run, config_file):
    """Recompute a floor's bounds from the room polygons on it.

    Run this after `rooms add` / `rooms move` / `rooms scale` so the floor box
    still contains its rooms. Pair with --dry-run to see the numbers first.
    """
    source = make_config_source(ctx, config_file)
    _, parsed = source.fetch()
    try:
        out = floors_core.fit_bounds(parsed, floor_id, margin=margin, z_min=z_min, z_max=z_max)
    except (KeyError, ValueError, geometry.GeometryError) as exc:
        _abort(str(exc))
        return
    out["dry_run"] = dry_run
    if not dry_run:
        out["pushed"] = source.push(parsed, restart=restart)
    emit(ctx, out)


@floors.command("delete")
@click.argument("floor_id")
@click.option("--restart/--no-restart", default=False)
@click.option("--dry-run", is_flag=True)
@click.option("--force", is_flag=True, help="Delete even though rooms/nodes still reference it")
@config_file_option
@click.pass_context
def floors_delete(ctx, floor_id, restart, dry_run, force, config_file):
    """Delete a floor and the rooms on it.

    Refuses to write while any node still points at one of those rooms (or
    lists the floor) unless --force is given; the affected node names come
    back as `orphaned_nodes` / `nodes_referencing` either way.
    """
    source = make_config_source(ctx, config_file)
    _, parsed = source.fetch()
    out = floors_core.delete_floor(parsed, floor_id)
    out["dry_run"] = dry_run
    if not out["deleted"]:
        out["refused"] = f"no floor with id={floor_id!r}"
        emit(ctx, out)
        sys.exit(1)
    blockers = sorted(set(out["orphaned_nodes"]) | set(out["nodes_referencing"]))
    if blockers and not force:
        out["pushed"] = None
        out["refused"] = (
            f"{len(blockers)} node(s) still tied to floor {floor_id!r} "
            f"({', '.join(str(b) for b in blockers)}); repoint them first or pass --force"
        )
        emit(ctx, out)
        sys.exit(1)
    if not dry_run:
        out["pushed"] = source.push(parsed, restart=restart)
    emit(ctx, out)


# ──────────────────────────────────────────────────────── rooms


@cli.group()
def rooms():
    """List, rename, reshape and measure rooms (floor polygons) in the config."""


@rooms.command("list")
@click.option("--floor", default=None, help="Restrict to this floor id")
@config_file_option
@click.pass_context
def rooms_list(ctx, floor, config_file):
    """List rooms across all floors (or one floor)."""
    source = make_config_source(ctx, config_file)
    _, parsed = source.fetch()
    rows = rooms_core.list_rooms(parsed, floor_id=floor)
    emit(ctx, rows)


@rooms.command("rename")
@click.argument("old")
@click.argument("new")
@click.option(
    "--restart/--no-restart",
    default=False,
    help="Restart the companion afterwards (default: no — review first)",
)
@click.option("--dry-run", is_flag=True, help="Show the proposed edits without writing")
@config_file_option
@click.pass_context
def rooms_rename(ctx, old, new, restart, dry_run, config_file):
    """Rename ONE room polygon AND all nodes that referenced it.

    Also strips trailing-whitespace bugs on every node's `room:` field as a
    side effect, since those are a common cause of "doesn't match polygon" sync issues.
    """
    source = make_config_source(ctx, config_file)
    _, parsed = source.fetch()
    summary = rooms_core.rename(parsed, old, new)
    summary["dry_run"] = dry_run
    if not dry_run and (
        summary["rooms_renamed"] > 0
        or summary["nodes_repointed"] > 0
        or summary["whitespace_fixes"] > 0
    ):
        push = source.push(parsed, restart=restart)
        summary["pushed"] = push
    emit(ctx, summary)


@rooms.command("rotate")
@click.option(
    "--map",
    "mappings",
    multiple=True,
    required=True,
    help="old=new (repeatable). Applied atomically — supports swaps & cycles.",
)
@click.option(
    "--restart/--no-restart", default=False, help="Restart the companion afterwards (default: no)"
)
@click.option("--dry-run", is_flag=True, help="Show the proposed edits without writing")
@config_file_option
@click.pass_context
def rooms_rotate(ctx, mappings, restart, dry_run, config_file):
    """Apply N room renames atomically. Use for swaps and rotations.

    Examples:
      --map "Spare Room=Noah Bedroom" \\
      --map "Noah Bedroom=Sophie Bedroom" \\
      --map "Sophie Bedroom=Spare Room"
    """
    parsed_map: dict[str, str] = {}
    for entry in mappings:
        if "=" not in entry:
            _abort(f"--map expected old=new, got {entry!r}")
        old, new = entry.split("=", 1)
        old = old.strip()
        new = new.strip()
        if not old or not new:
            _abort(f"--map empty side in {entry!r}")
        parsed_map[old] = new
    source = make_config_source(ctx, config_file)
    _, parsed = source.fetch()
    summary = rooms_core.rotate(parsed, parsed_map)
    summary["dry_run"] = dry_run
    if not dry_run:
        push = source.push(parsed, restart=restart)
        summary["pushed"] = push
    emit(ctx, summary)


@rooms.command("repoint-node")
@click.argument("node_name")
@click.argument("room_name")
@click.option("--restart/--no-restart", default=False)
@click.option("--dry-run", is_flag=True)
@config_file_option
@click.pass_context
def rooms_repoint(ctx, node_name, room_name, restart, dry_run, config_file):
    """Set a single node's `room:` to a specific room (without renaming anything)."""
    source = make_config_source(ctx, config_file)
    _, parsed = source.fetch()
    out = rooms_core.repoint_node(parsed, node_name, room_name)
    out["dry_run"] = dry_run
    if not dry_run and out["found"]:
        push = source.push(parsed, restart=restart)
        out["pushed"] = push
    emit(ctx, out)


@rooms.command("add")
@click.argument("floor_id")
@click.argument("name")
@click.option(
    "--point",
    "points",
    multiple=True,
    required=True,
    help="Polygon vertex as x,y (repeatable, min 3 for a real room).",
)
@click.option("--color", default=None, help="Room colour, e.g. '#a3c9f9'")
@click.option("--restart/--no-restart", default=False)
@click.option("--dry-run", is_flag=True)
@config_file_option
@click.pass_context
def rooms_add(ctx, floor_id, name, points, color, restart, dry_run, config_file):
    """Add a new room polygon to a floor.

    Example:
      rooms add gf "Office" --point 0,0 --point 4,0 --point 4,3 --point 0,3
    """
    parsed_points: list[list[float]] = [_parse_xy(entry) for entry in points]
    source = make_config_source(ctx, config_file)
    _, parsed = source.fetch()
    try:
        out = rooms_core.add_room(parsed, floor_id, name, parsed_points, color=color)
    except (ValueError, KeyError) as exc:
        _abort(str(exc))
        return
    out["dry_run"] = dry_run
    if not dry_run:
        out["pushed"] = source.push(parsed, restart=restart)
    emit(ctx, out)


@rooms.command("delete")
@click.argument("name")
@click.option("--restart/--no-restart", default=False)
@click.option("--dry-run", is_flag=True)
@click.option("--force", is_flag=True, help="Delete even if nodes still reference the room")
@config_file_option
@click.pass_context
def rooms_delete(ctx, name, restart, dry_run, force, config_file):
    """Delete a room polygon.

    Nodes still pointing at it are reported as `orphaned_nodes` and are NOT
    rewritten — repoint them with `rooms repoint-node`. Refuses to write if
    any node would be orphaned unless --force is given.
    """
    source = make_config_source(ctx, config_file)
    _, parsed = source.fetch()
    out = rooms_core.delete_room(parsed, name)
    out["dry_run"] = dry_run
    if out["orphaned_nodes"] and not force:
        out["pushed"] = None
        out["refused"] = (
            f"{len(out['orphaned_nodes'])} node(s) still reference {name!r}; "
            "repoint them first or pass --force"
        )
        emit(ctx, out)
        sys.exit(1)
    if not dry_run and out["deleted"]:
        out["pushed"] = source.push(parsed, restart=restart)
    emit(ctx, out)


@rooms.command("geometry")
@click.option("--floor", default=None, help="Restrict to this floor id")
@config_file_option
@click.pass_context
def rooms_geometry(ctx, floor, config_file):
    """Area, perimeter, centroid, bbox and node containment per room.

    `nodes_outside` lists nodes whose `point:` falls outside the room their
    `room:` field names — a config that validates but localises wrongly.
    Read-only.
    """
    source = make_config_source(ctx, config_file)
    _, parsed = source.fetch()
    emit(ctx, rooms_core.geometry_report(parsed, floor_id=floor))


@rooms.command("locate", context_settings=COORD_SETTINGS)
@click.argument("x", type=float)
@click.argument("y", type=float)
@click.option("--floor", default=None, help="Restrict to this floor id")
@config_file_option
@click.pass_context
def rooms_locate(ctx, x, y, floor, config_file):
    """Which room polygon(s) contain (X, Y)?

    Use before `nodes set-point` to confirm a coordinate lands where you think
    it does. Exits 1 when the point is in no room at all. Read-only.
    """
    source = make_config_source(ctx, config_file)
    _, parsed = source.fetch()
    hits = rooms_core.locate_point(parsed, x, y, floor_id=floor)
    if not hits:
        emit(ctx, [] if ctx.obj.get("as_json") else f"({x}, {y}) is not inside any room")
        sys.exit(1)
    emit(ctx, hits)


@rooms.command("overlaps")
@click.option("--floor", default=None, help="Restrict to this floor id")
@config_file_option
@click.pass_context
def rooms_overlaps(ctx, floor, config_file):
    """Report pairs of rooms on one floor whose polygons share area.

    Rooms that merely share a wall are not overlaps. Exits 1 if any overlap is
    found, so it can gate a push. Read-only.
    """
    source = make_config_source(ctx, config_file)
    _, parsed = source.fetch()
    pairs = rooms_core.find_overlaps(parsed, floor_id=floor)
    if not pairs:
        emit(ctx, [] if ctx.obj.get("as_json") else "no overlapping rooms")
        return
    emit(ctx, pairs)
    sys.exit(1)


@rooms.command("set-points")
@click.argument("name")
@click.option(
    "--point", "points", multiple=True, required=True, help="Polygon vertex as x,y (repeatable)"
)
@click.option("--restart/--no-restart", default=False)
@click.option("--dry-run", is_flag=True)
@config_file_option
@click.pass_context
def rooms_set_points(ctx, name, points, restart, dry_run, config_file):
    """Replace a room's polygon wholesale (redraw it).

    Example:
      rooms set-points Office --point 0,0 --point 5,0 --point 5,4 --point 0,4
    """
    parsed_points = [_parse_xy(entry) for entry in points]
    source = make_config_source(ctx, config_file)
    _, parsed = source.fetch()
    try:
        out = rooms_core.set_points(parsed, name, parsed_points)
    except (KeyError, geometry.GeometryError) as exc:
        _abort(str(exc))
        return
    out["dry_run"] = dry_run
    if not dry_run:
        out["pushed"] = source.push(parsed, restart=restart)
    emit(ctx, out)


@rooms.command("move", context_settings=COORD_SETTINGS)
@click.argument("name")
@click.argument("dx", type=float)
@click.argument("dy", type=float)
@click.option("--restart/--no-restart", default=False)
@click.option("--dry-run", is_flag=True)
@config_file_option
@click.pass_context
def rooms_move(ctx, name, dx, dy, restart, dry_run, config_file):
    """Translate a whole room polygon by (DX, DY) metres, shape unchanged."""
    source = make_config_source(ctx, config_file)
    _, parsed = source.fetch()
    try:
        out = rooms_core.move_room(parsed, name, dx, dy)
    except (KeyError, geometry.GeometryError) as exc:
        _abort(str(exc))
        return
    out["dry_run"] = dry_run
    if not dry_run:
        out["pushed"] = source.push(parsed, restart=restart)
    emit(ctx, out)


@rooms.command("scale")
@click.argument("name")
@click.argument("factor", type=float)
@click.option(
    "--about-origin",
    is_flag=True,
    help="Scale about (0,0) instead of the room's own centroid (which keeps it in place)",
)
@click.option("--restart/--no-restart", default=False)
@click.option("--dry-run", is_flag=True)
@config_file_option
@click.pass_context
def rooms_scale(ctx, name, factor, about_origin, restart, dry_run, config_file):
    """Grow or shrink a room polygon by FACTOR (1.1 = 10% bigger)."""
    source = make_config_source(ctx, config_file)
    _, parsed = source.fetch()
    try:
        out = rooms_core.scale_room(parsed, name, factor, about_origin=about_origin)
    except (KeyError, geometry.GeometryError) as exc:
        _abort(str(exc))
        return
    out["dry_run"] = dry_run
    if not dry_run:
        out["pushed"] = source.push(parsed, restart=restart)
    emit(ctx, out)


@rooms.command("set-color")
@click.argument("name")
@click.argument("color", required=False)
@click.option("--clear", is_flag=True, help="Remove the room's color instead of setting one")
@click.option("--restart/--no-restart", default=False)
@click.option("--dry-run", is_flag=True)
@config_file_option
@click.pass_context
def rooms_set_color(ctx, name, color, clear, restart, dry_run, config_file):
    """Set (or --clear) a room's `color:`, e.g. rooms set-color Office '#a3c9f9'."""
    if clear and color:
        _abort("pass either a COLOR or --clear, not both")
    if not clear and not color:
        _abort("COLOR is required unless --clear is given")
    source = make_config_source(ctx, config_file)
    _, parsed = source.fetch()
    try:
        out = rooms_core.set_color(parsed, name, None if clear else color)
    except KeyError as exc:
        _abort(str(exc))
        return
    out["dry_run"] = dry_run
    if not dry_run:
        out["pushed"] = source.push(parsed, restart=restart)
    emit(ctx, out)


# ──────────────────────────────────────────────────────── nodes (companion view)


@cli.group()
def nodes():
    """List, rename, configure nodes via the companion (config.yaml + API)."""


@nodes.command("list")
@click.option(
    "--merge-live/--no-merge-live",
    default=True,
    help="Join config rows with live state from the API (default: yes)",
)
@click.option(
    "--include-telemetry/--no-include-telemetry",
    default=True,
    help="Include telemetry when calling the live API (default: yes)",
)
@config_file_option
@click.pass_context
def nodes_list(ctx, merge_live, include_telemetry, config_file):
    """List nodes — by default merges config.yaml with live API state."""
    source = make_config_source(ctx, config_file)
    _, parsed = source.fetch()
    if not merge_live:
        emit(ctx, nodes_core.list_config_nodes(parsed))
        return
    client = make_client(ctx)
    try:
        live = nodes_core.list_live_nodes(client, include_telemetry=include_telemetry)
    except CompanionError as exc:
        click.echo(f"warning: live API unreachable, showing config-only: {exc}", err=True)
        emit(ctx, nodes_core.list_config_nodes(parsed))
        return
    emit(ctx, nodes_core.merged_view(parsed, live))


@nodes.command("show")
@click.argument("node_id")
@click.pass_context
def nodes_show(ctx, node_id):
    """Show one node's full settings via the companion API."""
    client = make_client(ctx)
    emit(ctx, companion_api.get_node(client, node_id))


@nodes.command("rename-in-config")
@click.argument("old")
@click.argument("new")
@click.option("--restart/--no-restart", default=False)
@click.option("--dry-run", is_flag=True)
@config_file_option
@click.pass_context
def nodes_rename_in_config(ctx, old, new, restart, dry_run, config_file):
    """Rename a node's `name:` in config.yaml only (does NOT touch the physical device)."""
    source = make_config_source(ctx, config_file)
    _, parsed = source.fetch()
    out = nodes_core.rename_in_config(parsed, old, new)
    out["dry_run"] = dry_run
    if not dry_run and out["found"]:
        push = source.push(parsed, restart=restart)
        out["pushed"] = push
    emit(ctx, out)


@nodes.command("set-point", context_settings=COORD_SETTINGS)
@click.argument("name")
@click.argument("x", type=float)
@click.argument("y", type=float)
@click.argument("z", type=float)
@click.option("--restart/--no-restart", default=False)
@click.option("--dry-run", is_flag=True)
@config_file_option
@click.pass_context
def nodes_set_point(ctx, name, x, y, z, restart, dry_run, config_file):
    """Set a node's 3D point in config.yaml."""
    source = make_config_source(ctx, config_file)
    _, parsed = source.fetch()
    out = nodes_core.set_point(parsed, name, [x, y, z])
    out["dry_run"] = dry_run
    if not dry_run and out["found"]:
        push = source.push(parsed, restart=restart)
        out["pushed"] = push
    emit(ctx, out)


@nodes.command("place")
@click.argument("name")
@click.option("--room", default=None, help="Room to place it in (default: its current room)")
@click.option("--z", default=None, type=float, help="Height in metres (default: keep, else 2.4)")
@click.option("--restart/--no-restart", default=False)
@click.option("--dry-run", is_flag=True)
@config_file_option
@click.pass_context
def nodes_place(ctx, name, room, z, restart, dry_run, config_file):
    """Snap a node's `point:` to the centroid of its room.

    The coordinate-free way to fix (or initialise) node placement: the centroid
    is guaranteed to be inside a convex room, so the node stops being drawn
    outside the polygon it names. Passing --room also repoints the node.
    """
    source = make_config_source(ctx, config_file)
    _, parsed = source.fetch()
    try:
        out = nodes_core.place_in_room(parsed, name, room=room, z=z)
    except (KeyError, ValueError, geometry.GeometryError) as exc:
        _abort(str(exc))
        return
    out["dry_run"] = dry_run
    if not out["found"]:
        out["refused"] = f"no node named {name!r} in config.yaml"
        emit(ctx, out)
        sys.exit(1)
    if not dry_run:
        out["pushed"] = source.push(parsed, restart=restart)
    emit(ctx, out)


@nodes.command("restart")
@click.argument("node_id")
@click.pass_context
def nodes_restart(ctx, node_id):
    """Restart a node via the companion API (which publishes the MQTT command)."""
    client = make_client(ctx)
    out = companion_api.restart_node(client, node_id)
    emit(ctx, {"node_id": node_id, "restart": "sent", "response": out})


@nodes.command("delete")
@click.argument("node_id")
@click.confirmation_option(prompt="Delete this node's settings and telemetry?")
@click.pass_context
def nodes_delete(ctx, node_id):
    """Delete a node from the companion's settings (does NOT remove from config.yaml)."""
    client = make_client(ctx)
    companion_api.delete_node(client, node_id)
    emit(ctx, {"node_id": node_id, "deleted": True})


@nodes.command("update-firmware")
@click.argument("node_id")
@click.argument("url")
@click.pass_context
def nodes_update_firmware(ctx, node_id, url):
    """Trigger an OTA firmware update on a node (URL must be a GitHub release)."""
    client = make_client(ctx)
    companion_api.update_node_firmware(client, node_id, url)
    emit(ctx, {"node_id": node_id, "update_triggered": True, "url": url})


@nodes.command("put-settings")
@click.argument("node_id")
@click.argument("settings_json")
@click.pass_context
def nodes_put_settings(ctx, node_id, settings_json):
    """Push a NodeSettings JSON blob via the companion API."""
    try:
        payload = json.loads(settings_json)
    except json.JSONDecodeError as e:
        _abort(f"settings_json is not valid JSON: {e}")
        return
    client = make_client(ctx)
    companion_api.put_node(client, node_id, payload)
    emit(ctx, {"node_id": node_id, "updated": True})


@nodes.command("add")
@click.argument("name")
@click.option("--room", default=None, help="Room name this node sits in")
@click.option("--point", default=None, help="Position as x,y,z")
@click.option("--floor", "floors_opt", multiple=True, help="Floor id (repeatable)")
@click.option("--disabled", is_flag=True, help="Add with enabled: false")
@click.option("--mobile", is_flag=True, help="Add with stationary: false")
@click.option("--restart/--no-restart", default=False)
@click.option("--dry-run", is_flag=True)
@config_file_option
@click.pass_context
def nodes_add(ctx, name, room, point, floors_opt, disabled, mobile, restart, dry_run, config_file):
    """Add a new node entry to config.yaml.

    Example:
      nodes add office-node --room "Office" --point 1.2,3.4,2.1
    """
    parsed_point = None
    if point:
        parts = point.replace(" ", "").split(",")
        if len(parts) != 3:
            _abort(f"--point expected x,y,z, got {point!r}")
        try:
            parsed_point = [float(v) for v in parts]
        except ValueError:
            _abort(f"--point coordinates must be numbers, got {point!r}")
    source = make_config_source(ctx, config_file)
    _, parsed = source.fetch()
    try:
        out = nodes_core.add(
            parsed,
            name,
            room=room,
            point=parsed_point,
            floors=list(floors_opt) or None,
            enabled=not disabled,
            stationary=not mobile,
        )
    except ValueError as exc:
        _abort(str(exc))
        return
    out["dry_run"] = dry_run
    if not dry_run:
        out["pushed"] = source.push(parsed, restart=restart)
    emit(ctx, out)


@nodes.command("remove-from-config")
@click.argument("name")
@click.option("--restart/--no-restart", default=False)
@click.option("--dry-run", is_flag=True)
@config_file_option
@click.pass_context
def nodes_remove_from_config(ctx, name, restart, dry_run, config_file):
    """Remove a node entry from config.yaml.

    The counterpart to `nodes delete`, which only clears the companion's
    runtime settings/telemetry and leaves config.yaml untouched. Use both to
    fully retire a node.
    """
    source = make_config_source(ctx, config_file)
    _, parsed = source.fetch()
    removed = nodes_core.remove(parsed, name)
    out = {"name": name, "removed": removed, "dry_run": dry_run}
    if not dry_run and removed:
        out["pushed"] = source.push(parsed, restart=restart)
    emit(ctx, out)


# ──────────────────────────────────────────────────────── node (direct HTTP to one ESP)


@cli.group()
def node():
    """Talk directly to one ESP node by IP/hostname (firmware web server)."""


def _node_client(ctx, host: str) -> node_direct.NodeClient:
    return node_direct.NodeClient(
        host,
        port=ctx.obj.get("node_http_port", 80),
        timeout=ctx.obj.get("node_http_timeout", 10),
    )


@node.command("info")
@click.argument("host")
@click.option("--show-all", is_flag=True, help="Include seen-devices list")
@click.pass_context
def node_info(ctx, host, show_all):
    """GET /json on the node — room, firmware, version, devices."""
    emit(ctx, _node_client(ctx, host).info(show_all=show_all))


@node.command("restart")
@click.argument("host")
@click.pass_context
def node_restart(ctx, host):
    """POST /restart on the node."""
    ok = _node_client(ctx, host).restart()
    emit(ctx, {"host": host, "restarted": ok})


@node.command("settings")
@click.argument("host")
@click.option(
    "--section",
    default="extras",
    type=click.Choice(["main", "extras", "hardware"]),
    help="Settings page: main=wifi/mqtt, extras=BLE, hardware=sensors",
)
@click.pass_context
def node_settings(ctx, host, section):
    """GET /wifi/<section> on the node — read settings as JSON."""
    emit(ctx, _node_client(ctx, host).get_settings(section))


@node.command("set")
@click.argument("host")
@click.argument("fields", nargs=-1)
@click.option("--section", default="extras", type=click.Choice(["main", "extras", "hardware"]))
@click.pass_context
def node_set(ctx, host, fields, section):
    """POST settings on the node. Pass key=value pairs as positional args.

    Example: node set 10.32.101.32 absorption=2.8 tx_ref_rssi=-59
    """
    payload: dict = {}
    for f in fields:
        if "=" not in f:
            _abort(f"expected key=value, got {f!r}")
        k, v = f.split("=", 1)
        payload[k.strip()] = v
    if not payload:
        _abort("no fields supplied")
    res = _node_client(ctx, host).put_settings(section, payload)
    emit(ctx, {"host": host, "section": section, "fields": payload, **res})


@node.command("rename")
@click.argument("host")
@click.argument("new_name")
@click.pass_context
def node_rename(ctx, host, new_name):
    """Rename a physical node — sets `room` and triggers a restart.

    The MQTT id and hostname (espresense-<kebab>) both follow this name.
    """
    res = _node_client(ctx, host).rename(new_name)
    emit(ctx, {"host": host, **res})


@node.command("scan-wifi")
@click.argument("host")
@click.pass_context
def node_scan_wifi(
    ctx,
    host,
):
    emit(ctx, _node_client(ctx, host).scan_wifi())


@node.command("devices")
@click.argument("host")
@click.pass_context
def node_devices(ctx, host):
    """Devices currently seen by this node."""
    info = _node_client(ctx, host).info(show_all=True)
    emit(ctx, info.get("devices") or [])


@node.command("reboot")
@click.argument("host")
@click.pass_context
def node_reboot(ctx, host):
    """POST /reboot on the node (firmware alias of /restart)."""
    ok = _node_client(ctx, host).reboot()
    emit(ctx, {"host": host, "rebooted": ok})


@node.command("config-list")
@click.argument("host")
@click.pass_context
def node_config_list(ctx, host):
    """List the per-device config entries stored on this node."""
    emit(ctx, _node_client(ctx, host).list_device_configs())


@node.command("config-set")
@click.argument("host")
@click.argument("device_id")
@click.option("--alias", default=None, help="Short alias the node reports instead of the raw id")
@click.option("--name", default=None, help="Friendly device name")
@click.option("--rssi-at-1m", default=None, type=int, help="Calibrated rssi@1m for this device")
@click.pass_context
def node_config_set(ctx, host, device_id, alias, name, rssi_at_1m):
    """Add or update one device-config entry on this node (POST /json/configs).

    Example:
      node config-set 10.32.101.32 apple:1005:9-12 --name "Jon Watch" --rssi-at-1m -59
    """
    if alias is None and name is None and rssi_at_1m is None:
        _abort("nothing to set — pass at least one of --alias / --name / --rssi-at-1m")
    res = _node_client(ctx, host).upsert_device_config(
        device_id, alias=alias, name=name, rssi_at_1m=rssi_at_1m
    )
    emit(ctx, {"host": host, "device_id": device_id, "result": res})


@node.command("config-delete")
@click.argument("host")
@click.argument("device_id")
@click.pass_context
def node_config_delete(ctx, host, device_id):
    """Delete one device-config entry from this node (DELETE /json/configs)."""
    ok = _node_client(ctx, host).delete_device_config(device_id)
    emit(ctx, {"host": host, "device_id": device_id, "deleted": ok})


# ──────────────────────────────────────────────────────── devices (companion view)


@cli.group()
def devices():
    """Tracked devices (phones/tags/beacons): the companion's live view, and
    the durable `devices:` registry in config.yaml (`...-config` commands)."""


@devices.command("whereis")
@click.argument("device_id")
@click.pass_context
def devices_whereis(ctx, device_id):
    """Last known position of one tracked device (room, floor, coordinates, when).

    Reads the companion's /api/history/<id> — the same data `history get`
    returns, reduced to the most recent point. Exits 1 when the device has
    never been seen.
    """
    client = make_client(ctx)
    try:
        out = telemetry_core.whereis(client, device_id)
    except telemetry_core.TelemetryError as exc:
        _abort(str(exc))
        return
    emit(ctx, out)
    if not out.get("found"):
        sys.exit(1)


@devices.command("occupancy")
@click.option("--floor", default=None, help="Only devices on this floor (id or name)")
@click.option("--show-all", is_flag=True, help="Include untracked devices too")
@click.pass_context
def devices_occupancy(ctx, floor, show_all):
    """Which tracked devices are currently in which room (companion live view).

    A grouped read of `devices list` — rooms with their occupants, plus the
    devices the companion cannot place yet.
    """
    client = make_client(ctx)
    rows = devices_core.list_devices(client, show_all=show_all)
    emit(ctx, telemetry_core.occupancy(rows, floor=floor))


@devices.command("list")
@click.option("--show-all", is_flag=True, help="Include untracked devices too")
@click.pass_context
def devices_list(ctx, show_all):
    client = make_client(ctx)
    emit(ctx, devices_core.list_devices(client, show_all=show_all))


@devices.command("show")
@click.argument("device_id")
@click.pass_context
def devices_show(ctx, device_id):
    client = make_client(ctx)
    emit(ctx, devices_core.get_device(client, device_id))


@devices.command("set")
@click.argument("device_id")
@click.option("--name", default=None)
@click.option("--ref-rssi", default=None, type=int)
@click.option("--anchor-x", default=None, type=float)
@click.option("--anchor-y", default=None, type=float)
@click.option("--anchor-z", default=None, type=float)
@click.pass_context
def devices_set(ctx, device_id, name, ref_rssi, anchor_x, anchor_y, anchor_z):
    client = make_client(ctx)
    out = devices_core.update_device(
        client,
        device_id,
        name=name,
        ref_rssi=ref_rssi,
        anchored_x=anchor_x,
        anchored_y=anchor_y,
        anchored_z=anchor_z,
    )
    emit(ctx, {"device_id": device_id, "result": out or "no fields"})


@devices.command("delete")
@click.argument("device_id")
@click.confirmation_option(prompt="Really delete this tracked device?")
@click.pass_context
def devices_delete(ctx, device_id):
    client = make_client(ctx)
    devices_core.delete_device(client, device_id)
    emit(ctx, {"device_id": device_id, "deleted": True})


# ── devices: the config.yaml registry (durable) vs the live view above ───────
#
# `devices set` edits the companion's runtime store; `devices update-in-config`
# edits config.yaml. Same distinction as `nodes delete` vs
# `nodes remove-from-config` — retiring a beacon for good needs both.


@devices.command("list-in-config")
@config_file_option
@click.pass_context
def devices_list_in_config(ctx, config_file):
    """List the `devices:` block of config.yaml (the durable tracked list)."""
    source = make_config_source(ctx, config_file)
    _, parsed = source.fetch()
    emit(ctx, config_devices_core.list_devices(parsed))


@devices.command("show-in-config")
@click.argument("device_id")
@config_file_option
@click.pass_context
def devices_show_in_config(ctx, device_id, config_file):
    """Show one configured device by id."""
    source = make_config_source(ctx, config_file)
    _, parsed = source.fetch()
    try:
        emit(ctx, config_devices_core.get(parsed, device_id))
    except KeyError as exc:
        _abort(str(exc))


@devices.command("add-to-config")
@click.argument("device_id")
@click.option("--name", default=None, help="Friendly name the companion reports")
@click.option(
    "--rssi-at-1m",
    default=None,
    type=float,
    help="Reference RSSI at 1 m for this beacon (written as `rssi@1m:`)",
)
@click.option("--restart/--no-restart", default=False)
@click.option("--dry-run", is_flag=True)
@config_file_option
@click.pass_context
def devices_add_to_config(ctx, device_id, name, rssi_at_1m, restart, dry_run, config_file):
    """Add a tracked device to config.yaml.

    Example:
      devices add-to-config 'irk:abc123' --name Phone --rssi-at-1m -65
    """
    source = make_config_source(ctx, config_file)
    _, parsed = source.fetch()
    try:
        out = config_devices_core.add(parsed, device_id, name=name, rssi_at_1m=rssi_at_1m)
    except config_devices_core.DeviceConfigError as exc:
        _abort(str(exc))
        return
    out["dry_run"] = dry_run
    if not dry_run:
        out["pushed"] = source.push(parsed, restart=restart)
    emit(ctx, out)


@devices.command("update-in-config")
@click.argument("device_id")
@click.option("--name", default=None, help="New friendly name")
@click.option("--clear-name", is_flag=True, help="Remove the device's `name:`")
@click.option("--rssi-at-1m", default=None, type=float, help="New reference RSSI at 1 m")
@click.option("--clear-rssi", is_flag=True, help="Remove the device's `rssi@1m:`")
@click.option("--new-id", default=None, help="Rewrite the device's `id:`")
@click.option("--restart/--no-restart", default=False)
@click.option("--dry-run", is_flag=True)
@config_file_option
@click.pass_context
def devices_update_in_config(
    ctx, device_id, name, clear_name, rssi_at_1m, clear_rssi, new_id, restart, dry_run, config_file
):
    """Edit a configured device's name / reference RSSI / id."""
    if clear_name and name is not None:
        _abort("pass either --name or --clear-name, not both")
    if clear_rssi and rssi_at_1m is not None:
        _abort("pass either --rssi-at-1m or --clear-rssi, not both")
    if not any([clear_name, clear_rssi, name is not None, rssi_at_1m is not None, new_id]):
        _abort("nothing to change: pass --name/--rssi-at-1m/--new-id or a --clear-* flag")
    source = make_config_source(ctx, config_file)
    _, parsed = source.fetch()
    kwargs: dict = {}
    if clear_name:
        kwargs["name"] = None
    elif name is not None:
        kwargs["name"] = name
    if clear_rssi:
        kwargs["rssi_at_1m"] = None
    elif rssi_at_1m is not None:
        kwargs["rssi_at_1m"] = rssi_at_1m
    try:
        out = config_devices_core.update(parsed, device_id, new_id=new_id, **kwargs)
    except config_devices_core.DeviceConfigError as exc:
        _abort(str(exc))
        return
    if not out["found"]:
        _abort(f"no device with id={device_id!r} in config.yaml")
    out["dry_run"] = dry_run
    if not dry_run and out["changed"]:
        out["pushed"] = source.push(parsed, restart=restart)
    emit(ctx, out)


@devices.command("remove-from-config")
@click.argument("device_id")
@click.option("--restart/--no-restart", default=False)
@click.option("--dry-run", is_flag=True)
@config_file_option
@click.pass_context
def devices_remove_from_config(ctx, device_id, restart, dry_run, config_file):
    """Remove a device from the config.yaml `devices:` block.

    This does not delete the companion's runtime record — use
    `devices delete` for that.
    """
    source = make_config_source(ctx, config_file)
    _, parsed = source.fetch()
    out = config_devices_core.remove(parsed, device_id)
    out["dry_run"] = dry_run
    if not out["removed"]:
        emit(ctx, out)
        sys.exit(1)
    if not dry_run:
        out["pushed"] = source.push(parsed, restart=restart)
    emit(ctx, out)


# ──────────────────────────────────────────────────────── settings (tuning)


@cli.group()
def settings():
    """Tuning knobs in config.yaml: timeouts, mqtt, gps, locators, optimizers.

    Dotted paths address any key the running companion version understands,
    so this keeps working across schema changes. Structural blocks
    (floors/rooms/nodes/devices) are deliberately out of scope — they have
    their own commands, which keep cross-references consistent.
    """


@settings.command("show")
@click.option("--section", default=None, help="Only this top-level section, e.g. mqtt")
@click.option("--reveal", is_flag=True, help="Do not redact passwords/tokens")
@config_file_option
@click.pass_context
def settings_show(ctx, section, reveal, config_file):
    """Show the behaviour half of config.yaml (secrets redacted by default)."""
    source = make_config_source(ctx, config_file)
    _, parsed = source.fetch()
    try:
        emit(ctx, settings_core.summary(parsed, section=section, reveal=reveal))
    except (KeyError, settings_core.SettingsError) as exc:
        _abort(str(exc))


@settings.command("get")
@click.argument("path")
@click.option("--reveal", is_flag=True, help="Do not redact a secret value")
@config_file_option
@click.pass_context
def settings_get(ctx, path, reveal, config_file):
    """Read one dotted path, e.g. settings get locators.nelder_mead.enabled."""
    source = make_config_source(ctx, config_file)
    _, parsed = source.fetch()
    try:
        out = settings_core.get_path(parsed, path, reveal=reveal)
    except settings_core.SettingsError as exc:
        _abort(str(exc))
        return
    emit(ctx, out)
    if not out["found"]:
        sys.exit(1)


@settings.command("set")
@click.argument("path")
@click.argument("value")
@click.option(
    "--type",
    "value_type",
    type=click.Choice(["auto", "str", "int", "float", "bool", "json"]),
    default="auto",
    help="How to read VALUE (default: auto-detect bool/null/number/JSON)",
)
@click.option("--restart/--no-restart", default=False)
@click.option("--dry-run", is_flag=True)
@config_file_option
@click.pass_context
def settings_set(ctx, path, value, value_type, restart, dry_run, config_file):
    """Set one dotted path, creating parent mappings as needed.

    Example:
      settings set away_timeout 300
      settings set locators.nelder_mead.enabled false
    """
    source = make_config_source(ctx, config_file)
    _, parsed = source.fetch()
    try:
        out = settings_core.set_path(parsed, path, value, kind=value_type)
    except settings_core.SettingsError as exc:
        _abort(str(exc))
        return
    out["dry_run"] = dry_run
    if not dry_run:
        out["pushed"] = source.push(parsed, restart=restart)
    emit(ctx, out)


@settings.command("unset")
@click.argument("path")
@click.option("--restart/--no-restart", default=False)
@click.option("--dry-run", is_flag=True)
@config_file_option
@click.pass_context
def settings_unset(ctx, path, restart, dry_run, config_file):
    """Delete one dotted path so the companion falls back to its default."""
    source = make_config_source(ctx, config_file)
    _, parsed = source.fetch()
    try:
        out = settings_core.unset_path(parsed, path)
    except settings_core.SettingsError as exc:
        _abort(str(exc))
        return
    out["dry_run"] = dry_run
    if not out["removed"]:
        emit(ctx, out)
        sys.exit(1)
    if not dry_run:
        out["pushed"] = source.push(parsed, restart=restart)
    emit(ctx, out)


def _toggle_section(parsed, *candidates: str) -> str:
    section = settings_core.resolve_section(parsed, *candidates)
    if section is None:
        _abort(f"config has no {' / '.join(candidates)} section")
    return section  # type: ignore[return-value]


def _toggle_list(ctx, parsed, *candidates: str) -> None:
    emit(ctx, settings_core.list_toggles(parsed, _toggle_section(parsed, *candidates)))


def _toggle_apply(ctx, source, parsed, state, name, restart, dry_run, *candidates: str) -> None:
    """Shared `<thing> NAME on|off|status` body for locators and optimizers."""
    section = _toggle_section(parsed, *candidates)
    if state == "status":
        for row in settings_core.list_toggles(parsed, section):
            if row["name"] == name:
                emit(ctx, row)
                return
        _abort(f"config has no {section}.{name}")
        return
    try:
        out = settings_core.set_toggle(parsed, section, name, state == "on")
    except KeyError as exc:
        _abort(str(exc))
        return
    out["dry_run"] = dry_run
    if not dry_run:
        out["pushed"] = source.push(parsed, restart=restart)
    emit(ctx, out)


@settings.command("locators")
@config_file_option
@click.pass_context
def settings_locators(ctx, config_file):
    """List localisation algorithms and whether each is enabled."""
    source = make_config_source(ctx, config_file)
    _, parsed = source.fetch()
    _toggle_list(ctx, parsed, "locators")


@settings.command("locator")
@click.argument("name")
@click.argument("state", type=click.Choice(["on", "off", "status"]))
@click.option("--restart/--no-restart", default=False)
@click.option("--dry-run", is_flag=True)
@config_file_option
@click.pass_context
def settings_locator(ctx, name, state, restart, dry_run, config_file):
    """Turn one locator on/off, e.g. settings locator nelder_mead off."""
    source = make_config_source(ctx, config_file)
    _, parsed = source.fetch()
    _toggle_apply(ctx, source, parsed, state, name, restart, dry_run, "locators")


@settings.command("optimizers")
@config_file_option
@click.pass_context
def settings_optimizers(ctx, config_file):
    """List the auto-calibration optimizers declared in config.yaml."""
    source = make_config_source(ctx, config_file)
    _, parsed = source.fetch()
    _toggle_list(ctx, parsed, "optimizers", "optimization")


@settings.command("optimizer")
@click.argument("name")
@click.argument("state", type=click.Choice(["on", "off", "status"]))
@click.option("--restart/--no-restart", default=False)
@click.option("--dry-run", is_flag=True)
@config_file_option
@click.pass_context
def settings_optimizer(ctx, name, state, restart, dry_run, config_file):
    """Turn one optimizer on/off, e.g. settings optimizer absorption off."""
    source = make_config_source(ctx, config_file)
    _, parsed = source.fetch()
    _toggle_apply(ctx, source, parsed, state, name, restart, dry_run, "optimizers", "optimization")


# ──────────────────────────────────────────────────────── calibration


@cli.group()
def calibration():
    """Calibration matrix + autocalibration controls."""


@calibration.command("get")
@click.pass_context
def calibration_get(ctx):
    client = make_client(ctx)
    emit(ctx, calibration_core.get(client))


@calibration.command("summary")
@click.pass_context
def calibration_summary(ctx):
    client = make_client(ctx)
    emit(ctx, calibration_core.summary(client))


@calibration.command("reset")
@click.confirmation_option(prompt="Reset ALL calibration (TxRefRssi/RxAdjRssi/Absorption -> 0)?")
@click.pass_context
def calibration_reset(ctx):
    client = make_client(ctx)
    emit(ctx, calibration_core.reset(client))


@calibration.command("auto-optimize")
@click.argument("state", type=click.Choice(["on", "off", "status"]))
@click.pass_context
def calibration_auto(ctx, state):
    client = make_client(ctx)
    if state == "status":
        emit(ctx, calibration_core.auto_optimize_get(client))
    else:
        emit(ctx, calibration_core.auto_optimize_set(client, state == "on"))


# ──────────────────────────────────────────────────────── history


@cli.group()
def history():
    """Device-position history."""


@history.command("get")
@click.argument("device_id")
@click.option("--start", default=None, help="UTC start (ISO-8601), optional")
@click.option("--end", default=None, help="UTC end (ISO-8601), optional")
@click.option("--limit", default=None, type=int, help="Show last N points only")
@click.pass_context
def history_get(ctx, device_id, start, end, limit):
    client = make_client(ctx)
    rows = history_core.get_history(client, device_id, start=start, end=end)
    if limit:
        rows = rows[-limit:]
    emit(ctx, rows)


# ──────────────────────────────────────────────────────── mqtt


@cli.group()
def mqtt():
    """Direct MQTT pub/sub against the broker (bypasses companion + node HTTP)."""


def _mqtt_args(ctx) -> dict:
    obj = ctx.obj
    if not obj.get("mqtt_host"):
        _abort(
            "no MQTT broker configured. Set it with:\n"
            "  cli-anything-espresense config-set mqtt_host=10.32.100.5"
        )
    return {
        "host": obj["mqtt_host"],
        "port": obj.get("mqtt_port", 1883),
        "username": obj.get("mqtt_username"),
        "password": obj.get("mqtt_password"),
    }


@mqtt.command("set-node")
@click.argument("node_id")
@click.argument("key")
@click.argument("value")
@click.option(
    "--retain/--no-retain", default=True, help="Retain the message on the broker (default: yes)"
)
@click.option("--prefix", default=None, help="Topic prefix (default: espresense)")
@click.pass_context
def mqtt_set_node(ctx, node_id, key, value, retain, prefix):
    """Publish a per-node setting: espresense/rooms/<id>/<key>/set"""
    kw = _mqtt_args(ctx)
    out = mqtt_core.publish_setting(
        node_id=node_id,
        key=key,
        value=value,
        prefix=prefix or ctx.obj.get("mqtt_topic_prefix", "espresense"),
        retain=retain,
        **kw,
    )
    emit(ctx, out)


@mqtt.command("pub")
@click.argument("topic")
@click.argument("payload")
@click.option("--retain", is_flag=True)
@click.pass_context
def mqtt_pub(ctx, topic, payload, retain):
    """Publish a raw topic/payload (use to set arbitrary settings)."""
    kw = _mqtt_args(ctx)
    out = mqtt_core.publish_raw(topic=topic, payload=payload, retain=retain, **kw)
    emit(ctx, out)


@mqtt.command("set-device")
@click.argument("device_id")
@click.argument("config_json")
@click.option(
    "--retain/--no-retain", default=True, help="Retain the message on the broker (default: yes)"
)
@click.option("--prefix", default=None, help="Topic prefix (default: espresense)")
@click.pass_context
def mqtt_set_device(ctx, device_id, config_json, retain, prefix):
    """Publish a tracked-device fingerprint: espresense/settings/<id>/config

    The device-side counterpart to `mqtt set-node`. Retained by default so
    nodes that are offline right now still pick it up on reconnect.

    Example:
      mqtt set-device apple:1005:9-12 '{"name":"Jon Watch","rssi@1m":-59}'
    """
    try:
        payload = json.loads(config_json)
    except json.JSONDecodeError as e:
        _abort(f"config_json is not valid JSON: {e}")
        return
    kw = _mqtt_args(ctx)
    try:
        out = mqtt_core.publish_device_config(
            device_id=device_id,
            config=payload,
            prefix=prefix or ctx.obj.get("mqtt_topic_prefix", "espresense"),
            retain=retain,
            **kw,
        )
    except mqtt_core.MqttError as exc:
        _abort(str(exc))
        return
    emit(ctx, out)


@mqtt.command("set-global")
@click.argument("key")
@click.argument("value")
@click.option(
    "--retain/--no-retain", default=True, help="Retain the message on the broker (default: yes)"
)
@click.option("--prefix", default=None, help="Topic prefix (default: espresense)")
@click.pass_context
def mqtt_set_global(ctx, key, value, retain, prefix):
    """Publish a global setting: espresense/settings/<key>/set

    The broker-side twin of `companion settings-set` — works when the
    companion's REST API is unreachable, and the retained message is
    re-applied at startup. Keys are shared with `companion settings-keys`.

    Example:
      mqtt set-global expiration 300
      mqtt set-global telemetry true
      mqtt set-global gps '{"lat":51.5,"lng":-0.1,"elev":30}'
    """
    kw = _mqtt_args(ctx)
    try:
        out = mqtt_core.publish_global_setting(
            key=key,
            value=value,
            prefix=prefix or ctx.obj.get("mqtt_topic_prefix", "espresense"),
            retain=retain,
            **kw,
        )
    except mqtt_core.MqttError as exc:
        _abort(str(exc))
        return
    emit(ctx, out)


@mqtt.command("watch")
@click.argument("topic_filter")
@click.option(
    "--duration", default=None, type=float, help="Seconds to listen (default: until Ctrl-C)"
)
@click.pass_context
def mqtt_watch(ctx, topic_filter, duration):
    """Subscribe to a topic pattern and print/collect messages.

    Example: mqtt watch 'espresense/rooms/+/telemetry'
    """
    kw = _mqtt_args(ctx)
    if ctx.obj.get("as_json"):
        records = mqtt_core.watch(topic_filter=topic_filter, duration=duration, **kw)
        emit(ctx, records)
        return

    def _print(topic, payload):
        click.echo(f"{topic}\t{payload}")

    mqtt_core.watch(topic_filter=topic_filter, duration=duration, callback=_print, **kw)


@mqtt.command("distances")
@click.option("--device", "device_id", default=None, help="Only this device id")
@click.option("--node", "node_id", default=None, help="Only this node id")
@click.option("--duration", default=10.0, type=float, help="Seconds to listen (default: 10)")
@click.option("--prefix", default=None, help="Topic prefix (default: espresense)")
@click.pass_context
def mqtt_distances(ctx, device_id, node_id, duration, prefix):
    """Snapshot which nodes see which devices, at what distance.

    Subscribes to <prefix>/rooms/+/devices/+ for --duration seconds and
    aggregates the raw distance messages: per device and node, the most
    recent distance plus min/max/sample count over the window, with the
    closest node flagged. `mqtt watch` gives you the firehose; this gives
    the table.

    Example:
      mqtt distances --device apple:1005:9-12 --duration 5
    """
    kw = _mqtt_args(ctx)
    out = telemetry_core.distance_snapshot(
        duration=duration,
        prefix=prefix or ctx.obj.get("mqtt_topic_prefix", "espresense"),
        device_id=device_id,
        node_id=node_id,
        **kw,
    )
    if ctx.obj.get("as_json"):
        emit(ctx, out)
        return
    emit(ctx, telemetry_core.distance_rows(out))


@mqtt.command("node-status")
@click.option("--duration", default=5.0, type=float, help="Seconds to listen (default: 5)")
@click.option("--prefix", default=None, help="Topic prefix (default: espresense)")
@click.pass_context
def mqtt_node_status(ctx, duration, prefix):
    """Which nodes report online/offline on <prefix>/rooms/+/status.

    Nodes retain their status message, so even a short listen reports every
    node that has published since its last boot — not just currently
    chatty ones.
    """
    kw = _mqtt_args(ctx)
    out = telemetry_core.status_snapshot(
        duration=duration,
        prefix=prefix or ctx.obj.get("mqtt_topic_prefix", "espresense"),
        **kw,
    )
    if ctx.obj.get("as_json"):
        emit(ctx, out)
        return
    click.echo(f"online:  {', '.join(out['online']) or '-'}")
    click.echo(f"offline: {', '.join(out['offline']) or '-'}")


# ──────────────────────────────────────────────────────── REPL


@cli.command()
@click.pass_context
def repl(ctx):
    """Start an interactive shell."""
    try:
        from cli_anything.espresense.utils.repl_skin import ReplSkin
        from prompt_toolkit.history import InMemoryHistory
    except ImportError:
        click.echo("REPL requires prompt-toolkit. pip install prompt-toolkit", err=True)
        return
    skin = ReplSkin("espresense", version="0.1.0")
    skin.print_banner()
    pt_session = skin.create_prompt_session()
    while True:
        try:
            line = skin.get_input(pt_session)
        except (EOFError, KeyboardInterrupt):
            skin.print_goodbye()
            break
        line = (line or "").strip()
        if not line:
            continue
        if line in ("exit", "quit"):
            skin.print_goodbye()
            break
        if line == "help":
            skin.help(cli.commands)
            continue
        import shlex

        argv = shlex.split(line)
        try:
            cli.main(args=argv, standalone_mode=False, prog_name="(espresense)")
        except SystemExit:
            pass
        except Exception as exc:
            skin.error(str(exc))


# ──────────────────────────────────────────────────────── entry


def main():
    cli(obj={})


if __name__ == "__main__":
    main()
