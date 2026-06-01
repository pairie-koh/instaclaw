"""Kuri-as-MCP server for instaclaw.

Exposes kuri's browser control + instaclaw's incremental checkpoint tools as an
MCP server (stdio). The codegraff/forge agent (driven via the Python SDK in
agent_scrape.py) calls these tools to scrape Instagram instead of instaclaw
hand-rolling an OpenAI tool-use loop.

Browser tools wrap kuri_client.py. The `screenshot` tool captures a PNG, routes
it through the omnimodal model over the codegraff gateway, and returns a text
description (MCP tool results are text, and it keeps the agent context lean).

The save_* tools checkpoint to out/{stem}.json on every call — same on-disk
contract the rest of the app (analyze.py / server.py / render.py) already reads,
so a dead agent loop still leaves everything collected so far on disk.

Registered in ~/forge/.mcp.json as the "kuri" server (user-level = auto-trusted;
a project-local .mcp.json is rejected by forge's headless trust gate).
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

ROOT = Path(__file__).parent
OUT_DIR = ROOT / "out"
OUT_DIR.mkdir(exist_ok=True)


# ---- env: load instaclaw/.env so CODEGRAFF_API_KEY / KURI_* are available ----
def _load_env() -> None:
    envf = ROOT / ".env"
    if not envf.exists():
        return
    for line in envf.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


_load_env()

# kuri_client lives next to this file.
import sys
sys.path.insert(0, str(ROOT))
from kuri_client import Kuri, KuriError, KuriTab  # noqa: E402

MODEL = os.environ.get("INSTACLAW_MODEL", "mimo-v2.5")
MAX_TOKENS = int(os.environ.get("INSTACLAW_MAX_TOKENS", "24000"))
CODEGRAFF_BASE_URL = os.environ.get("CODEGRAFF_BASE_URL", "https://gateway.codegraff.com/v1")

mcp = FastMCP("kuri")


# ---- lazy singletons ----
_tab: KuriTab | None = None


def _tab_or_connect() -> KuriTab:
    global _tab
    if _tab is None:
        _tab = Kuri().first_tab()
    return _tab


# ---- checkpoint store (stem-keyed; mirrors agent_scrape.CheckpointStore) ----
class _Store:
    def __init__(self, stem: str):
        self.path = OUT_DIR / f"{stem}.json"
        mode = "self" if stem.startswith("self_") else "target"
        handle = stem.split("_", 1)[1] if "_" in stem else stem
        self.state: dict = {
            "mode": mode, "handle": handle, "private": False,
            "header": {"name": "", "header_text": "", "stats_raw": []},
            "grid": [], "reels": [], "tagged": [],
            "highlights": [], "following": [], "mutuals": [],
        }
        if mode == "self":
            self.state["saved"] = []
        self._flush()

    def _flush(self):
        self.path.write_text(json.dumps(self.state, indent=2, ensure_ascii=False), encoding="utf-8")


_stores: dict[str, _Store] = {}


def _store(stem: str) -> _Store:
    if stem not in _stores:
        _stores[stem] = _Store(stem)
    return _stores[stem]


# ---- vision side-call (same shape as the old agent_scrape._describe_screenshot) ----
def _describe_screenshot(png_bytes: bytes, current_url: str) -> str:
    from openai import OpenAI
    b64 = base64.b64encode(png_bytes).decode("ascii")
    client = OpenAI(
        base_url=CODEGRAFF_BASE_URL,
        api_key=os.environ.get("CODEGRAFF_API_KEY") or os.environ.get("CG_API_KEY", ""),
    )
    try:
        resp = client.chat.completions.create(
            model=MODEL, max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": (
                    f"This is a screenshot of {current_url}. List every piece of text "
                    "visible, prioritising: text burned into video frames (overlay "
                    "captions, audio track names, sticker text), text on image posts, "
                    "and any handle / caption / count not in plain DOM. Transcribe "
                    "verbatim. If nothing beyond standard IG chrome, say so."
                )},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ]}],
        )
        return (resp.choices[0].message.content or "").strip() or "(vision returned no description)"
    except Exception as e:  # noqa
        return f"(vision call failed: {type(e).__name__}: {e})"


# ============================ browser tools ============================
@mcp.tool()
def navigate(url: str) -> str:
    """Navigate the current tab to a full https URL."""
    import time
    _tab_or_connect().goto(url)
    time.sleep(2.5)
    return f"navigated to {_tab_or_connect().url()}"


@mcp.tool()
def snap_interactive() -> str:
    """Interactive elements on the page as JSON [{ref, role, name}]. Refs (e0, e1, ...) are stable until the DOM mutates."""
    nodes = _tab_or_connect().snap(interactive_only=True)
    return json.dumps([{"ref": n.ref, "role": n.role, "name": n.name} for n in nodes])


@mcp.tool()
def snap_text() -> str:
    """Full a11y tree as indented text. Use for non-interactive content like captions or comments."""
    return _tab_or_connect().snap_text()


@mcp.tool()
def page_text() -> str:
    """All visible text on the page (heavier than snap_text), capped to 6000 chars."""
    return _tab_or_connect().text()[:6000]


@mcp.tool()
def click(ref: str) -> str:
    """Click an element by its ref from snap_interactive."""
    import time
    _tab_or_connect().click(ref)
    time.sleep(1.2)
    return "clicked"


@mcp.tool()
def type_text(ref: str, text: str, submit: bool = False) -> str:
    """Type text into an input element identified by ref. Set submit=true to press Enter after."""
    import time
    _tab_or_connect().type(ref, text, submit=submit)
    time.sleep(0.6)
    return "typed"


@mcp.tool()
def scroll(dy: int) -> str:
    """Scroll the page by dy pixels (positive = down)."""
    import time
    _tab_or_connect().scroll(int(dy))
    time.sleep(0.8)
    return "scrolled"


@mcp.tool()
def back() -> str:
    """Browser back button."""
    import time
    _tab_or_connect().back()
    time.sleep(1.5)
    return f"back -> {_tab_or_connect().url()}"


@mcp.tool()
def current_url() -> str:
    """Get the current page URL."""
    return _tab_or_connect().url()


@mcp.tool()
def screenshot(full_page: bool = False) -> str:
    """Capture a PNG of the page, route it through the vision model, and return that
    model's text description. Use only when text tools aren't enough (overlay text
    burned into a reel video frame, audio name rendered as visual UI)."""
    tab = _tab_or_connect()
    png = tab.screenshot(full_page=bool(full_page))
    current = tab.url()
    return f"vision description of {current}:\n{_describe_screenshot(png, current)}"


# ============================ checkpoint tools ============================
@mcp.tool()
def save_header(stem: str, name: str, header_text: str, stats_raw: list[str]) -> str:
    """Save the profile header. stem is the scrape id (e.g. 'self_handle' or 'target_handle'). Call once."""
    s = _store(stem)
    s.state["header"] = {"name": name, "header_text": header_text, "stats_raw": list(stats_raw)}
    s._flush()
    return f"header saved for {stem}: name={name!r}"


@mcp.tool()
def mark_private(stem: str) -> str:
    """Mark this account as PRIVATE. Call only when the private wall is shown and content is not visible."""
    s = _store(stem)
    s.state["private"] = True
    s._flush()
    return "marked private"


@mcp.tool()
def save_reel(stem: str, url: str, caption: str, creator: str, audio: str) -> str:
    """Save one repost from the Reposts tab. creator = the ORIGINAL poster (not the profile owner)."""
    s = _store(stem)
    s.state["reels"].append({"url": url, "caption": caption, "creator": creator, "audio": audio})
    s._flush()
    return f"reel #{len(s.state['reels'])} saved: {url}"


@mcp.tool()
def save_highlight(stem: str, title: str, cover_text: str, slides: list[str]) -> str:
    """Save one story highlight bubble after clicking through all its slides."""
    s = _store(stem)
    s.state["highlights"].append({"title": title, "cover_text": cover_text, "slides": list(slides)})
    s._flush()
    return f"highlight #{len(s.state['highlights'])} saved: {title!r} ({len(slides)} slides)"


@mcp.tool()
def save_grid_post(stem: str, url: str, caption: str, comments: list[str], likes_raw: str) -> str:
    """Save one grid post after opening it and reading caption + top comments + likes."""
    s = _store(stem)
    s.state["grid"].append({"url": url, "caption": caption, "comments": list(comments), "likes_raw": likes_raw})
    s._flush()
    return f"grid post #{len(s.state['grid'])} saved: {url}"


@mcp.tool()
def save_tagged(stem: str, url: str) -> str:
    """Save one tagged post URL. TARGET mode only."""
    s = _store(stem)
    s.state["tagged"].append({"url": url})
    s._flush()
    return f"tagged #{len(s.state['tagged'])} saved"


@mcp.tool()
def save_saved(stem: str, url: str) -> str:
    """Save one saved post URL. SELF mode only."""
    s = _store(stem)
    s.state.setdefault("saved", []).append({"url": url})
    s._flush()
    return f"saved #{len(s.state.get('saved') or [])} saved"


if __name__ == "__main__":
    mcp.run()  # stdio transport
