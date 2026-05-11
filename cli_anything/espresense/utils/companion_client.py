"""Thin requests-based client for the ESPresense-companion REST API."""

from __future__ import annotations

import json as _json
from typing import Any, Optional
from urllib.parse import urljoin

import requests


class CompanionError(RuntimeError):
    pass


class CompanionClient:
    """HTTP client for the ESPresense-companion .NET service.

    Default base URL is http://localhost:8267 (matches the upstream Kestrel default).
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8267",
        timeout: int = 30,
        verify_ssl: bool = True,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        self.session = requests.Session()

    # ---------------------------------------------------------------- low-level

    def _url(self, path: str) -> str:
        if path.startswith("http"):
            return path
        if not path.startswith("/"):
            path = "/" + path
        return self.base_url + path

    def request(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        json: Any = None,
        data: Any = None,
        headers: Optional[dict] = None,
        stream: bool = False,
        timeout: Optional[int] = None,
    ) -> requests.Response:
        url = self._url(path)
        try:
            resp = self.session.request(
                method.upper(),
                url,
                params=params,
                json=json,
                data=data,
                headers=headers,
                stream=stream,
                timeout=timeout or self.timeout,
                verify=self.verify_ssl,
            )
        except requests.RequestException as exc:
            raise CompanionError(f"{method} {url} failed: {exc}") from exc
        if resp.status_code >= 400:
            body = resp.text[:500] if resp.text else ""
            raise CompanionError(
                f"{method} {url} -> {resp.status_code}: {body}"
            )
        return resp

    # ---------------------------------------------------------------- convenience

    def get(self, path: str, params: Optional[dict] = None) -> Any:
        resp = self.request("GET", path, params=params)
        return self._parse(resp)

    def post(self, path: str, json: Any = None, data: Any = None) -> Any:
        resp = self.request("POST", path, json=json, data=data)
        return self._parse(resp)

    def put(self, path: str, json: Any = None) -> Any:
        resp = self.request("PUT", path, json=json)
        return self._parse(resp)

    def delete(self, path: str) -> Any:
        resp = self.request("DELETE", path)
        return self._parse(resp)

    @staticmethod
    def _parse(resp: requests.Response) -> Any:
        if not resp.content:
            return None
        ctype = (resp.headers.get("Content-Type") or "").lower()
        if "json" in ctype:
            return resp.json()
        try:
            return resp.json()
        except (ValueError, _json.JSONDecodeError):
            return resp.text
