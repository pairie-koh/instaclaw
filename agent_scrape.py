"""Kuri-driven Instagram scraper with incremental checkpointing.

Each piece of data the agent extracts is written to disk through a dedicated
save_* tool action, so if the loop dies (LLM timeout, context bloat, exception)
the partial file on disk still has everything collected so far. Public surface
(ScrapeResult, CheckpointStore, scrape_self/target/focused_scrape, PROFILE_DIR)
is preserved so analyze.py / server.py / render.py work unchanged.

Engine: kuri (https://github.com/justrach/kuri) v0.4.5+ drives Chrome over CDP
via its HTTP API; Claude (Sonnet 4.6) issues tool calls in a loop. Kuri's
v0.4.5 auto-recovers the CDP session after Chrome's renderer swap on Instagram
(see kuri#172) so the loop stays alive across navigations.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Optional

from anthropic import Anthropic
from pydantic import BaseModel, Field

from kuri_client import Kuri, KuriError, KuriTab

# Narrate callback signature used by the web layer: (step, thinking, next_goal).
NarrateCb = Optional[Callable[[int, str, str], None]]

ROOT = Path(__file__).parent
PROFILE_DIR = ROOT / ".chrome-profile"
OUT_DIR = ROOT / "out"
OUT_DIR.mkdir(exist_ok=True)
MODEL = os.environ.get("INSTACLAW_MODEL", "claude-sonnet-4-6")
LLM_TIMEOUT_S = int(os.environ.get("INSTACLAW_LLM_TIMEOUT", "180"))
MAX_TURNS = int(os.environ.get("INSTACLAW_MAX_TURNS", "60"))


# ---------- output schema (matches the prior browser-use version) ----------
class Header(BaseModel):
    name: str = ""
    header_text: str = ""
    stats_raw: list[str] = Field(default_factory=list)


class Post(BaseModel):
    url: str = ""
    caption: str = ""
    comments: list[str] = Field(default_factory=list)
    likes_raw: str = ""


class Reel(BaseModel):
    url: str = ""
    caption: str = ""
    creator: str = ""
    audio: str = ""


class UrlOnly(BaseModel):
    url: str = ""


class Highlight(BaseModel):
    title: str = ""
    cover_text: str = ""
    slides: list[str] = Field(default_factory=list)


class Follow(BaseModel):
    handle: str = ""
    display_name: str = ""


class ScrapeResult(BaseModel):
    mode: str
    handle: str
    private: bool = False
    header: Header = Field(default_factory=Header)
    grid: list[Post] = Field(default_factory=list)
    reels: list[Reel] = Field(default_factory=list)
    tagged: list[UrlOnly] = Field(default_factory=list)
    highlights: list[Highlight] = Field(default_factory=list)
    following: list[Follow] = Field(default_factory=list)
    mutuals: list[Follow] = Field(default_factory=list)
    saved: Optional[list[UrlOnly]] = None


# ---------- checkpoint store ----------
class CheckpointStore:
    """Holds the in-progress scrape result and flushes to disk on every change."""
    def __init__(self, path: Path, mode: str, handle: str):
        self.path = path
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
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.state, indent=2, ensure_ascii=False), encoding="utf-8")

    def set_header(self, name: str, header_text: str, stats_raw: list[str]):
        self.state["header"] = {"name": name, "header_text": header_text, "stats_raw": stats_raw}
        self._flush()

    def mark_private(self):
        self.state["private"] = True
        self._flush()

    def add_reel(self, url: str, caption: str, creator: str, audio: str):
        self.state["reels"].append({"url": url, "caption": caption, "creator": creator, "audio": audio})
        self._flush()

    def add_highlight(self, title: str, cover_text: str, slides: list[str]):
        self.state["highlights"].append({"title": title, "cover_text": cover_text, "slides": list(slides)})
        self._flush()

    def add_grid_post(self, url: str, caption: str, comments: list[str], likes_raw: str):
        self.state["grid"].append({"url": url, "caption": caption, "comments": list(comments), "likes_raw": likes_raw})
        self._flush()

    def add_tagged(self, url: str):
        self.state["tagged"].append({"url": url})
        self._flush()

    def add_saved(self, url: str):
        self.state.setdefault("saved", []).append({"url": url})
        self._flush()

    def add_following(self, handle: str, display_name: str):
        self.state["following"].append({"handle": handle, "display_name": display_name})
        self._flush()

    def add_mutual(self, handle: str, display_name: str):
        self.state["mutuals"].append({"handle": handle, "display_name": display_name})
        self._flush()

    def snapshot(self) -> dict:
        return dict(self.state)


# ---------- task prompts ----------
GLOBAL_RULES = """
HARD RULES (apply to every step):
- IGNORE the Explore feed, "Suggested Posts", "Suggested for you", and any popup or
  banner that says "Open in app", "See notifications", "Turn on notifications",
  "Log in to see more", or asks to save login info. Dismiss them and keep going.
- Do NOT click on stories, ads, or sidebars. Stay on the profile / reposts /
  highlights / grid surfaces only.
- If a surface fails to load after 2 attempts, SKIP it and move on. Never get stuck.
- URLs in path form: "/p/ABC/" or "/reel/XYZ/" (no domain).
- CHECKPOINT EVERYTHING. Call the save_* tool immediately after extracting each
  piece of data — not at the end. If the loop dies, only the data you saved is
  preserved.
- Use snap_interactive to see clickable elements with stable refs (e.g. e12),
  then click(ref). After a navigation or DOM mutation, snap again — refs change.
- Use screenshot only when text/snapshot tools aren't enough (reel caption
  overlays burned into video, audio names rendered as part of the visual UI).
"""


def _self_task(handle: str) -> str:
    return f"""You are scraping the Instagram account @{handle} (the logged-in user's own account).

Go to https://www.instagram.com/{handle}/reposts/ for STEP 1, then collect surfaces in this order:

**STEP 1 (CRITICAL): REPOSTS TAB — the main event. THIS IS NOT THE REELS TAB.**
/{handle}/reposts/ is the dedicated Reposts surface. What the user has boosted
from other creators is the highest-fidelity taste signal on the whole profile.

**TARGET: 20 reposts minimum, 30 max.** Scroll the modal to load more.

Per repost: URL path, caption, creator (the ORIGINAL POSTER's @handle, NOT
@{handle}; the whole point is distinguishing original creators), audio track.
Call **save_reel** after each one. Save as you go.

**STEP 2: STORY HIGHLIGHTS — pinned identity.**
Back on /{handle}/. Open up to 5 highlight bubbles, left to right. Per bubble:
title, cover_text, one short string per slide (caption / location / audio / sticker).
Call **save_highlight** after closing each.

**STEP 3: PROFILE HEADER.**
Display name, bio (with links), stats (posts/followers/following).
Call **save_header** once.

**STEP 4: GRID POSTS — what they actually broadcast.**
Up to 5 most recent grid posts. Per post: URL, caption, ~5 top comments, likes.
Call **save_grid_post** after each.

Don't touch saved, tagged, or Following — those aren't needed.

{GLOBAL_RULES}

Finish when done or by step 50. Everything is on disk via save_* calls; ending
the loop is fine. Stop calling tools when there's nothing left to collect.
"""


def _target_task(handle: str) -> str:
    return f"""You are scraping the Instagram account @{handle} (someone else, vibe check for the user).

Go to https://www.instagram.com/{handle}/.

**FIRST: privacy gate.**
If you see "This Account is Private" or "This profile is private" and there is NO
visible content beyond the header, call **mark_private**, then call **save_header**
with whatever header data is visible, and stop. Don't try to bypass.

Otherwise navigate to /{handle}/reposts/ for STEP 1, then collect:

**STEP 1 (CRITICAL): REPOSTS TAB — the main event.**
/{handle}/reposts/ is the dedicated Reposts surface. What the target has boosted
from other creators is the highest-fidelity taste signal.

**TARGET: 20 reposts minimum, 30 max.** Scroll to load more.

Per repost: URL, caption, creator (the ORIGINAL poster's @handle), audio track.
Call **save_reel** after each. Save as you go.

**STEP 2: STORY HIGHLIGHTS — pinned identity.**
Up to 5 highlight bubbles. Per bubble: title, cover_text, one short string per
slide (caption / location / audio / sticker).
Call **save_highlight** after closing each.

**STEP 3: PROFILE HEADER.**
Display name, bio, stats. Call **save_header** once.

**STEP 4: GRID POSTS — what they actually broadcast.**
Up to 5 most recent grid posts. Per post: URL, caption, ~5 top comments, likes.
Call **save_grid_post** after each.

**STEP 5: TAGGED — partner-signal sweep.**
/{handle}/tagged/ often reveals a partner or close friend who tags the target
repeatedly. Collect up to 8 tagged URLs. Call **save_tagged** each.

Don't touch mutuals, Following, or Saved — keep it tight.

{GLOBAL_RULES}

Finish when done or by step 50. Everything is on disk via save_* calls.
"""


# ---------- tool schema ----------
def _tools(mode: str) -> list[dict]:
    save_tools: list[dict] = [
        {
            "name": "save_header",
            "description": "Save the profile header. Call once after extracting name/bio/stats.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "header_text": {"type": "string"},
                    "stats_raw": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["name", "header_text", "stats_raw"],
            },
        },
        {
            "name": "mark_private",
            "description": "Mark this target account as PRIVATE. Call only when the page shows the private wall and content is not visible.",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "save_reel",
            "description": "Save one repost from the Reposts tab. URL path like /p/ABC/ or /reel/XYZ/, caption, creator (especially the ORIGINAL poster for reposts), audio track name.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "caption": {"type": "string"},
                    "creator": {"type": "string"},
                    "audio": {"type": "string"},
                },
                "required": ["url", "caption", "creator", "audio"],
            },
        },
        {
            "name": "save_highlight",
            "description": "Save one story highlight bubble after clicking through all its slides.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "cover_text": {"type": "string"},
                    "slides": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "cover_text", "slides"],
            },
        },
        {
            "name": "save_grid_post",
            "description": "Save one grid post after opening it and reading caption + top comments + likes.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "caption": {"type": "string"},
                    "comments": {"type": "array", "items": {"type": "string"}},
                    "likes_raw": {"type": "string"},
                },
                "required": ["url", "caption", "comments", "likes_raw"],
            },
        },
        {
            "name": "save_tagged",
            "description": "Save one tagged post URL. TARGET MODE only.",
            "input_schema": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    ]
    if mode == "self":
        save_tools.append({
            "name": "save_saved",
            "description": "Save one saved post URL. SELF MODE only.",
            "input_schema": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        })

    browser_tools: list[dict] = [
        {
            "name": "navigate",
            "description": "Navigate the current tab to a URL. Full https URLs.",
            "input_schema": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
        {
            "name": "snap_interactive",
            "description": "Return interactive elements on the current page as a list of {ref, role, name}. Refs (e0, e1, ...) are stable until the DOM mutates.",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "snap_text",
            "description": "Return the full a11y tree as indented text. Use when you need non-interactive content like captions or comments.",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "page_text",
            "description": "Return all visible text on the page. Heavier than snap_text.",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "click",
            "description": "Click an element by its ref from snap_interactive.",
            "input_schema": {
                "type": "object",
                "properties": {"ref": {"type": "string"}},
                "required": ["ref"],
            },
        },
        {
            "name": "type",
            "description": "Type text into an input element identified by ref.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "ref": {"type": "string"},
                    "text": {"type": "string"},
                    "submit": {"type": "boolean", "description": "Press Enter after typing"},
                },
                "required": ["ref", "text"],
            },
        },
        {
            "name": "scroll",
            "description": "Scroll the page by dy pixels (positive = down).",
            "input_schema": {
                "type": "object",
                "properties": {"dy": {"type": "integer"}},
                "required": ["dy"],
            },
        },
        {
            "name": "back",
            "description": "Browser back button.",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "current_url",
            "description": "Get the current page URL.",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "screenshot",
            "description": "Take a screenshot of the current page and return it as a visible image. Use only when text tools aren't enough (reel caption overlays burned into video, audio overlays, photo content).",
            "input_schema": {
                "type": "object",
                "properties": {"full_page": {"type": "boolean"}},
            },
        },
    ]
    return browser_tools + save_tools


# ---------- tool dispatch ----------
def _execute_tool(tab: KuriTab, store: CheckpointStore, name: str, args: dict) -> Any:
    """Run a tool call. Returns either a string (text tool_result) or a list of
    Anthropic content blocks (used for image responses from screenshot)."""
    try:
        # ---- save_* (checkpoint to disk) ----
        if name == "save_header":
            store.set_header(args["name"], args["header_text"], args.get("stats_raw", []))
            return f"header saved: name={args['name']!r}"
        if name == "mark_private":
            store.mark_private()
            return "marked private"
        if name == "save_reel":
            store.add_reel(args["url"], args["caption"], args["creator"], args["audio"])
            return f"reel #{len(store.state['reels'])} saved: {args['url']}"
        if name == "save_highlight":
            store.add_highlight(args["title"], args.get("cover_text", ""), args.get("slides", []))
            return f"highlight #{len(store.state['highlights'])} saved: {args['title']!r} ({len(args.get('slides', []))} slides)"
        if name == "save_grid_post":
            store.add_grid_post(args["url"], args["caption"], args.get("comments", []), args.get("likes_raw", ""))
            return f"grid post #{len(store.state['grid'])} saved: {args['url']}"
        if name == "save_tagged":
            store.add_tagged(args["url"])
            return f"tagged #{len(store.state['tagged'])} saved"
        if name == "save_saved":
            store.add_saved(args["url"])
            return f"saved #{len(store.state.get('saved') or [])} saved"

        # ---- browser tools (kuri) ----
        if name == "navigate":
            tab.goto(args["url"])
            time.sleep(2.5)
            return f"navigated to {tab.url()}"
        if name == "snap_interactive":
            nodes = tab.snap(interactive_only=True)
            return json.dumps([{"ref": n.ref, "role": n.role, "name": n.name} for n in nodes])
        if name == "snap_text":
            return tab.snap_text()
        if name == "page_text":
            return tab.text()[:6000]
        if name == "click":
            tab.click(args["ref"])
            time.sleep(1.2)
            return "clicked"
        if name == "type":
            tab.type(args["ref"], args["text"], submit=args.get("submit", False))
            time.sleep(0.6)
            return "typed"
        if name == "scroll":
            tab.scroll(int(args["dy"]))
            time.sleep(0.8)
            return "scrolled"
        if name == "back":
            tab.back()
            time.sleep(1.5)
            return f"back -> {tab.url()}"
        if name == "current_url":
            return tab.url()
        if name == "screenshot":
            png = tab.screenshot(full_page=bool(args.get("full_page", False)))
            return [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                              "data": base64.b64encode(png).decode("ascii")}},
                {"type": "text", "text": f"screenshot of {tab.url()}"},
            ]
        return f"unknown tool: {name}"
    except KuriError as e:
        return f"tool error: {e}"
    except Exception as e:
        return f"tool error: {type(e).__name__}: {e}"


# ---------- agent loop ----------
def _short_args(d: dict) -> str:
    s = json.dumps(d, ensure_ascii=False)
    return s if len(s) < 80 else s[:77] + "..."


async def _run(task: str, mode: str, handle: str, narrate: NarrateCb = None) -> dict:
    out_path = OUT_DIR / f"{mode}_{handle}.json"
    store = CheckpointStore(out_path, mode, handle)

    k = Kuri()
    try:
        tab = k.first_tab()
    except KuriError as e:
        raise KuriError(f"kuri not reachable (start it first): {e}") from e

    tools = _tools(mode)
    client = Anthropic(timeout=LLM_TIMEOUT_S)

    messages: list[dict] = [{"role": "user", "content": "Begin. Use the tools to complete the task above."}]
    system = [
        {"type": "text", "text": task, "cache_control": {"type": "ephemeral"}},
    ]

    for step in range(1, MAX_TURNS + 1):
        try:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=4096,
                system=system,
                tools=tools,
                messages=messages,
            )
        except Exception as e:
            print(f">>> [{step}] LLM call failed: {type(e).__name__}: {e}")
            break

        thinking_chunks = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
        thinking = "\n".join(thinking_chunks).strip()

        tool_uses = [b for b in resp.content if getattr(b, "type", "") == "tool_use"]
        next_goal = ", ".join(f"{tu.name}({_short_args(tu.input or {})})" for tu in tool_uses) if tool_uses else ""

        if thinking or next_goal:
            print(f">>> [{step}] {thinking}".rstrip())
            if next_goal:
                print(f">>> [{step}] next: {next_goal}")
            if narrate is not None:
                try:
                    narrate(step, thinking, next_goal)
                except Exception as e:
                    print(f">>> [narrate-cb error] {e}")

        messages.append({"role": "assistant", "content": resp.content})

        # No tool calls = agent decided it's done.
        if not tool_uses:
            break

        results: list[dict] = []
        for tu in tool_uses:
            out = _execute_tool(tab, store, tu.name, tu.input or {})
            if isinstance(out, str) and len(out) > 8000:
                out = out[:8000] + "\n... [truncated]"
            results.append({"type": "tool_result", "tool_use_id": tu.id, "content": out})

        messages.append({"role": "user", "content": results})

    # Whatever happened, the checkpoint file on disk is the canonical result.
    return json.loads(out_path.read_text(encoding="utf-8"))


def _write(data: dict, mode: str, handle: str) -> dict:
    # CheckpointStore already wrote the file; this is just for the log line.
    out_path = OUT_DIR / f"{mode}_{handle}.json"
    if not out_path.exists():
        out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {out_path}")
    return data


def scrape_self(handle: str, narrate: NarrateCb = None) -> dict:
    print(f">>> starting self-scrape for @{handle}")
    data = asyncio.run(_run(_self_task(handle), "self", handle, narrate=narrate))
    return _write(data, "self", handle)


def scrape_target(handle: str, narrate: NarrateCb = None) -> dict:
    print(f">>> starting target-scrape for @{handle}")
    data = asyncio.run(_run(_target_task(handle), "target", handle, narrate=narrate))
    return _write(data, "target", handle)


# ---------- focused investigation used by the chat layer ----------
async def focused_scrape(query: str, base_handle: str, narrate: NarrateCb = None) -> str:
    """Open a kuri-driven Claude loop for a follow-up question. Returns prose evidence."""
    task = f"""You are investigating Instagram for a follow-up question about @{base_handle}.

QUESTION: {query}

Start at https://www.instagram.com/{base_handle}/. You may navigate to other
profiles they interact with (frequent commenters, tagged accounts, people they
repost). Read posts, captions, comments, and reels as needed.

{GLOBAL_RULES}

When you have an answer, STOP calling tools — your final text response IS the
answer. Cite specific posts (URL path), commenters (@handle), reels, captions,
or audio tracks you saw. If you couldn't find an answer, say so plainly and
describe what you did look at. Keep it under ~400 words. No speculation beyond
what's visible.
"""

    k = Kuri()
    tab = k.first_tab()
    # Reuse the browser tool list (no save_* — this returns prose)
    tools = [t for t in _tools("target") if not t["name"].startswith("save_") and t["name"] != "mark_private"]
    client = Anthropic(timeout=LLM_TIMEOUT_S)

    # Throwaway store so _execute_tool's signature works; we never call save_*.
    store = CheckpointStore(OUT_DIR / f"_focused_{base_handle}.tmp.json", "target", base_handle)

    messages: list[dict] = [{"role": "user", "content": "Begin investigating to answer the question above."}]
    system = [{"type": "text", "text": task, "cache_control": {"type": "ephemeral"}}]

    final_text = ""
    for step in range(1, MAX_TURNS + 1):
        try:
            resp = await asyncio.to_thread(
                client.messages.create,
                model=MODEL, max_tokens=4096, system=system, tools=tools, messages=messages,
            )
        except Exception as e:
            print(f">>> [focused {step}] LLM failed: {type(e).__name__}: {e}")
            break

        thinking_chunks = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
        text = "\n".join(thinking_chunks).strip()
        tool_uses = [b for b in resp.content if getattr(b, "type", "") == "tool_use"]
        next_goal = ", ".join(f"{tu.name}({_short_args(tu.input or {})})" for tu in tool_uses) if tool_uses else ""

        if text or next_goal:
            print(f">>> [focused {step}] {text}".rstrip())
            if next_goal:
                print(f">>> [focused {step}] next: {next_goal}")
            if narrate is not None:
                try:
                    narrate(step, text, next_goal)
                except Exception:
                    pass

        messages.append({"role": "assistant", "content": resp.content})
        if not tool_uses:
            final_text = text
            break

        results: list[dict] = []
        for tu in tool_uses:
            out = _execute_tool(tab, store, tu.name, tu.input or {})
            if isinstance(out, str) and len(out) > 8000:
                out = out[:8000] + "\n... [truncated]"
            results.append({"type": "tool_result", "tool_use_id": tu.id, "content": out})
        messages.append({"role": "user", "content": results})

    try:
        (OUT_DIR / f"_focused_{base_handle}.tmp.json").unlink(missing_ok=True)
    except Exception:
        pass

    return final_text.strip() or "(no evidence found)"


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3 or sys.argv[1] not in ("self", "target"):
        print("usage: python agent_scrape.py [self|target] <handle>")
        sys.exit(1)
    (scrape_self if sys.argv[1] == "self" else scrape_target)(sys.argv[2])
