"""Thin Python wrapper around kuri's HTTP API.

Kuri lives in WSL, listens on http://127.0.0.1:8080 (forwarded to Windows by WSL2).
Auth via Bearer token in KURI_API_TOKEN. Every per-tab op requires `tab_id`.

This module replaces the Playwright surface used by scrape.py / agent_scrape.py /
screenshot.py — it does NOT try to mirror Playwright's full API, only the calls
instaclaw actually makes.
"""
from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from typing import Optional

import requests


class KuriError(RuntimeError):
    pass


@dataclass
class SnapNode:
    ref: str
    role: str
    name: str


class KuriTab:
    """One Chromium tab driven through kuri's HTTP API."""

    def __init__(self, client: "Kuri", tab_id: str):
        self._c = client
        self.tab_id = tab_id

    # ---- navigation ----
    def goto(self, url: str, wait_ms: int = 0) -> None:
        self._c._get("/navigate", url=url)
        if wait_ms:
            self._c._sleep(wait_ms)

    def back(self) -> None:
        self._c._get("/back")

    def reload(self) -> None:
        self._c._get("/reload")

    # ---- state ----
    def url(self) -> str:
        r = self._c._get("/get", type="url")
        return _unwrap_evaluate_value(r) or ""

    def title(self) -> str:
        r = self._c._get("/get", type="title")
        return _unwrap_evaluate_value(r) or ""

    def text(self) -> str:
        """All visible text on the page."""
        r = self._c._get("/text")
        return _unwrap_evaluate_value(r) or ""

    # ---- snapshots ----
    def snap(self, interactive_only: bool = True) -> list[SnapNode]:
        params = {"filter": "interactive"} if interactive_only else {}
        rows = self._c._get("/snapshot", **params)
        if not isinstance(rows, list):
            return []
        return [SnapNode(ref=r.get("ref", ""), role=r.get("role", ""), name=r.get("name", "")) for r in rows]

    def snap_text(self) -> str:
        """Compact a11y snapshot in text form — what kuri-agent feeds to an LLM."""
        r = self._c._get("/snapshot", format="text")
        return r if isinstance(r, str) else str(r)

    # ---- interaction ----
    def click(self, ref: str) -> None:
        self._c._get("/action", action="click", ref=ref)

    def type(self, ref: str, text: str, submit: bool = False) -> None:
        params = {"action": "type", "ref": ref, "text": text}
        if submit:
            params["submit"] = "true"
        self._c._get("/action", **params)

    def press(self, key: str) -> None:
        self._c._get("/action", action="press", key=key)

    def scroll(self, dy: int) -> None:
        """Wheel-scroll by `dy` pixels. Done via JS — kuri's /scroll signature varies."""
        self.evaluate(f"window.scrollBy(0, {int(dy)})")

    # ---- JS ----
    def evaluate(self, expression: str) -> object:
        r = self._c._get("/evaluate", expression=expression)
        return _unwrap_evaluate_value(r)

    # ---- attributes ----
    def attr(self, ref: str, name: str) -> Optional[str]:
        r = self._c._get("/get", type="attr", ref=ref, name=name)
        v = _unwrap_evaluate_value(r)
        return v if isinstance(v, str) else None

    def get_html(self, ref: Optional[str] = None) -> str:
        params = {"type": "html"}
        if ref:
            params["ref"] = ref
        r = self._c._get("/get", **params)
        return _unwrap_evaluate_value(r) or ""

    def count(self, selector: str) -> int:
        """Count elements matching a CSS selector — via JS since kuri's ref model is a11y-based."""
        n = self.evaluate(f"document.querySelectorAll({_js_str(selector)}).length")
        try:
            return int(n)
        except (TypeError, ValueError):
            return 0

    # ---- screenshots ----
    def screenshot(self, full_page: bool = False) -> bytes:
        params = {"full": "true"} if full_page else {}
        r = self._c._get("/screenshot", **params)
        # Response shape: {"id":N,"result":{"data":"<base64-png>"}}
        if isinstance(r, dict):
            data = (r.get("result") or {}).get("data") or r.get("data") or ""
            if data:
                return base64.b64decode(data)
        raise KuriError(f"unexpected screenshot response: {str(r)[:200]}")


class Kuri:
    """Top-level kuri client. One instance per process.

    Usage:
        k = Kuri()
        tab = k.first_tab()
        tab.goto("https://example.com")
        nodes = tab.snap()
    """

    def __init__(self, base_url: Optional[str] = None, token: Optional[str] = None, timeout: float = 30.0):
        self.base_url = (base_url or os.environ.get("KURI_BASE_URL") or "http://127.0.0.1:8080").rstrip("/")
        self.token = token or os.environ.get("KURI_API_TOKEN") or ""
        self.timeout = timeout
        self._tab_id: Optional[str] = None

    def _headers(self) -> dict:
        h = {"Accept": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def _get(self, path: str, _retries: int = 2, **params) -> object:
        if self._tab_id and "tab_id" not in params:
            params["tab_id"] = self._tab_id
        last_err: Optional[Exception] = None
        for attempt in range(_retries + 1):
            try:
                resp = requests.get(self.base_url + path, params=params,
                                    headers=self._headers(), timeout=self.timeout)
            except requests.RequestException as e:
                last_err = e
                if attempt < _retries:
                    import time as _t
                    _t.sleep(1.5)
                    continue
                raise KuriError(f"kuri request failed: {path}: {e}") from e
            # Retry on the 502 "CDP command failed" transient that kuri returns
            # while it auto-recovers the CDP session after a Chrome renderer swap.
            # This is the fix from kuri#172 — the first request after Chrome
            # detaches surfaces the dead session; the next one succeeds because
            # kuri has rebuilt the CdpClient under the hood.
            if resp.status_code == 502 and "CDP command failed" in resp.text and attempt < _retries:
                import time as _t
                _t.sleep(1.5)
                continue
            if resp.status_code >= 400:
                raise KuriError(f"kuri {path} -> HTTP {resp.status_code}: {resp.text[:300]}")
            ctype = resp.headers.get("content-type", "")
            if "application/json" in ctype:
                try:
                    return resp.json()
                except ValueError:
                    return resp.text
            return resp.text
        # Should be unreachable, but satisfy type checkers.
        raise KuriError(f"kuri request failed: {path}: {last_err}") from last_err

    def _sleep(self, ms: int) -> None:
        import time as _t
        _t.sleep(ms / 1000.0)

    def health(self) -> dict:
        r = self._get("/health")
        return r if isinstance(r, dict) else {"raw": r}

    def list_tabs(self) -> list[dict]:
        r = self._get("/tabs")
        return r if isinstance(r, list) else []

    def first_tab(self) -> KuriTab:
        tabs = self.list_tabs()
        if not tabs:
            raise KuriError("no tabs available — is kuri running with a managed Chrome?")
        self._tab_id = tabs[0]["id"]
        return KuriTab(self, self._tab_id)

    def tab(self, tab_id: str) -> KuriTab:
        self._tab_id = tab_id
        return KuriTab(self, tab_id)


# ---- helpers ----

def _unwrap_evaluate_value(r: object) -> object:
    """Kuri returns CDP-shaped JSON for /evaluate, /get and /text:
        {"id": N, "result": {"result": {"type": "string", "value": "..."}}}
    Flatten to the inner value. Returns the raw object unchanged if shape differs.
    """
    if isinstance(r, dict):
        result = r.get("result")
        if isinstance(result, dict):
            inner = result.get("result")
            if isinstance(inner, dict) and "value" in inner:
                return inner["value"]
            if "value" in result:
                return result["value"]
        if "value" in r:
            return r["value"]
    return r


def _js_str(s: str) -> str:
    """Quote a string for safe embedding in a JS literal."""
    import json as _json
    return _json.dumps(s)
