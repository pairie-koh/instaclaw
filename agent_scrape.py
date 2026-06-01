"""Kuri-driven Instagram scraper, rebuilt on the codegraff SDK.

The forge agent (codegraff Python SDK; see cg_agent.py) drives a real Chrome
through the `kuri` MCP server (kuri_mcp.py): browser-control tools plus save_*
checkpoint tools that flush out/{mode}_{handle}.json on every call. instaclaw
hands the agent a scrape task with graff.chat() and reads the checkpoint file
when the turn ends — so a dead loop still leaves everything collected so far on
disk. The screenshot tool routes a PNG through the omnimodal model over the
codegraff gateway and returns text (handled inside kuri_mcp.py).

Public surface (ScrapeResult, CheckpointStore, scrape_self/target/
focused_scrape, PROFILE_DIR) is preserved so analyze.py / server.py / render.py
work unchanged.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Optional

from pydantic import BaseModel, Field

import cg_agent

# Narrate callback signature used by the web layer: (step, thinking, next_goal).
NarrateCb = Optional[Callable[[int, str, str], None]]

ROOT = Path(__file__).parent
PROFILE_DIR = ROOT / ".chrome-profile"
OUT_DIR = ROOT / "out"
OUT_DIR.mkdir(exist_ok=True)

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


# ---------- engine: the forge agent drives the kuri MCP server ----------
def _kuri_preamble(stem: str) -> str:
    return f"""You are driving a real, already-logged-in Chrome browser through the `kuri` MCP server to scrape Instagram.

Use ONLY the kuri tools listed below. Do NOT use shell, file, web-search, or any
other tools — they cannot see Instagram and will only waste turns.

Browser: navigate(url), snap_interactive(), snap_text(), page_text(),
click(ref), type_text(ref, text, submit), scroll(dy), back(), current_url(),
screenshot(full_page).

Checkpoint (call the instant you extract each item, and pass stem="{stem}" to
EVERY one): save_header, mark_private, save_reel, save_highlight,
save_grid_post, save_tagged, save_saved.

Hard requirement: every save_* call MUST include stem="{stem}".
"""


def _empty_result(mode: str, handle: str) -> dict:
    state: dict = {
        "mode": mode, "handle": handle, "private": False,
        "header": {"name": "", "header_text": "", "stats_raw": []},
        "grid": [], "reels": [], "tagged": [], "highlights": [],
        "following": [], "mutuals": [],
    }
    if mode == "self":
        state["saved"] = []
    return state


def _run(task: str, mode: str, handle: str, narrate: NarrateCb = None) -> dict:
    """Hand the scrape task to the forge agent; it drives kuri and flushes
    out/{stem}.json through the save_* tools. Read that file back when the turn
    ends — whatever was checkpointed is the canonical result."""
    stem = f"{mode}_{handle}"
    out_path = OUT_DIR / f"{stem}.json"
    prompt = _kuri_preamble(stem) + "\n" + task
    try:
        cg_agent.run(prompt, narrate=narrate, agent_id=cg_agent.SCRAPER_AGENT)
    except Exception as e:  # noqa
        print(f">>> agent run failed: {type(e).__name__}: {e}")
    if out_path.exists():
        return json.loads(out_path.read_text(encoding="utf-8"))
    return _empty_result(mode, handle)


def _write(data: dict, mode: str, handle: str) -> dict:
    # The kuri save_* tools already wrote the file; this is the log line + a
    # fallback flush if the agent saved nothing.
    out_path = OUT_DIR / f"{mode}_{handle}.json"
    if not out_path.exists():
        out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {out_path}")
    return data


def scrape_self(handle: str, narrate: NarrateCb = None) -> dict:
    print(f">>> starting self-scrape for @{handle}")
    data = _run(_self_task(handle), "self", handle, narrate=narrate)
    return _write(data, "self", handle)


def scrape_target(handle: str, narrate: NarrateCb = None) -> dict:
    print(f">>> starting target-scrape for @{handle}")
    data = _run(_target_task(handle), "target", handle, narrate=narrate)
    return _write(data, "target", handle)


# ---------- focused investigation used by the chat layer ----------
async def focused_scrape(query: str, base_handle: str, narrate: NarrateCb = None) -> str:
    """Run a kuri-driven forge loop for a follow-up question. Returns prose evidence.

    No save_* here — the agent's final text response IS the answer.
    """
    import asyncio
    task = _kuri_preamble(f"focused_{base_handle}") + "\n" + f"""You are investigating Instagram for a follow-up question about @{base_handle}.

QUESTION: {query}

Start at https://www.instagram.com/{base_handle}/. You may navigate to other
profiles they interact with (frequent commenters, tagged accounts, people they
repost). Read posts, captions, comments, and reels as needed. You do NOT need to
save anything.

{GLOBAL_RULES}

When you have an answer, STOP. Your final text response IS the answer. Cite
specific posts (URL path), commenters (@handle), reels, captions, or audio
tracks you saw. If you couldn't find an answer, say so plainly and describe what
you looked at. Keep it under ~400 words. No speculation beyond what's visible.
"""
    text = await asyncio.to_thread(cg_agent.run, task, narrate, None, cg_agent.SCRAPER_AGENT)
    return (text or "").strip() or "(no evidence found)"


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3 or sys.argv[1] not in ("self", "target"):
        print("usage: python agent_scrape.py [self|target] <handle>")
        sys.exit(1)
    (scrape_self if sys.argv[1] == "self" else scrape_target)(sys.argv[2])
