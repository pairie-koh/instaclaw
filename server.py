# Run: uvicorn server:app --host 127.0.0.1 --port 8000
"""FastAPI backend for instaclaw — local-only IG vibe-check web app.

Wraps agent_scrape / analyze / render / screenshot behind an HTTP + SSE API.
Single-user, in-memory job queue, SQLite persistence. No auth, no CORS.
"""
import asyncio
import json
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import os

import aiosqlite
import cg_agent
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

import agent_scrape
import analyze
import render as render_mod
import screenshot as screenshot_mod

ROOT = Path(__file__).parent
OUT_DIR = ROOT / "out"
OUT_DIR.mkdir(exist_ok=True)
STATIC_DIR = ROOT / "static"
DB_PATH = OUT_DIR / "instaclaw.db"


# ---------- job state (in-memory) ----------
@dataclass
class JobState:
    job_id: str
    kind: str          # "self" | "target"
    handle: str
    status: str = "pending"   # pending|scraping|analyzing|done|error
    created_at: str = ""
    scrape: Optional[dict] = None
    readout: Optional[dict] = None
    error: Optional[str] = None
    # For target jobs: which self-handle to use for compatibility, and the data
    # collected if we had to scrape self first. self_scrape lives in memory only —
    # it gets persisted under its own kind="self" job row by _scrape_worker.
    self_handle: Optional[str] = None
    self_scrape: Optional[dict] = None
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    loop: Optional[asyncio.AbstractEventLoop] = None


JOBS: dict[str, JobState] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------- SQLite ----------
SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    kind TEXT,
    handle TEXT,
    status TEXT,
    scrape_json TEXT,
    readout_json TEXT,
    error TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT,
    role TEXT,
    content TEXT,
    evidence TEXT,
    created_at TEXT
);
"""


async def db_init():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
        await db.commit()


async def db_upsert_job(job: JobState):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO jobs "
            "(job_id, kind, handle, status, scrape_json, readout_json, error, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (job.job_id, job.kind, job.handle, job.status,
             json.dumps(job.scrape) if job.scrape else None,
             json.dumps(job.readout) if job.readout else None,
             job.error, job.created_at),
        )
        await db.commit()


async def db_load_job(job_id: str) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await db.execute_fetchall(
            "SELECT * FROM jobs WHERE job_id = ?", (job_id,))
        if not row:
            return None
        r = row[0]
        return {
            "job_id": r["job_id"], "kind": r["kind"], "handle": r["handle"],
            "status": r["status"], "created_at": r["created_at"], "error": r["error"],
            "scrape": json.loads(r["scrape_json"]) if r["scrape_json"] else None,
            "readout": json.loads(r["readout_json"]) if r["readout_json"] else None,
        }


async def db_recent_self_readout(days: int = 30, handle: Optional[str] = None) -> Optional[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if handle:
            rows = await db.execute_fetchall(
                "SELECT readout_json FROM jobs WHERE kind = 'self' AND status = 'done' "
                "AND handle = ? AND created_at >= ? AND readout_json IS NOT NULL "
                "ORDER BY created_at DESC LIMIT 1", (handle, cutoff))
        else:
            rows = await db.execute_fetchall(
                "SELECT readout_json FROM jobs WHERE kind = 'self' AND status = 'done' "
                "AND created_at >= ? AND readout_json IS NOT NULL "
                "ORDER BY created_at DESC LIMIT 1", (cutoff,))
        if not rows:
            return None
        return json.loads(rows[0]["readout_json"])


async def db_recent_self_scrape(days: int = 30, handle: Optional[str] = None) -> Optional[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if handle:
            rows = await db.execute_fetchall(
                "SELECT scrape_json FROM jobs WHERE kind = 'self' AND status = 'done' "
                "AND handle = ? AND scrape_json IS NOT NULL AND created_at >= ? "
                "ORDER BY created_at DESC LIMIT 1", (handle, cutoff))
        else:
            rows = await db.execute_fetchall(
                "SELECT scrape_json FROM jobs WHERE kind = 'self' AND status = 'done' "
                "AND scrape_json IS NOT NULL AND created_at >= ? "
                "ORDER BY created_at DESC LIMIT 1", (cutoff,))
        if not rows:
            return None
        return json.loads(rows[0]["scrape_json"])


async def db_history() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            "SELECT job_id, kind, handle, readout_json, created_at "
            "FROM jobs ORDER BY created_at DESC LIMIT 100")
        out = []
        for r in rows:
            headline = None
            if r["readout_json"]:
                try:
                    headline = json.loads(r["readout_json"]).get("headline")
                except Exception:
                    pass
            out.append({"job_id": r["job_id"], "kind": r["kind"],
                        "handle": r["handle"], "headline": headline,
                        "created_at": r["created_at"]})
        return out


async def db_save_chat(job_id: str, role: str, content: str, evidence: Optional[str] = None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO chat_messages (job_id, role, content, evidence, created_at) "
            "VALUES (?,?,?,?,?)",
            (job_id, role, content, evidence, _now_iso()))
        await db.commit()


async def db_load_chat(job_id: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            "SELECT role, content FROM chat_messages WHERE job_id = ? ORDER BY id",
            (job_id,))
        return [{"role": r["role"], "content": r["content"]} for r in rows]


# ---------- SSE event helpers ----------
def _push(job: JobState, event: dict):
    """Push from the worker thread back into the asyncio queue on the main loop."""
    if job.loop is None:
        return
    asyncio.run_coroutine_threadsafe(job.queue.put(event), job.loop)


def sse(event: dict) -> dict:
    return {"event": "message", "data": json.dumps(event)}


# ---------- scrape worker (runs in BackgroundTasks via to_thread) ----------
def _scrape_worker(job_id: str):
    """Synchronous worker — agent_scrape.scrape_self/target call asyncio.run internally."""
    job = JOBS[job_id]

    def narrate_cb(step: int, thinking: str, next_goal: str):
        _push(job, {"type": "narration", "step": step,
                    "thinking": thinking, "next_goal": next_goal})

    try:
        # SELF MODE — one scrape, aura readout.
        if job.kind == "self":
            job.status = "scraping"
            _push(job, {"type": "status", "status": "scraping", "phase": "self"})
            data = agent_scrape.scrape_self(job.handle, narrate=narrate_cb)
            job.scrape = data

            job.status = "analyzing"
            _push(job, {"type": "status", "status": "analyzing"})
            readout = analyze.aura(data)
            job.readout = readout
            job.status = "done"
            if job.loop:
                asyncio.run_coroutine_threadsafe(db_upsert_job(job), job.loop).result()
            _push(job, {"type": "done", "readout": readout})
            return

        # TARGET MODE — compatibility flow.
        # 1. Pull (or scrape) self data for the requested self_handle.
        prior_self_scrape: Optional[dict] = None
        prior_self_readout: Optional[dict] = None
        if job.self_handle and job.loop:
            fut = asyncio.run_coroutine_threadsafe(
                _gather_prior_self(job.self_handle), job.loop)
            prior_self_scrape, prior_self_readout = fut.result()

        if job.self_handle and not prior_self_scrape:
            # No cached self data — scrape self FIRST.
            _push(job, {"type": "status", "status": "scraping", "phase": "self"})
            _push(job, {"type": "narration", "step": 0,
                        "thinking": f"No recent scrape on file for @{job.self_handle} — scraping you first so we can do a real compatibility read.",
                        "next_goal": f"scrape @{job.self_handle}"})
            self_data = agent_scrape.scrape_self(job.self_handle, narrate=narrate_cb)
            job.self_scrape = self_data
            # Persist the self scrape as its own job row so it shows up in history
            # and future target runs can reuse it.
            if job.loop:
                self_job_id = uuid.uuid4().hex[:12]
                self_job = JobState(job_id=self_job_id, kind="self", handle=job.self_handle,
                                    status="done", scrape=self_data, created_at=_now_iso(),
                                    loop=job.loop)
                asyncio.run_coroutine_threadsafe(db_upsert_job(self_job), job.loop).result()
            prior_self_scrape = self_data
            prior_self_readout = None

        # 2. Scrape target.
        job.status = "scraping"
        _push(job, {"type": "status", "status": "scraping", "phase": "target"})
        target_data = agent_scrape.scrape_target(job.handle, narrate=narrate_cb)
        job.scrape = target_data

        # 3. Vibe analysis.
        job.status = "analyzing"
        _push(job, {"type": "status", "status": "analyzing"})
        self_for_vibe = prior_self_scrape or {
            "handle": "unknown", "header": {}, "grid": [], "reels": [], "highlights": []}
        readout = analyze.vibe(self_for_vibe, target_data, self_aura=prior_self_readout)
        job.readout = readout
        job.status = "done"
        if job.loop:
            asyncio.run_coroutine_threadsafe(db_upsert_job(job), job.loop).result()
        _push(job, {"type": "done", "readout": readout})

    except Exception as e:
        job.status = "error"
        job.error = f"{type(e).__name__}: {e}"
        if job.loop:
            asyncio.run_coroutine_threadsafe(db_upsert_job(job), job.loop).result()
        _push(job, {"type": "error", "message": job.error})
    finally:
        _push(job, {"type": "__end__"})


async def _gather_prior_self(handle: Optional[str] = None) -> tuple[Optional[dict], Optional[dict]]:
    return await db_recent_self_scrape(handle=handle), await db_recent_self_readout(handle=handle)


# ---------- FastAPI app ----------
app = FastAPI(title="instaclaw")

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.on_event("startup")
async def _startup():
    await db_init()


@app.get("/", response_class=HTMLResponse)
async def index():
    idx = STATIC_DIR / "index.html"
    if not idx.exists():
        return HTMLResponse("<h1>instaclaw</h1><p>no static/index.html yet</p>")
    return HTMLResponse(idx.read_text(encoding="utf-8"))


# ---------- /scrape ----------
class ScrapeBody(BaseModel):
    kind: str
    handle: str
    # For target jobs: your own handle. If provided and there's no recent self
    # scrape for it, we run a self scrape first so the vibe analysis has
    # something to compare against.
    self_handle: Optional[str] = None


@app.post("/scrape")
async def scrape(body: ScrapeBody, background: BackgroundTasks):
    if body.kind not in ("self", "target"):
        raise HTTPException(400, "kind must be 'self' or 'target'")
    handle = body.handle.strip().lstrip("@")
    if not handle:
        raise HTTPException(400, "empty handle")
    self_handle = (body.self_handle or "").strip().lstrip("@") or None
    job_id = uuid.uuid4().hex[:12]
    job = JobState(job_id=job_id, kind=body.kind, handle=handle,
                   self_handle=self_handle,
                   created_at=_now_iso(), loop=asyncio.get_event_loop())
    JOBS[job_id] = job
    await db_upsert_job(job)
    background.add_task(asyncio.to_thread, _scrape_worker, job_id)
    return {"job_id": job_id}


# ---------- /jobs/{id} ----------
def _job_to_response(job: JobState) -> dict:
    headline = job.readout.get("headline") if job.readout else None
    return {
        "job_id": job.job_id, "status": job.status, "kind": job.kind,
        "handle": job.handle, "created_at": job.created_at,
        "scrape": job.scrape, "readout": job.readout,
        "error": job.error, "headline": headline,
    }


@app.get("/jobs/{job_id}")
async def get_job(job_id: str):
    if job_id in JOBS:
        return _job_to_response(JOBS[job_id])
    row = await db_load_job(job_id)
    if not row:
        raise HTTPException(404, "no such job")
    row["headline"] = row["readout"].get("headline") if row["readout"] else None
    return row


# ---------- /jobs/{id}/stream  (SSE for scrape progress) ----------
@app.get("/jobs/{job_id}/stream")
async def stream_job(job_id: str, request: Request):
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "no such job")

    async def gen():
        yield sse({"type": "status", "status": job.status})
        if job.status == "done" and job.readout:
            yield sse({"type": "done", "readout": job.readout})
            return
        if job.status == "error":
            yield sse({"type": "error", "message": job.error or ""})
            return
        while True:
            if await request.is_disconnected():
                break
            try:
                evt = await asyncio.wait_for(job.queue.get(), timeout=20)
            except asyncio.TimeoutError:
                yield {"event": "ping", "data": "{}"}
                continue
            if evt.get("type") == "__end__":
                break
            yield sse(evt)

    return EventSourceResponse(gen())


# ---------- /chat (SSE) ----------
class ChatBody(BaseModel):
    job_id: str
    message: str
    # Optional pronouns for the target and self — frontend can pass from settings.
    target_pronouns: Optional[str] = None
    self_pronouns: Optional[str] = None


def _chat_system(job: dict, target_pronouns: Optional[str] = None,
                 self_pronouns: Optional[str] = None) -> str:
    kind = job["kind"]
    handle = job["handle"]
    scrape = job.get("scrape") or {}
    readout = job.get("readout") or {}
    tp = target_pronouns or "they/them"
    sp = self_pronouns or "they/them"
    return f"""You are the user's sharpest friend, answering follow-up questions about
@{handle}'s Instagram. You already wrote the readout below and have the full scrape.

TARGET MODE: {kind}
TARGET HANDLE: @{handle}
TARGET PRONOUNS: {tp} — use these throughout.
USER PRONOUNS: {sp}

CACHED SCRAPE:
```json
{json.dumps(scrape, ensure_ascii=False)[:80000]}
```

PRIOR READOUT YOU WROTE:
```json
{json.dumps(readout, ensure_ascii=False)[:20000]}
```

Rules:
- Answer from the cached data when possible. Be specific, cite captions, comments, reels, audio.
- If the answer truly requires fresh IG browsing (e.g. "who's that person in their stories",
  "what's @x's deal", "are they at the same event as @y"), use the kuri browser tools to
  investigate. Don't browse for things you can already answer from the cache above.
- Tone matches the readout: observational, specific, faintly funny. Never mean.
- Keep replies under ~200 words unless the user asks for more.
- NEVER use em-dashes (—) or en-dashes (–). Use periods, commas, parentheses, colons, or line breaks instead.
- Format the reply for legibility: use \\n\\n (double newline) for paragraph breaks, and **double asterisks** around the 2-3 key phrases you want bolded (the verdict line, a key @handle, a punchline). Each beat on its own paragraph, not a wall of prose.
"""


async def _chat_stream(body: ChatBody, request: Request):
    """SSE generator: the forge agent answers from the cached scrape/readout and
    can browse the target's IG via the kuri MCP tools when it needs fresh data."""
    job_row = await db_load_job(body.job_id)
    if job_row is None and body.job_id in JOBS:
        job_row = _job_to_response(JOBS[body.job_id])
    if job_row is None:
        yield sse({"type": "message", "content": "(no such job)"})
        yield sse({"type": "done"})
        return

    system = _chat_system(job_row, target_pronouns=body.target_pronouns,
                          self_pronouns=body.self_pronouns)
    history = await db_load_chat(body.job_id)
    convo = "\n".join(f"{m['role']}: {m['content']}" for m in history)
    await db_save_chat(body.job_id, "user", body.message)

    prompt = (
        system
        + (f"\n\nCONVERSATION SO FAR:\n{convo}" if convo else "")
        + f"\n\nUSER'S NEW MESSAGE:\n{body.message}\n\n"
        + "Reply now. Answer from the cached scrape and readout above when you can. "
        + "Only if you genuinely need fresh data should you use the kuri browser tools "
        + "to investigate the target's Instagram."
    )

    loop = asyncio.get_event_loop()
    narration_q: asyncio.Queue = asyncio.Queue()

    def narrate_cb(step: int, thinking: str, next_goal: str, lp=loop, q=narration_q):
        asyncio.run_coroutine_threadsafe(
            q.put({"type": "narration", "step": step,
                   "thinking": thinking, "next_goal": next_goal}), lp)

    yield sse({"type": "thinking", "content": "thinking..."})
    task = asyncio.create_task(asyncio.to_thread(cg_agent.run, prompt, narrate_cb))
    while not task.done():
        try:
            yield sse(await asyncio.wait_for(narration_q.get(), timeout=1.0))
        except asyncio.TimeoutError:
            pass
        if await request.is_disconnected():
            task.cancel()
            return
    while not narration_q.empty():
        yield sse(narration_q.get_nowait())

    final_text = (await task) or "(no reply)"
    yield sse({"type": "message", "content": final_text})
    await db_save_chat(body.job_id, "assistant", final_text)
    yield sse({"type": "done"})


@app.post("/chat")
async def chat(body: ChatBody, request: Request):
    return EventSourceResponse(_chat_stream(body, request))


# ---------- /setup ----------
# Login flow: kuri owns the Chrome window (started non-headless by instaclaw.bat / .command).
# /setup/connect points that window at instagram.com so the user can log in.
# /setup/check asks kuri whether the current page shows a logged-in nav element.
def _kuri_navigate_to_ig() -> None:
    from kuri_client import Kuri
    k = Kuri()
    tab = k.first_tab()
    tab.goto("https://www.instagram.com/")


@app.post("/setup/connect")
async def setup_connect():
    try:
        await asyncio.to_thread(_kuri_navigate_to_ig)
        return {"status": "opened"}
    except Exception as e:
        raise HTTPException(503, f"kuri not reachable: {e}")


@app.get("/setup/check")
async def setup_check():
    from kuri_client import Kuri, KuriError
    try:
        k = Kuri()
        tab = k.first_tab()
        # IG renders avatar nav-link to /accounts/edit/ only when logged in.
        has_nav = tab.evaluate(
            "Boolean(document.querySelector(\"a[href*='/accounts/edit/']\") || "
            "document.querySelector(\"a[href$='/accounts/activity/']\") || "
            "document.querySelector(\"a[href*='/direct/inbox/']\"))"
        )
        return {"connected": bool(has_nav)}
    except KuriError:
        return {"connected": False}
    except Exception:
        return {"connected": False}


# ---------- /history ----------
@app.get("/history")
async def history():
    return {"jobs": await db_history()}


# ---------- /readout/{job_id}/... ----------
async def _readout_for(job_id: str) -> tuple[dict, Optional[str]]:
    """Return (readout, target_handle_or_none) for rendering."""
    job = JOBS.get(job_id)
    if job and job.readout:
        return job.readout, (job.handle if job.kind == "target" else None)
    row = await db_load_job(job_id)
    if not row or not row.get("readout"):
        raise HTTPException(404, "no readout for that job")
    target = row["handle"] if row["kind"] == "target" else None
    return row["readout"], target


@app.get("/readout/{job_id}/card.html", response_class=HTMLResponse)
async def readout_card(job_id: str):
    readout, target = await _readout_for(job_id)
    return HTMLResponse(render_mod.render(readout, target_handle=target))


@app.get("/readout/{job_id}/story.html", response_class=HTMLResponse)
async def readout_story(job_id: str):
    readout, target = await _readout_for(job_id)
    return HTMLResponse(render_mod.render_story(readout, target_handle=target))


@app.get("/readout/{job_id}/story.png")
async def readout_story_png(job_id: str):
    readout, target = await _readout_for(job_id)
    png_path = OUT_DIR / f"job_{job_id}.story.png"
    if not png_path.exists():
        html_path = OUT_DIR / f"job_{job_id}.story.html"
        html_path.write_text(render_mod.render_story(readout, target_handle=target),
                             encoding="utf-8")
        # screenshot is sync playwright — run off the event loop
        await asyncio.to_thread(screenshot_mod.screenshot, html_path, png_path, "story")
    return FileResponse(str(png_path), media_type="image/png")
