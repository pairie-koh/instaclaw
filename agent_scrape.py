"""Browser-use powered Instagram scraper. Claude drives Playwright by looking at screenshots.

Same output schema as scrape.py — analyze.py keeps working unchanged.
Keys: mode, handle, header, grid, reels, tagged, saved (self only), private (target only).
"""
import asyncio
import json
from pathlib import Path
from typing import Callable, Optional

from pydantic import BaseModel, Field

from browser_use import Agent, Browser, BrowserProfile, ChatAnthropic
from browser_use.browser.views import BrowserStateSummary
from browser_use.agent.views import AgentOutput

# Narrate callback signature used by the web layer: (step, thinking, next_goal).
NarrateCb = Optional[Callable[[int, str, str], None]]

ROOT = Path(__file__).parent
PROFILE_DIR = ROOT / ".chrome-profile"
OUT_DIR = ROOT / "out"
OUT_DIR.mkdir(exist_ok=True)
MODEL = "claude-opus-4-7"


# ---------- output schema (agent returns this verbatim via structured output) ----------
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


class ScrapeResult(BaseModel):
    mode: str
    handle: str
    private: bool = False
    header: Header = Field(default_factory=Header)
    grid: list[Post] = Field(default_factory=list)
    reels: list[Reel] = Field(default_factory=list)
    tagged: list[UrlOnly] = Field(default_factory=list)
    saved: Optional[list[UrlOnly]] = None


# ---------- narration callback: prints Claude's thinking to terminal during the demo ----------
def _narrate(state: BrowserStateSummary, output: AgentOutput, step: int) -> None:
    cs = output.current_state
    if cs.thinking:
        print(f">>> [{step}] {cs.thinking.strip()}")
    if cs.next_goal:
        print(f">>> [{step}] next: {cs.next_goal.strip()}")


def _make_callback(extra: NarrateCb):
    """Wrap stdout _narrate + an optional (step, thinking, next_goal) callback."""
    def cb(state: BrowserStateSummary, output: AgentOutput, step: int) -> None:
        _narrate(state, output, step)
        if extra is not None:
            cs = output.current_state
            try:
                extra(step, (cs.thinking or "").strip(), (cs.next_goal or "").strip())
            except Exception as e:
                print(f">>> [narrate-cb error] {e}")
    return cb


# ---------- shared task prompt fragments ----------
GLOBAL_RULES = """
HARD RULES (apply to every step):
- IGNORE the Explore feed, "Suggested Posts", "Suggested for you", and any popup or
  banner that says "Open in app", "See notifications", "Turn on notifications",
  "Log in to see more", or asks to save login info. Dismiss them and keep going.
- Do NOT click on stories, ads, or sidebars. Stay on the profile / reels / tagged /
  saved surfaces only.
- If a surface fails to load after 2 attempts, SKIP it and return the partial data
  for everything else you DID collect. Never crash. Never return nothing.
- "Most recent" means top-left of the grid, top of the reels tab, etc. Collect in
  visible order — do not reorder.
- For URLs, use the path form like "/p/ABC123/" or "/reel/XYZ/" (no domain).
"""

OUTPUT_SCHEMA = """
Return your final result as JSON matching this exact shape:
{
  "mode": "self" | "target",
  "handle": "<the handle string>",
  "private": false,                       // true ONLY for private target accounts
  "header": {
    "name":  "<display name>",
    "header_text": "<full bio text incl. links>",
    "stats_raw": ["<posts count>", "<followers>", "<following>"]
  },
  "grid":   [ {"url": "/p/.../", "caption": "...", "comments": ["...", ...], "likes_raw": "..."} ],
  "reels":  [ {"url": "/reel/.../", "caption": "...", "creator": "<@handle of original poster>", "audio": "<audio track name>"} ],
  "tagged": [ {"url": "/p/.../"} ],
  "saved":  [ {"url": "/p/.../"} ]        // SELF mode only — omit for target
}
"""


def _self_task(handle: str) -> str:
    return f"""You are scraping the Instagram account @{handle} (the logged-in user's own account).

Go to https://www.instagram.com/{handle}/ and collect the following surfaces, in order:

1. PROFILE HEADER — display name, full bio text, and the three stat numbers
   (posts / followers / following) as they appear on the page.

2. GRID — open each of the 15 MOST RECENT posts (top-left to right, row by row).
   For each post: URL path, caption, and the ~10 TOP comments visible (first ones
   shown when you open the post). Also grab the likes string if shown.

3. REELS TAB — go to /{handle}/reels/ and open each of the 20 MOST RECENT reels.
   *** REPOSTED REELS ARE THE HIGHEST-PRIORITY SIGNAL — DO NOT SKIP THEM. ***
   A repost shows another creator's handle at the top instead of @{handle}.
   For each reel capture: URL path, caption, creator handle (theirs OR the original
   poster for reposts), and the audio track name shown at the bottom.

4. TAGGED — go to /{handle}/tagged/ and collect the URL paths of the 10 most
   recent posts. URLs only, no need to open them.

5. SAVED — go to /{handle}/saved/all-posts/ and collect the URL paths of the 20
   most recently saved items. URLs only.

{GLOBAL_RULES}
{OUTPUT_SCHEMA}

Set mode="self", handle="{handle}", private=false. Saved must be populated.
"""


def _target_task(handle: str) -> str:
    return f"""You are scraping the PUBLIC Instagram account @{handle} (someone else — not the
logged-in user). You may be logged in, but treat this as public-only data.

Go to https://www.instagram.com/{handle}/.

FIRST: check if the account is private. If you see "This Account is Private" or
"This account is private. Follow to see their photos and videos." then STOP
after capturing the profile header and return:
  {{ "mode": "target", "handle": "{handle}", "private": true, "header": {{...}} }}

Otherwise collect (in order):

1. PROFILE HEADER — display name, full bio, posts/followers/following stats.

2. GRID — open each of the 15 MOST RECENT posts (top-left, row by row).
   Capture URL, caption, ~10 top comments, likes string.

3. REELS TAB — /{handle}/reels/, open the 20 MOST RECENT reels.
   *** REPOSTED REELS ARE THE HIGHEST-PRIORITY SIGNAL — DO NOT SKIP THEM. ***
   A repost shows another creator at the top instead of @{handle}.
   Per reel: URL, caption, creator handle, audio track name.

4. TAGGED — /{handle}/tagged/, URL paths of the 10 most recent. URLs only.

DO NOT touch the saved tab (you can't see someone else's saved).

{GLOBAL_RULES}
{OUTPUT_SCHEMA}

Set mode="target", handle="{handle}". Omit "saved" entirely.
"""


# ---------- agent runners ----------
def _browser() -> Browser:
    # Persistent profile keeps the IG login between runs. Windowed so the demo
    # audience can watch Claude work.
    profile = BrowserProfile(
        user_data_dir=str(PROFILE_DIR),
        headless=False,
        channel="chrome",
    )
    return Browser(browser_profile=profile)


def _to_dict(result: ScrapeResult, mode: str, handle: str) -> dict:
    d = result.model_dump()
    d["mode"] = mode
    d["handle"] = handle
    if mode == "target":
        d.pop("saved", None)
    if mode == "self" and d.get("saved") is None:
        d["saved"] = []
    return d


async def _run(task: str, mode: str, handle: str, narrate: NarrateCb = None) -> dict:
    browser = _browser()
    llm = ChatAnthropic(model=MODEL)
    agent = Agent(
        task=task,
        llm=llm,
        browser=browser,
        output_model_schema=ScrapeResult,
        register_new_step_callback=_make_callback(narrate),
        max_actions_per_step=5,
        use_vision=True,
    )
    history = await agent.run()
    result: Optional[ScrapeResult] = history.structured_output
    if result is None:
        # graceful partial: fall back to whatever the agent put in final_result text
        raw = history.final_result() or "{}"
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {}
        payload.setdefault("mode", mode)
        payload.setdefault("handle", handle)
        return payload
    return _to_dict(result, mode, handle)


def _write(data: dict, mode: str, handle: str) -> dict:
    out_path = OUT_DIR / f"{mode}_{handle}.json"
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
    llm = ChatAnthropic(model=MODEL)
    agent = Agent(
        task=task,
        llm=llm,
        browser=browser,
        register_new_step_callback=_make_callback(narrate),
        max_actions_per_step=5,
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
