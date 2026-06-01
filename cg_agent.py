"""Shared codegraff/forge agent driver for instaclaw.

Runs the forge agent in-process via the codegraff Python SDK and adapts its
event stream to instaclaw's narrate callback `(step, thinking, next_goal)`.
The agent's tools are forge's defaults plus the `kuri` MCP server registered
in ~/forge/.mcp.json (see kuri_mcp.py) — that's how it drives the browser.

Used by agent_scrape.py (scraping), analyze.py (aura/vibe), and server.py (chat).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).parent
# Forge's default tools include file/shell ops scoped to its cwd; point that at
# a throwaway dir so the agent can never touch the instaclaw repo.
SCRATCH = ROOT / ".agent-scratch"
SCRATCH.mkdir(exist_ok=True)

MODEL = os.environ.get("INSTACLAW_MODEL", "mimo-v2.5")
MAX_TOKENS = int(os.environ.get("INSTACLAW_MAX_TOKENS", "24000"))

# Agent selection (codegraff>=0.1.3). DEFAULT_AGENT is the full-tool built-in
# forge agent — the historical default every turn already ran as. SCRAPER_AGENT
# is the kuri-scoped custom agent (.forge/agents/kuri-scraper.md) used for
# scrapes so the nav loop is confined to the browser and can't wander into
# shell/file/web tools.
DEFAULT_AGENT = "forge"
SCRAPER_AGENT = "kuri-scraper"

NarrateCb = Optional[Callable[[int, str, str], None]]

_graff = None


def _load_env() -> None:
    envf = ROOT / ".env"
    if not envf.exists():
        return
    for line in envf.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def _agent():
    """Lazily build the one long-lived forge agent for this process."""
    global _graff
    if _graff is None:
        from codegraff import Graff
        import register_mcp
        _load_env()
        register_mcp.ensure_kuri_agent()  # idempotent: sync the agent into forge's global agents dir
        key = os.environ.get("CODEGRAFF_API_KEY") or os.environ.get("CG_API_KEY", "")
        kwargs = dict(cwd=str(SCRATCH), provider="codegraff", model=MODEL, max_tokens=MAX_TOKENS)
        if key:
            kwargs["api_key"] = key  # BYOK constructor; needs codegraff>=0.1.1 (fix for #151)
        _graff = Graff(**kwargs)
    return _graff
def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return content.get("text") or content.get("content") or ""
    return ""


def _short(args) -> str:
    if isinstance(args, str):
        s = args
    else:
        try:
            s = json.dumps(args)
        except Exception:  # noqa
            s = str(args)
    return s[:120] + ("…" if len(s) > 120 else "")


def run(prompt: str, narrate: NarrateCb = None, model: Optional[str] = None,
        agent_id: Optional[str] = None) -> str:
    """Run one agent turn to completion (blocking; releases the GIL while waiting).

    Streams reasoning + tool calls to `narrate(step, thinking, next_goal)` and
    returns the final assistant markdown text (the agent's answer). For scrapes
    the return is ignored — the data is whatever the kuri save_* tools flushed to
    disk; for analyze/chat it's the readout / reply text.
    """
    graff = _agent()
    step = 0
    last_reasoning = ""
    chunks: list[str] = []
    # codegraff>=0.1.3 per-call agent selection is sticky across chats on the
    # long-lived Graff, so always pass an explicit agent: the kuri-scoped
    # scraper for scrapes, else reset to the full-tool "forge" default so a
    # prior scrape's tool scope can't leak into a later analyze/chat turn.
    for ev in graff.chat(prompt, model=model or MODEL, agent=agent_id or DEFAULT_AGENT):
        t = ev.type
        if t == "TaskReasoning":
            last_reasoning = _text_of(ev.data.get("content")) or last_reasoning
        elif t == "ToolCallStart":
            step += 1
            tc = ev.data.get("tool_call") or {}
            name = (tc.get("name") or tc.get("tool_name") or "").replace("mcp_kuri_tool_", "")
            goal = f"{name}({_short(tc.get('arguments'))})"
            if narrate is not None:
                try:
                    narrate(step, last_reasoning[:300], goal)
                except Exception as e:  # noqa
                    print(f">>> [narrate-cb error] {e}")
        elif t == "TaskMessage":
            c = ev.data.get("content") or {}
            if c.get("kind") == "Markdown":
                chunks.append(c.get("text", ""))
    return "".join(chunks).strip()
