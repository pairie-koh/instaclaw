"""Browser-use powered Instagram scraper with incremental checkpointing.

Reels-first task order: reposts are the #1 dating-recon signal so we collect those
before anything else. Each piece of data the agent extracts is written to disk
through a custom save_* action — so if browser-use terminates from LLM timeouts
or repeated failures, the partial file on disk still has everything collected so
far. Nothing is lost on crash.

Same output schema as before — analyze.py / main.py / server.py keep working.
"""
import asyncio
import json
from pathlib import Path
from typing import Callable, Optional

from pydantic import BaseModel, Field

from browser_use import Agent, Browser, BrowserProfile, ChatAnthropic, Controller
from browser_use.browser.views import BrowserStateSummary
from browser_use.agent.views import AgentOutput

# Narrate callback signature used by the web layer: (step, thinking, next_goal).
NarrateCb = Optional[Callable[[int, str, str], None]]

ROOT = Path(__file__).parent
PROFILE_DIR = ROOT / ".chrome-profile"
OUT_DIR = ROOT / "out"
OUT_DIR.mkdir(exist_ok=True)
MODEL = "claude-sonnet-4-6"
# Long timeout — context bloats heavily by step 40+ when the agent has collected
# a lot of data. 90s default was too tight; 180s gives Sonnet room.
LLM_TIMEOUT_S = 180
MAX_FAILURES = 10


# ---------- output schema (kept for type compatibility — partial files match this) ----------
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


# ---------- checkpoint store (writes after every save_*) ----------
class CheckpointStore:
    """Holds the in-progress scrape result and flushes to disk on every change.

    The agent calls save_* actions throughout the scrape. Each call mutates the
    store and immediately flushes. If browser-use terminates the agent for any
    reason (timeouts, max failures, exception), the file already contains
    everything collected up to that moment.
    """
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


# ---------- narration callback ----------
def _narrate(state: BrowserStateSummary, output: AgentOutput, step: int) -> None:
    cs = output.current_state
    if cs.thinking:
        print(f">>> [{step}] {cs.thinking.strip()}")
    if cs.next_goal:
        print(f">>> [{step}] next: {cs.next_goal.strip()}")


def _make_callback(extra: NarrateCb):
    def cb(state: BrowserStateSummary, output: AgentOutput, step: int) -> None:
        _narrate(state, output, step)
        if extra is not None:
            cs = output.current_state
            try:
                extra(step, (cs.thinking or "").strip(), (cs.next_goal or "").strip())
            except Exception as e:
                print(f">>> [narrate-cb error] {e}")
    return cb


# ---------- controller with incremental-save actions ----------
def _make_controller(store: CheckpointStore) -> Controller:
    """Register save_* actions that the agent calls as it collects each piece of data.

    Each action immediately flushes to disk so the partial file is always current.
    The agent is told (in the task prompt) to call these as it goes, not at the end.
    """
    controller = Controller()

    @controller.action(
        "Save the profile header. Call this once, right after extracting name/bio/stats."
    )
    async def save_header(name: str, header_text: str, stats_raw: list[str]) -> str:
        store.set_header(name, header_text, stats_raw)
        return f"header saved: name={name!r}"

    @controller.action(
        "Mark this target account as PRIVATE. Call this if the profile shows "
        "'This Account is Private' or 'This profile is private' and you cannot see content."
    )
    async def mark_private() -> str:
        store.mark_private()
        return "marked private"

    @controller.action(
        "Save one reel from the Reels tab. Call this AFTER opening and reading the reel — "
        "URL path like '/reel/ABC/', caption text, creator (especially the original poster's "
        "@handle for REPOSTS), and the audio track name shown at the bottom."
    )
    async def save_reel(url: str, caption: str, creator: str, audio: str) -> str:
        store.add_reel(url, caption, creator, audio)
        return f"reel #{len(store.state['reels'])} saved: {url}"

    @controller.action(
        "Save one story highlight bubble. Call this after clicking through all its slides. "
        "Provide: title (label under the bubble, e.g. 'travel'), cover_text (any text on the "
        "bubble before opening), and slides (one short string per slide — caption overlays, "
        "location stamps, audio track names, sticker text)."
    )
    async def save_highlight(title: str, cover_text: str, slides: list[str]) -> str:
        store.add_highlight(title, cover_text, slides)
        return f"highlight #{len(store.state['highlights'])} saved: {title!r} ({len(slides)} slides)"

    @controller.action(
        "Save one grid post. Call this after opening the post and reading caption + top comments + likes."
    )
    async def save_grid_post(url: str, caption: str, comments: list[str], likes_raw: str) -> str:
        store.add_grid_post(url, caption, comments, likes_raw)
        return f"grid post #{len(store.state['grid'])} saved: {url}"

    @controller.action(
        "Save one mutual follower (a person on 'Followed by ... others you follow'). "
        "TARGET MODE ONLY. handle without @, display_name as shown."
    )
    async def save_mutual(handle: str, display_name: str) -> str:
        store.add_mutual(handle, display_name)
        return f"mutual #{len(store.state['mutuals'])} saved: @{handle}"

    @controller.action(
        "Save one tagged post URL (just the URL path, no captions needed)."
    )
    async def save_tagged(url: str) -> str:
        store.add_tagged(url)
        return f"tagged #{len(store.state['tagged'])} saved"

    @controller.action(
        "Save one saved post URL. SELF MODE ONLY. Just the URL path."
    )
    async def save_saved(url: str) -> str:
        store.add_saved(url)
        return f"saved #{len(store.state.get('saved') or [])} saved"

    @controller.action(
        "Save one account from the Following modal. handle without @, display_name as shown."
    )
    async def save_following(handle: str, display_name: str) -> str:
        store.add_following(handle, display_name)
        return f"following #{len(store.state['following'])} saved: @{handle}"

    return controller


# ---------- task prompts (REELS FIRST) ----------
GLOBAL_RULES = """
HARD RULES (apply to every step):
- IGNORE the Explore feed, "Suggested Posts", "Suggested for you", and any popup or
  banner that says "Open in app", "See notifications", "Turn on notifications",
  "Log in to see more", or asks to save login info. Dismiss them and keep going.
- Do NOT click on stories, ads, or sidebars. Stay on the profile / reels / tagged
  surfaces only.
- If a surface fails to load after 2 attempts, SKIP it and move on. Never get stuck.
- "Most recent" means top-left of the grid, top of the reels tab, etc.
- URLs in path form: "/p/ABC123/" or "/reel/XYZ/" (no domain).
- CHECKPOINT EVERYTHING. Call the save_* action immediately after extracting each
  piece of data. Do not batch — save_header right after reading the header, save_reel
  right after each reel, save_highlight right after closing each highlight. If the
  loop dies, only the data you saved is preserved.
"""


def _self_task(handle: str) -> str:
    return f"""You are scraping the Instagram account @{handle} (the logged-in user's own account).

Go to https://www.instagram.com/{handle}/ — collect THREE surfaces, in this order:

**STEP 1: REPOSTS TAB — THE MAIN EVENT. THIS IS NOT THE REELS TAB.**
On an Instagram profile there is a row of tab icons under the bio: Posts (grid),
Reels (play-arrow), REPOSTS (circular-arrows / repost icon), Tagged.
The REPOSTS tab is where things the user has reposted from OTHER creators live.
This is what we need — what they share/boost is the highest-signal taste data.

Click the REPOSTS tab on /{handle}/. It's distinct from the Reels tab and from
the grid. If you can't find a Reposts tab on the profile, try the Reels tab
INSTEAD as a fallback (some accounts route reposts through Reels).

**TARGET: 20 reposts minimum, 30 max** — collect AT LEAST 20 if they exist;
stop at 30. Scroll to load more if the visible set is smaller. Only stop short
of 20 if the tab genuinely has fewer items.

Per repost: URL path, caption, creator (the ORIGINAL POSTER's @handle, not
@{handle} — distinguishing the original creator is the whole point), audio
track name.
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

Finish when done or by step 50. Everything is on disk via save_* calls.
"""


def _target_task(handle: str) -> str:
    return f"""You are scraping the Instagram account @{handle} (someone else — vibe check for the user).

Go to https://www.instagram.com/{handle}/.

**FIRST: privacy gate.**
If you see "This Account is Private" or "This profile is private" and there is NO
visible content beyond the header, call **mark_private**, then call **save_header**
with whatever header data is visible, and STOP. Don't try to bypass.

Otherwise collect surfaces in THIS ORDER:

**STEP 1 (CRITICAL): REPOSTS TAB — the main event. NOT the Reels tab.**
On the profile, find the row of tab icons: Posts, Reels, REPOSTS (circular-arrows
icon), Tagged. Click the REPOSTS tab. That's what they've boosted from other
creators — highest-signal taste data.

If a Reposts tab is not visible (smaller/older accounts), fall back to the Reels
tab.

**TARGET: 20 reposts minimum, 30 max** — at least 20 if they exist, stop at 30.
Scroll to load more.

Per repost: URL, caption, creator (the ORIGINAL POSTER's @handle), audio track.
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

Don't touch mutuals, tagged, saved, or Following — keep it tight.

{GLOBAL_RULES}

Finish when done or by step 50. Everything is on disk via save_* calls.
"""


# ---------- agent runners ----------
def _browser() -> Browser:
    # Bundled chromium (no channel="chrome") avoids single-instance collision
    # with the user's normal Chrome.
    profile = BrowserProfile(
        user_data_dir=str(PROFILE_DIR),
        headless=False,
    )
    return Browser(browser_profile=profile)


async def _run(task: str, mode: str, handle: str, narrate: NarrateCb = None) -> dict:
    out_path = OUT_DIR / f"{mode}_{handle}.json"
    store = CheckpointStore(out_path, mode, handle)
    browser = _browser()
    llm = ChatAnthropic(model=MODEL, timeout=LLM_TIMEOUT_S)
    controller = _make_controller(store)
    agent = Agent(
        task=task,
        llm=llm,
        browser=browser,
        controller=controller,
        register_new_step_callback=_make_callback(narrate),
        max_actions_per_step=5,
        max_failures=MAX_FAILURES,
        use_vision=True,
    )
    try:
        await agent.run()
    except Exception as e:
        print(f">>> agent.run() raised {type(e).__name__}: {e}")
    # Whatever happened, the file on disk has everything that got checkpointed.
    return json.loads(out_path.read_text(encoding="utf-8"))


def _write(data: dict, mode: str, handle: str) -> dict:
    # The checkpoint store already wrote the file. This is just for the log line
    # and to return the canonical dict.
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
    """Open browser-use agent for a follow-up question. Returns prose evidence."""
    task = f"""You are investigating Instagram for a follow-up question about @{base_handle}.

QUESTION: {query}

Start at https://www.instagram.com/{base_handle}/. You may navigate to other
profiles they interact with (frequent commenters, tagged accounts, people they
repost). Read posts, captions, comments, and reels as needed.

{GLOBAL_RULES}

Return your answer as PROSE EVIDENCE — not JSON. Cite specific posts (URL path),
specific commenters (@handle), specific reels, captions, or audio tracks you saw.
If you couldn't find an answer, say so plainly and describe what you did look at.
Keep it under ~400 words. No speculation beyond what's visible.
"""
    browser = _browser()
    llm = ChatAnthropic(model=MODEL, timeout=LLM_TIMEOUT_S)
    agent = Agent(
        task=task,
        llm=llm,
        browser=browser,
        register_new_step_callback=_make_callback(narrate),
        max_actions_per_step=5,
        max_failures=MAX_FAILURES,
        use_vision=True,
    )
    history = await agent.run()
    return (history.final_result() or "").strip() or "(no evidence found)"


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3 or sys.argv[1] not in ("self", "target"):
        print("usage: python agent_scrape.py [self|target] <handle>")
        sys.exit(1)
    (scrape_self if sys.argv[1] == "self" else scrape_target)(sys.argv[2])
