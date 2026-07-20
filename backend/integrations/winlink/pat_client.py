# backend/integrations/winlink/pat_client.py
"""HTTP client seam for PAT's Winlink API. The single place that builds PAT
URLs, injects auth, and maps transport errors. Every consumer and every test
goes through this module — no live radio in CI."""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import AsyncIterator

import httpx


@dataclass
class PatAuth:
    mode: str  # "none" | "basic" | "token"
    username: str = ""
    password: str = ""
    token: str = ""


class PatUnavailable(Exception):
    """PAT could not be reached (down, bad URL, auth rejected, transport error)."""


class PatConnectError(Exception):
    """PAT accepted the request but the radio connect attempt failed."""


def _auth_headers(auth: PatAuth | None) -> dict[str, str]:
    if auth is None or auth.mode == "none":
        return {}
    if auth.mode == "basic":
        raw = f"{auth.username}:{auth.password}".encode()
        return {"Authorization": "Basic " + base64.b64encode(raw).decode()}
    if auth.mode == "token":
        return {"Authorization": f"Bearer {auth.token}"}
    return {}


class PatClient:
    def __init__(self, base_url: str, auth: PatAuth | None = None, timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.auth = auth
        self.timeout = timeout
        self._transport: httpx.BaseTransport | None = None  # test injection seam

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.base_url,
            headers=_auth_headers(self.auth),
            timeout=self.timeout,
            transport=self._transport,
        )

    def _request(self, method: str, path: str, **kw) -> httpx.Response:
        try:
            with self._client() as c:
                resp = c.request(method, path, **kw)
                resp.raise_for_status()
                return resp
        except httpx.HTTPStatusError as exc:
            raise PatUnavailable(f"PAT {method} {path} -> {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise PatUnavailable(f"PAT {method} {path} unreachable: {exc}") from exc

    def status(self) -> dict:
        return self._request("GET", "/api/status").json()

    def connect_aliases(self) -> dict[str, str]:
        return self._request("GET", "/api/config/connect_aliases").json() or {}

    def rmslist(self) -> list[dict]:
        return self._request("GET", "/api/rmslist").json() or []

    def list_mailbox(self, box: str) -> list[dict]:
        return self._request("GET", f"/api/mailbox/{box}").json() or []

    def get_message(self, box: str, mid: str) -> dict:
        return self._request("GET", f"/api/mailbox/{box}/{mid}").json()

    def get_attachment(self, box: str, mid: str, name: str) -> bytes:
        return self._request("GET", f"/api/mailbox/{box}/{mid}/{name}").content

    def post_outbound(self, to: str, subject: str, body: str, cc: list[str],
                      attachments: list[dict]) -> str:
        # Build a single multipart files list so all fields and file parts coexist
        # correctly under httpx 0.28 (passing data= as a list of tuples alongside
        # files= is broken in that version — it causes a TypeError when the mock
        # transport tries to read the stream).
        files: list = [
            ("to", (None, to)),
            ("subject", (None, subject)),
            ("body", (None, body)),
        ]
        files += [("cc", (None, c)) for c in cc]
        files += [
            ("attachment", (a["filename"], a["data"],
                            a.get("content_type", "application/octet-stream")))
            for a in attachments
        ]
        resp = self._request("POST", "/api/mailbox/out", files=files)
        try:
            payload = resp.json()
        except json.JSONDecodeError:
            return ""
        if isinstance(payload, dict):
            return str(payload.get("MID") or payload.get("mid") or "")
        return ""

    def connect(self, connect_url: str) -> bool:
        resp = self._request("GET", "/api/connect", params={"url": connect_url})
        try:
            ok = bool(resp.json().get("success", True))
        except json.JSONDecodeError:
            ok = True
        if not ok:
            raise PatConnectError(f"PAT refused connect: {connect_url}")
        return True

    def disconnect(self) -> None:
        self._request("GET", "/api/disconnect")

    async def stream_status(self) -> AsyncIterator[dict]:
        """Yield PAT status/notification frames from the /ws websocket. Replaced
        wholesale in tests with a canned async iterator."""
        import websockets  # local import: only needed when a session actually runs

        ws_url = self.base_url.replace("http://", "ws://").replace("https://", "wss://") + "/ws"
        headers = _auth_headers(self.auth)
        async with websockets.connect(ws_url, additional_headers=headers) as ws:
            async for raw in ws:
                try:
                    yield json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    continue
