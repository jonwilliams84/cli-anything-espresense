"""cli-anything-espresense — companion + per-node ESPresense control."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Optional

import click

from cli_anything.espresense.core import (
    calibration as calibration_core,
    config_yaml as config_core,
    devices as devices_core,
    history as history_core,
    k8s_backend,
    mqtt as mqtt_core,
    node_direct,
    nodes as nodes_core,
    project,
    rooms as rooms_core,
    stream as stream_core,
)
from cli_anything.espresense.core import companion_api
from cli_anything.espresense.utils.companion_client import CompanionClient, CompanionError

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}


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


# ──────────────────────────────────────────────────────── root

@click.group(context_settings=CONTEXT_SETTINGS, invoke_without_command=True)
@click.option("--base-url", default=None,
              help="Companion HTTP base URL (default http://localhost:8267)")
@click.option("--timeout", default=None, type=int, help="HTTP timeout in seconds (default 30)")
@click.option("--verify-ssl/--no-verify-ssl", default=None,
              help="Verify TLS cert (default: on)")
@click.option("--k8s-namespace", default=None, help="Kubernetes namespace (default espresense)")
@click.option("--k8s-deployment", default=None,
              help="Companion deployment name (default espresense-companion)")
@click.option("--k8s-container", default=None,
              help="Container name inside the pod (default espresense-companion)")
@click.option("--k8s-config-path", default=None,
              help="Path to config.yaml inside the pod (default /config/espresense/config.yaml)")
@click.option("--config", "config_path", default=None, type=click.Path(),
              help="Path to connection profile (default ~/.config/cli-anything-espresense.json)")
@click.option("--json", "as_json", is_flag=True, default=False,
              help="Emit machine-readable JSON output")
@click.pass_context
def cli(ctx, base_url, timeout, verify_ssl, k8s_namespace, k8s_deployment,
        k8s_container, k8s_config_path, config_path, as_json):
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
    """Manage the local connection profile (~/.config/cli-anything-espresense.json)."""


@config.command("show")
@click.pass_context
def config_show(ctx):
    """Print the resolved profile (merged file + env + flags)."""
    safe = {k: v for k, v in ctx.obj.items()
            if k not in ("config_path", "as_json")}
    emit(ctx, safe)


@config.command("save")
@click.pass_context
def config_save(ctx):
    """Write the current resolved profile back to disk."""
    safe = {k: v for k, v in ctx.obj.items()
            if k not in ("config_path", "as_json")}
    out = project.save_config(safe, ctx.obj.get("config_path"))
    emit(ctx, {"saved": str(out)})


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
    emit(ctx, {
        "companion_url": ctx.obj["base_url"],
        "node_count": len(nodes),
        "online": online,
        "offline": len(nodes) - online,
        "device_track_count": len(cfg.get("devices") or []),
        "calibration_r": cal.get("r") if isinstance(cal, dict) else None,
        "calibration_rmse": cal.get("rmse") if isinstance(cal, dict) else None,
        "optimization_enabled": (cfg.get("optimization") or {}).get("enabled"),
    })


@companion.command("config-get")
@click.option("--format", "fmt", default="yaml", type=click.Choice(["yaml", "json"]),
              help="Output format (default yaml from /api/state/config)")
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
@click.option("-o", "--out", type=click.Path(),
              help="Write to file instead of stdout")
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
@click.option("--no-backup", is_flag=True,
              help="Don't leave a timestamped .bak in the pod (default: do)")
@click.option("--restart", is_flag=True,
              help="Rollout-restart the companion deployment after writing")
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
@click.option("--wait/--no-wait", default=True,
              help="Wait for rollout to complete (default: wait)")
@click.option("--timeout", default="120s",
              help="kubectl rollout timeout (default 120s)")
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
@click.option("--type", "types", multiple=True,
              help="Filter to specific event types (repeatable)")
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


# ──────────────────────────────────────────────────────── rooms

@cli.group()
def rooms():
    """List and rename rooms (floor polygons) in the companion config."""


@rooms.command("list")
@click.option("--floor", default=None, help="Restrict to this floor id")
@click.pass_context
def rooms_list(ctx, floor):
    """List rooms across all floors (or one floor)."""
    target = make_k8s_target(ctx)
    _, parsed = config_core.fetch_yaml(target)
    rows = rooms_core.list_rooms(parsed, floor_id=floor)
    emit(ctx, rows)


@rooms.command("rename")
@click.argument("old")
@click.argument("new")
@click.option("--restart/--no-restart", default=False,
              help="Restart the companion afterwards (default: no — review first)")
@click.option("--dry-run", is_flag=True,
              help="Show the proposed edits without writing")
@click.pass_context
def rooms_rename(ctx, old, new, restart, dry_run):
    """Rename ONE room polygon AND all nodes that referenced it.

    Also strips trailing-whitespace bugs on every node's `room:` field as a
    side effect, since those are a common cause of "doesn't match polygon" sync issues.
    """
    target = make_k8s_target(ctx)
    _, parsed = config_core.fetch_yaml(target)
    summary = rooms_core.rename(parsed, old, new)
    summary["dry_run"] = dry_run
    if not dry_run and (summary["rooms_renamed"] > 0
                          or summary["nodes_repointed"] > 0
                          or summary["whitespace_fixes"] > 0):
        push = config_core.push_yaml(target, parsed, restart=restart)
        summary["pushed"] = push
    emit(ctx, summary)


@rooms.command("rotate")
@click.option("--map", "mappings", multiple=True, required=True,
              help="old=new (repeatable). Applied atomically — supports swaps & cycles.")
@click.option("--restart/--no-restart", default=False,
              help="Restart the companion afterwards (default: no)")
@click.option("--dry-run", is_flag=True,
              help="Show the proposed edits without writing")
@click.pass_context
def rooms_rotate(ctx, mappings, restart, dry_run):
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
    target = make_k8s_target(ctx)
    _, parsed = config_core.fetch_yaml(target)
    summary = rooms_core.rotate(parsed, parsed_map)
    summary["dry_run"] = dry_run
    if not dry_run:
        push = config_core.push_yaml(target, parsed, restart=restart)
        summary["pushed"] = push
    emit(ctx, summary)


@rooms.command("repoint-node")
@click.argument("node_name")
@click.argument("room_name")
@click.option("--restart/--no-restart", default=False)
@click.option("--dry-run", is_flag=True)
@click.pass_context
def rooms_repoint(ctx, node_name, room_name, restart, dry_run):
    """Set a single node's `room:` to a specific room (without renaming anything)."""
    target = make_k8s_target(ctx)
    _, parsed = config_core.fetch_yaml(target)
    out = rooms_core.repoint_node(parsed, node_name, room_name)
    out["dry_run"] = dry_run
    if not dry_run and out["found"]:
        push = config_core.push_yaml(target, parsed, restart=restart)
        out["pushed"] = push
    emit(ctx, out)


# ──────────────────────────────────────────────────────── nodes (companion view)

@cli.group()
def nodes():
    """List, rename, configure nodes via the companion (config.yaml + API)."""


@nodes.command("list")
@click.option("--merge-live/--no-merge-live", default=True,
              help="Join config rows with live state from the API (default: yes)")
@click.option("--include-telemetry/--no-include-telemetry", default=True,
              help="Include telemetry when calling the live API (default: yes)")
@click.pass_context
def nodes_list(ctx, merge_live, include_telemetry):
    """List nodes — by default merges config.yaml with live API state."""
    target = make_k8s_target(ctx)
    _, parsed = config_core.fetch_yaml(target)
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
@click.pass_context
def nodes_rename_in_config(ctx, old, new, restart, dry_run):
    """Rename a node's `name:` in config.yaml only (does NOT touch the physical device)."""
    target = make_k8s_target(ctx)
    _, parsed = config_core.fetch_yaml(target)
    out = nodes_core.rename_in_config(parsed, old, new)
    out["dry_run"] = dry_run
    if not dry_run and out["found"]:
        push = config_core.push_yaml(target, parsed, restart=restart)
        out["pushed"] = push
    emit(ctx, out)


@nodes.command("set-point")
@click.argument("name")
@click.argument("x", type=float)
@click.argument("y", type=float)
@click.argument("z", type=float)
@click.option("--restart/--no-restart", default=False)
@click.option("--dry-run", is_flag=True)
@click.pass_context
def nodes_set_point(ctx, name, x, y, z, restart, dry_run):
    """Set a node's 3D point in config.yaml."""
    target = make_k8s_target(ctx)
    _, parsed = config_core.fetch_yaml(target)
    out = nodes_core.set_point(parsed, name, [x, y, z])
    out["dry_run"] = dry_run
    if not dry_run and out["found"]:
        push = config_core.push_yaml(target, parsed, restart=restart)
        out["pushed"] = push
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
@click.option("--section", default="extras",
              type=click.Choice(["main", "extras", "hardware"]),
              help="Settings page: main=wifi/mqtt, extras=BLE, hardware=sensors")
@click.pass_context
def node_settings(ctx, host, section):
    """GET /wifi/<section> on the node — read settings as JSON."""
    emit(ctx, _node_client(ctx, host).get_settings(section))


@node.command("set")
@click.argument("host")
@click.argument("fields", nargs=-1)
@click.option("--section", default="extras",
              type=click.Choice(["main", "extras", "hardware"]))
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
def node_scan_wifi(ctx, host, ):
    emit(ctx, _node_client(ctx, host).scan_wifi())


@node.command("devices")
@click.argument("host")
@click.pass_context
def node_devices(ctx, host):
    """Devices currently seen by this node."""
    info = _node_client(ctx, host).info(show_all=True)
    emit(ctx, info.get("devices") or [])


# ──────────────────────────────────────────────────────── devices (companion view)

@cli.group()
def devices():
    """Tracked-device commands (companion's view of phones/tags/beacons)."""


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
        client, device_id, name=name, ref_rssi=ref_rssi,
        anchored_x=anchor_x, anchored_y=anchor_y, anchored_z=anchor_z,
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
@click.option("--retain/--no-retain", default=True,
              help="Retain the message on the broker (default: yes)")
@click.option("--prefix", default=None, help="Topic prefix (default: espresense)")
@click.pass_context
def mqtt_set_node(ctx, node_id, key, value, retain, prefix):
    """Publish a per-node setting: espresense/rooms/<id>/<key>/set"""
    kw = _mqtt_args(ctx)
    out = mqtt_core.publish_setting(
        node_id=node_id, key=key, value=value,
        prefix=prefix or ctx.obj.get("mqtt_topic_prefix", "espresense"),
        retain=retain, **kw,
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


@mqtt.command("watch")
@click.argument("topic_filter")
@click.option("--duration", default=None, type=float,
              help="Seconds to listen (default: until Ctrl-C)")
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
