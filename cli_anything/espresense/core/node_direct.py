"""HTTP client for an individual ESPresense ESP32 node.

These endpoints come from the firmware's web server (HttpWebServer + the
HeadlessWiFiSettings library):

  GET  /json                       — status + room + version + flavor
  POST /json/configs               — add/update a device-config entry
  DELETE /json/configs?id=<id>     — delete one
  POST /restart                    — reboot
  POST /reboot                     — alias
  GET  /wifi/main                  — wifi + mqtt settings (form/json)
  POST /wifi/main                  — write same (form-encoded)
  GET  /wifi/extras                — BLE settings (absorption, tx_ref_rssi, …)
  POST /wifi/extras                — write same
  GET  /wifi/hardware              — sensor settings
  POST /wifi/hardware              — write same
  GET  /wifi/scan                  — list visible APs

Settings forms always trigger a node restart after POST, so callers should
expect the node to drop offline briefly.

`rename(host, new_name)` is the convenience for the most common chore: it
PUTs a new `room` setting and triggers the restart.
"""

from __future__ import annotations

from typing import Any, Optional

import requests


class NodeError(RuntimeError):
    pass


class NodeClient:
    def __init__(self, host: str, *, port: int = 80, timeout: int = 10,
                 scheme: str = "http") -> None:
        host = host.strip()
        if host.startswith("http://") or host.startswith("https://"):
            self.base_url = host.rstrip("/")
        else:
            self.base_url = f"{scheme}://{host}:{port}"
        self.timeout = timeout
        self.session = requests.Session()

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return self.base_url + path

    def _request(self, method: str, path: str, **kw) -> requests.Response:
        kw.setdefault("timeout", self.timeout)
        url = self._url(path)
        try:
            resp = self.session.request(method, url, **kw)
        except requests.RequestException as exc:
            raise NodeError(f"{method} {url} failed: {exc}") from exc
        return resp

    # ── status ──────────────────────────────────────────────────────────────

    def info(self, show_all: bool = False) -> dict:
        params = {"showAll": "1"} if show_all else None
        resp = self._request("GET", "/json", params=params)
        if resp.status_code >= 400:
            raise NodeError(f"GET /json -> {resp.status_code}: {resp.text[:200]}")
        try:
            return resp.json()
        except ValueError:
            return {"raw": resp.text, "content_type": resp.headers.get("Content-Type")}

    # ── lifecycle ───────────────────────────────────────────────────────────

    def restart(self) -> bool:
        resp = self._request("POST", "/restart")
        return resp.status_code < 400

    def reboot(self) -> bool:
        # alias on the firmware side; provided here for callers who prefer the verb
        resp = self._request("POST", "/reboot")
        return resp.status_code < 400

    # ── settings (HeadlessWiFiSettings pages) ───────────────────────────────

    def get_settings(self, section: str = "main") -> dict:
        section = section.strip("/")
        if section not in ("main", "extras", "hardware"):
            raise ValueError("section must be one of: main, extras, hardware")
        resp = self._request("GET", f"/wifi/{section}")
        if resp.status_code >= 400:
            raise NodeError(
                f"GET /wifi/{section} -> {resp.status_code}: {resp.text[:200]}"
            )
        try:
            return resp.json()
        except ValueError:
            return {"raw": resp.text}

    def put_settings(self, section: str, fields: dict[str, Any]) -> dict:
        """Push form-encoded settings into /wifi/{section} and report status.

        The firmware restarts after each successful POST.
        """
        section = section.strip("/")
        if section not in ("main", "extras", "hardware"):
            raise ValueError("section must be one of: main, extras, hardware")
        data = {k: ("" if v is None else str(v)) for k, v in fields.items()}
        resp = self._request("POST", f"/wifi/{section}", data=data)
        if resp.status_code >= 400:
            raise NodeError(
                f"POST /wifi/{section} -> {resp.status_code}: {resp.text[:200]}"
            )
        return {"status": resp.status_code, "body": resp.text[:500]}

    # ── device-config CRUD ──────────────────────────────────────────────────

    def list_device_configs(self) -> list[dict]:
        info = self.info(show_all=True)
        return info.get("configs") or []

    def upsert_device_config(self, device_id: str, *, alias: Optional[str] = None,
                              name: Optional[str] = None,
                              rssi_at_1m: Optional[int] = None) -> dict:
        payload: dict[str, Any] = {"id": device_id}
        if alias is not None:
            payload["alias"] = alias
        if name is not None:
            payload["name"] = name
        if rssi_at_1m is not None:
            payload["rssi@1m"] = rssi_at_1m
        resp = self._request("POST", "/json/configs", json=payload)
        if resp.status_code >= 400:
            raise NodeError(
                f"POST /json/configs -> {resp.status_code}: {resp.text[:200]}"
            )
        try:
            return resp.json()
        except ValueError:
            return {"raw": resp.text}

    def delete_device_config(self, device_id: str) -> bool:
        resp = self._request("DELETE", "/json/configs", params={"id": device_id})
        return resp.status_code < 400

    # ── high-level conveniences ─────────────────────────────────────────────

    def rename(self, new_name: str) -> dict:
        """Rename this node (room = MQTT node-id) and restart.

        The firmware re-derives its hostname (`espresense-<kebab>`) on next
        boot, so the node may take ~30-60s to reappear on the network.
        """
        if not new_name or "/" in new_name:
            raise ValueError("new_name must be non-empty and not contain '/'")
        # `room` is what the firmware reads in HeadlessWiFiSettings; setting it
        # via /wifi/main is the same path the SvelteKit UI uses.
        resp = self.put_settings("main", {"room": new_name})
        self.restart()
        return {"new_name": new_name, "post_status": resp.get("status")}

    def scan_wifi(self) -> list[dict]:
        resp = self._request("GET", "/wifi/scan")
        if resp.status_code >= 400:
            raise NodeError(
                f"GET /wifi/scan -> {resp.status_code}: {resp.text[:200]}"
            )
        try:
            data = resp.json()
        except ValueError:
            return [{"raw": resp.text}]
        return data if isinstance(data, list) else [data]
