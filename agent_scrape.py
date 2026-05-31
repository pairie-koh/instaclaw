"""Kuri-driven Instagram scraper with incremental checkpointing.

Each piece of data the agent extracts is written to disk through a dedicated
save_* tool action, so if the loop dies (LLM timeout, context bloat, exception)
the partial file on disk still has everything collected so far. Public surface
(ScrapeResult, CheckpointStore, scrape_self/target/focused_scrape, PROFILE_DIR)
is preserved so analyze.py / server.py / render.py work unchanged.

Engine: kuri (https://github.com/justrach/kuri) v0.4.5+ drives Chrome over CDP
via its HTTP API. Both the nav loop and the screenshot tool's vision side call
run on Xiaomi MiMo-V2.5 (omnimodal — text agent + vision in one model) via
OpenRouter. The screenshot tool captures a PNG, posts it to the same model in
a separate single-shot call, and returns the text description back to the nav
loop as the tool_result — keeps the nav loop's context lean while still
letting overlay text on reel video frames be read on demand.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from pathlib import Path
from typing import Callable, Optional

from openai import OpenAI
from pydantic import BaseModel, Field

from kuri_client import Kuri, KuriError, KuriTab

# Narrate callback signature used by the web layer: (step, thinking, next_goal).
NarrateCb = Optional[Callable[[int, str, str], None]]

ROOT = Path(__file__).parent
PROFILE_DIR = ROOT / ".chrome-profile"
OUT_DIR = ROOT / "out"
OUT_DIR.mkdir(exist_ok=True)
MODEL = os.environ.get("INSTACLAW_MODEL", "xiaomi/mimo-v2.5")
LLM_TIMEOUT_S = int(os.environ.get("INSTACLAW_LLM_TIMEOUT", "180"))
MAX_TURNS = int(os.environ.get("INSTACLAW_MAX_TURNS", "60"))
OPENROUTER_BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")


def _client() -> OpenAI:
    return OpenAI(base_url=OPENROUTER_BASE_URL,
                  api_key=os.environ.get("OPENROUTER_API_KEY", ""),
                  timeout=LLM_TIMEOUT_S)


def _describe_screenshot(png_bytes: bytes, current_url: str) -> str:
    """Side call to the omnimodal model with the captured PNG. Returns a focused
    text description so the nav loop receives a string-shaped tool_result."""
    b64 = base64.b64encode(png_bytes).decode("ascii")
    try:
        resp = _client().chat.completions.create(
            model=MODEL,
            max_tokens=800,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": (
                        f"This is a screenshot of {current_url}. List every piece of "
                        "text visible in the image, with priority on: text burned into "
                        "video frames (overlay captions, audio track names, sticker "
                        "text), text on image posts, and any handle / caption / count "
                        "not already in plain DOM. Transcribe verbatim where possible. "
                        "If no extra text is present beyond standard IG chrome, say so."
                    )},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/png;base64,{b64}"}},
                ],
            }],
        )
        return (resp.choices[0].message.content or "").strip() or "(vision returned no description)"
    except Exception as e:
        return f"(vision call failed: {type(e).__name__}: {e})"


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
- Use snap_text and page_text first for caption / comment / audio text. Use
  the `screenshot` tool only when text tools aren't enough — overlay text
  burned into reel video frames, audio names rendered as visual UI. The
  screenshot is read by a separate vision model and returned to you as a
  text description.
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


# ---------- tool schema (OpenAI function-calling format) ----------
def _fn(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


def _tools(mode: str) -> list[dict]:
    save_tools: list[dict] = [
        _fn("save_header",
            "Save the profile header. Call once after extracting name/bio/stats.",
            {"name": {"type": "string"},
             "header_text": {"type": "string"},
             "stats_raw": {"type": "array", "items": {"type": "string"}}},
            ["name", "header_text", "stats_raw"]),
        _fn("mark_private",
            "Mark this target account as PRIVATE. Call only when the page shows the private wall and content is not visible.",
            {}, []),
        _fn("save_reel",
            "Save one repost from the Reposts tab. URL path like /p/ABC/ or /reel/XYZ/, caption, creator (especially the ORIGINAL poster for reposts), audio track name.",
            {"url": {"type": "string"},
             "caption": {"type": "string"},
             "creator": {"type": "string"},
             "audio": {"type": "string"}},
            ["url", "caption", "creator", "audio"]),
        _fn("save_highlight",
            "Save one story highlight bubble after clicking through all its slides.",
            {"title": {"type": "string"},
             "cover_text": {"type": "string"},
             "slides": {"type": "array", "items": {"type": "string"}}},
            ["title", "cover_text", "slides"]),
        _fn("save_grid_post",
            "Save one grid post after opening it and reading caption + top comments + likes.",
            {"url": {"type": "string"},
             "caption": {"type": "string"},
             "comments": {"type": "array", "items": {"type": "string"}},
             "likes_raw": {"type": "string"}},
            ["url", "caption", "comments", "likes_raw"]),
        _fn("save_tagged",
            "Save one tagged post URL. TARGET MODE only.",
            {"url": {"type": "string"}}, ["url"]),
    ]
    if mode == "self":
        save_tools.append(_fn("save_saved",
            "Save one saved post URL. SELF MODE only.",
            {"url": {"type": "string"}}, ["url"]))

    browser_tools: list[dict] = [
        _fn("navigate", "Navigate the current tab to a URL. Full https URLs.",
            {"url": {"type": "string"}}, ["url"]),
        _fn("snap_interactive",
            "Return interactive elements on the current page as a list of {ref, role, name}. Refs (e0, e1, ...) are stable until the DOM mutates.",
            {}, []),
        _fn("snap_text",
            "Return the full a11y tree as indented text. Use when you need non-interactive content like captions or comments.",
            {}, []),
        _fn("page_text",
            "Return all visible text on the page. Heavier than snap_text.",
            {}, []),
        _fn("click", "Click an element by its ref from snap_interactive.",
            {"ref": {"type": "string"}}, ["ref"]),
        _fn("type", "Type text into an input element identified by ref.",
            {"ref": {"type": "string"},
             "text": {"type": "string"},
             "submit": {"type": "boolean", "description": "Press Enter after typing"}},
            ["ref", "text"]),
        _fn("scroll", "Scroll the page by dy pixels (positive = down).",
            {"dy": {"type": "integer"}}, ["dy"]),
        _fn("back", "Browser back button.", {}, []),
        _fn("current_url", "Get the current page URL.", {}, []),
        _fn("screenshot",
            "Capture a PNG of the current page, route it through a vision model, and return that model's text description. Use only when text tools (snap_text, page_text) aren't enough — e.g. overlay text burned into a reel video frame, audio name rendered as visual UI.",
            {"full_page": {"type": "boolean",
                           "description": "Capture the full scrollable page instead of just the viewport."}},
            []),
    ]
    return browser_tools + save_tools


# ---------- tool dispatch ----------
def _execute_tool(tab: KuriTab, store: CheckpointStore, name: str, args: dict) -> str:
    """Run a tool call. Returns a string for the tool_result content."""
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
            current = tab.url()
            description = _describe_screenshot(png, current)
            return f"vision description of {current}:\n{description}"
        return f"unknown tool: {name}"
    except KuriError as e:
        return f"tool error: {e}"
    except Exception as e:
        return f"tool error: {type(e).__name__}: {e}"


# ---------- agent loop ----------
def _short_args(d: dict) -> str:
    s = json.dumps(d, ensure_ascii=False)
    return s if len(s) < 80 else s[:77] + "..."


def _parse_args(arg_str: str) -> dict:
    """OpenAI-compatible APIs return tool-call arguments as a JSON string. Parse defensively."""
    if not arg_str:
        return {}
    try:
        return json.loads(arg_str)
    except json.JSONDecodeError:
        return {}


async def _run(task: str, mode: str, handle: str, narrate: NarrateCb = None) -> dict:
    out_path = OUT_DIR / f"{mode}_{handle}.json"
    store = CheckpointStore(out_path, mode, handle)

    k = Kuri()
    try:
        tab = k.first_tab()
    except KuriError as e:
        raise KuriError(f"kuri not reachable (start it first): {e}") from e

    tools = _tools(mode)
    client = _client()

    messages: list[dict] = [
        {"role": "system", "content": task},
        {"role": "user", "content": "Begin. Use the tools to complete the task above."},
    ]

    for step in range(1, MAX_TURNS + 1):
        try:
            resp = client.chat.completions.create(
                model=MODEL,
                max_tokens=4096,
                tools=tools,
                messages=messages,
            )
        except Exception as e:
            print(f">>> [{step}] LLM call failed: {type(e).__name__}: {e}")
            break

        msg = resp.choices[0].message
        thinking = (msg.content or "").strip()
        tool_calls = msg.tool_calls or []
        next_goal = ", ".join(
            f"{tc.function.name}({_short_args(_parse_args(tc.function.arguments))})"
            for tc in tool_calls
        ) if tool_calls else ""

        if thinking or next_goal:
            print(f">>> [{step}] {thinking}".rstrip())
            if next_goal:
                print(f">>> [{step}] next: {next_goal}")
            if narrate is not None:
                try:
                    narrate(step, thinking, next_goal)
                except Exception as e:
                    print(f">>> [narrate-cb error] {e}")

        # Append the assistant turn verbatim so the model sees its own tool_calls
        # on the next round.
        assistant_msg: dict = {"role": "assistant", "content": msg.content or ""}
        if tool_calls:
            assistant_msg["tool_calls"] = [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name,
                              "arguments": tc.function.arguments or "{}"}}
                for tc in tool_calls
            ]
        messages.append(assistant_msg)

        # No tool calls = agent decided it's done.
        if not tool_calls:
            break

        for tc in tool_calls:
            args = _parse_args(tc.function.arguments)
            out = _execute_tool(tab, store, tc.function.name, args)
            if len(out) > 8000:
                out = out[:8000] + "\n... [truncated]"
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": out})

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
    """Open a kuri-driven MiMo loop for a follow-up question. Returns prose evidence."""
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
    # Reuse the browser tool list (no save_* — this returns prose).
    tools = [
        t for t in _tools("target")
        if not t["function"]["name"].startswith("save_")
        and t["function"]["name"] != "mark_private"
    ]
    client = _client()

    # Throwaway store so _execute_tool's signature works; we never call save_*.
    store = CheckpointStore(OUT_DIR / f"_focused_{base_handle}.tmp.json", "target", base_handle)

    messages: list[dict] = [
        {"role": "system", "content": task},
        {"role": "user", "content": "Begin investigating to answer the question above."},
    ]

    final_text = ""
    for step in range(1, MAX_TURNS + 1):
        try:
            resp = await asyncio.to_thread(
                client.chat.completions.create,
                model=MODEL, max_tokens=4096, tools=tools, messages=messages,
            )
        except Exception as e:
            print(f">>> [focused {step}] LLM failed: {type(e).__name__}: {e}")
            break

        msg = resp.choices[0].message
        text = (msg.content or "").strip()
        tool_calls = msg.tool_calls or []
        next_goal = ", ".join(
            f"{tc.function.name}({_short_args(_parse_args(tc.function.arguments))})"
            for tc in tool_calls
        ) if tool_calls else ""

        if text or next_goal:
            print(f">>> [focused {step}] {text}".rstrip())
            if next_goal:
                print(f">>> [focused {step}] next: {next_goal}")
            if narrate is not None:
                try:
                    narrate(step, text, next_goal)
                except Exception:
                    pass

        assistant_msg: dict = {"role": "assistant", "content": msg.content or ""}
        if tool_calls:
            assistant_msg["tool_calls"] = [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name,
                              "arguments": tc.function.arguments or "{}"}}
                for tc in tool_calls
            ]
        messages.append(assistant_msg)

        if not tool_calls:
            final_text = text
            break

        for tc in tool_calls:
            args = _parse_args(tc.function.arguments)
            out = _execute_tool(tab, store, tc.function.name, args)
            if len(out) > 8000:
                out = out[:8000] + "\n... [truncated]"
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": out})

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
