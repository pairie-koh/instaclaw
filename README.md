# instaclaw

Pre-DM recon, agent-driven. You point it at your own Instagram and it tells
you what your feed is actually saying about you. Then you point it at someone
you're about to DM and it tells you whether you'd vibe, whether they're
likely single, and gives you three openers tied to specific posts they
reposted.

Runs on [kuri](https://github.com/justrach/kuri) (v0.4.5+) for the browser and
the [codegraff](https://codegraff.com) agent SDK for the brain.

Two modes:

- **Aura readout** (self) — the agent reads your Reposts tab, story highlights,
  grid, and bio, and writes you a readout in the voice of a friend who's been
  paying attention. The kind of thing you screenshot to the group chat.

- **Vibe check** (target) — same scrape on someone else, then a cross-profile
  compatibility readout against your saved aura. Likely-single with a
  probability and cited signals, mutual ground, three openers, yellow flags.

After a readout lands there's a chat box. Ask follow-ups ("are they actually
single tho", "what's their deal with @x"). The agent answers from the cached
scrape, and re-investigates the profile through the browser when it needs to.

## How it works

instaclaw drives the **codegraff forge agent** in-process (the `codegraff`
Python SDK). The agent does the Instagram work itself by calling tools from a
local **kuri MCP server** (`kuri_mcp.py`):

```
your browser  →  uvicorn (localhost:8000)
                     |
                     ├─ /scrape  →  codegraff forge agent  (graff.chat)
                     |                 │ calls kuri MCP tools:
                     |                 │   navigate / snap / click / screenshot
                     |                 │   save_reel / save_highlight / save_* ...
                     |                 ▼
                     |              kuri_mcp.py  →  kuri  →  Chrome  →  instagram.com
                     |                 │ save_* tools flush out/{stem}.json as they go
                     |                 ▼
                     ├─ analyze.py  →  graff.chat  (aura / vibe → JSON readout)
                     |
                     └─ /chat  →  graff.chat  (answers from cache; browses via the
                                  same kuri MCP tools when it needs fresh data)
```

- **`cg_agent.py`** wraps the SDK: `graff.chat(prompt)` yields an event stream
  (`TaskReasoning` / `ToolCallStart` / `TaskMessage`) that's adapted to
  instaclaw's live SSE narration. The model is `mimo-v2.5` over the codegraff
  gateway — omnimodal, so the screenshot tool can read overlay text.
- **`kuri_mcp.py`** exposes kuri's browser control plus the `save_*` checkpoint
  tools over MCP (stdio). Each `save_*` writes the in-progress scrape JSON to
  disk immediately, so a dead agent loop still leaves everything collected so
  far. The `screenshot` tool captures a PNG, routes it through the omnimodal
  model over the gateway, and returns a text description (MCP results are text).
- **`register_mcp.py`** registers the kuri server in forge's user-level config
  (`~/forge/.mcp.json`). It has to be user-level: forge rejects a project-local
  `.mcp.json` headlessly (codegraff#152).

## Platform

The `codegraff` SDK is a native PyO3 wheel. 0.1.1 ships wheels for **macOS
(arm64 + x86_64) and Windows (amd64) on CPython 3.13**, **Linux (aarch64 +
x86_64) on CPython 3.9**, plus an sdist (build-from-source needs a Rust
toolchain). `setup.command` targets Python 3.13 on macOS; on Windows, use
Python 3.13 with kuri running in WSL.

## Install (macOS)

```bash
./setup.command
```

Creates a Python 3.13 `.venv-cg`, installs requirements (codegraff, mcp, the
kuri client deps, playwright for share-card PNGs), registers the kuri MCP
server in forge, and prompts for your `CODEGRAFF_API_KEY` (a `cg_sk_` key from
https://codegraff.com/dashboard/keys) which is stored in `.env`.

You also need kuri running with a managed Chrome logged in to Instagram — see
the kuri docs. (kuri ships Linux binaries; on macOS run it natively.)

## Run

```bash
./instaclaw.command
```

The server starts on `http://localhost:8000` and your browser opens it. Save
your own handle in settings, then "Scrape my vibe" for your aura, or type a
handle + "Vibe check" for a compatibility readout.

A full scrape is an agent loop driving the browser through ~20 reposts, a few
highlights and grid posts. `mimo-v2.5` is a reasoning model, so it's not fast —
budget tens of minutes per profile. Everything is checkpointed to
`out/{mode}_{handle}.json` as it goes.

## Architecture

- `server.py` — FastAPI app, SSE streaming, SQLite job persistence
- `cg_agent.py` — codegraff SDK driver: `graff.chat()` → narration events + final text
- `kuri_mcp.py` — kuri-as-MCP server (browser tools + `save_*` checkpoints + vision)
- `register_mcp.py` — idempotently registers kuri in `~/forge/.mcp.json`
- `agent_scrape.py` — scrape task prompts; hands them to the agent, reads checkpoints
- `analyze.py` — aura / vibe prompts (`graff.chat` → JSON)
- `kuri_client.py` — thin HTTP wrapper around kuri
- `render.py` / `screenshot.py` / `static/index.html` / `main.py` — unchanged

`out/` holds the scrape JSON files and the SQLite DB. `.agent-scratch/` is a
throwaway cwd for the forge agent's file tools (kept away from the repo).

## Configuration

`.env`:

- `CODEGRAFF_API_KEY` — required. A `cg_sk_` key from
  https://codegraff.com/dashboard/keys. (`CG_API_KEY` also works.)
- `INSTACLAW_MODEL` — default `mimo-v2.5`, the only codegraff alias that accepts
  image input (`input_modalities: ["text","image"]`), which the screenshot tool
  needs. The other aliases are text-only.
- `INSTACLAW_MAX_TOKENS` — default 24000. `mimo-v2.5` is a reasoning model and
  spends tokens thinking before answering, so the cap must be generous.
- `CODEGRAFF_BASE_URL` — gateway base, default `https://gateway.codegraff.com/v1`.
- `KURI_API_TOKEN` / `KURI_BASE_URL` — the local kuri server.

## Caveats

- The SDK runs forge's **default agent** (full shell / web / file tools); there's
  no per-call toolset restriction (codegraff#153), so instaclaw constrains the
  agent by prompt to use only the kuri tools, and readouts go through a tolerant
  `{...}` extractor since the agent isn't a JSON-mode endpoint. Less
  deterministic than a hand-rolled tool loop.

## Future work

See `FUTURE.md`.
