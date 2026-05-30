# instaclaw

Pre-DM recon, agent-driven. You point it at your own Instagram and it tells
you what your feed is actually saying about you. Then you point it at someone
you're about to DM and it tells you whether you'd vibe, whether they're
likely single, and gives you three openers tied to specific posts they
reposted.

This branch runs on [kuri](https://github.com/justrach/kuri) (v0.4.5+). For
the previous browser-use + Playwright version, see `main`.

Two modes:

- **Aura readout** (self) — Claude reads your Reposts tab, story highlights,
  grid, and bio, and writes you a readout in the voice of a friend who's
  been paying attention. Headline, taste cluster, friend-group role, what
  you actually project. The kind of thing you screenshot to the group chat.

- **Vibe check** (target) — Same scrape on someone else, then a cross-
  profile compatibility readout against your saved aura. Likely single
  with a probability and cited signals, mutual ground, three openers
  drafted off actual posts they reposted, yellow flags written as
  observations not warnings.

After a readout lands, there's a chat box at the bottom of the UI. Ask
follow-ups ("are they actually single tho", "what's their deal with
@x"). The agent re-investigates the target's profile with a focused
query and answers with cited evidence.

## How it works

```
your browser  →  uvicorn (localhost:8000)
                     |
                     ├─ /scrape  →  Claude Sonnet 4.6 tool-use loop
                     |                  ↓ (navigate / snap / click / screenshot / save_*)
                     |               kuri_client  →  HTTP  →  kuri server (WSL)
                     |                                          ↓ (CDP)
                     |                                       Chrome  →  instagram.com
                     |
                     ├─ analyze.py  →  Claude Sonnet 4.6
                     |                  (aura prompt for self, vibe prompt for target
                     |                   with cached self as comparison context)
                     |
                     └─ /chat  →  Anthropic tool-use loop with fetch_more tool,
                                  fetch_more kicks off focused_scrape (another
                                  kuri-driven Claude loop returning prose evidence)
```

Kuri runs in WSL on Windows and natively on macOS / Linux. It exposes
Chrome's CDP via an HTTP API and serves compact a11y snapshots that the
agent uses instead of vision-heavy screenshots. The Claude tool loop has
both browser tools (`navigate`, `snap_interactive`, `click`, `screenshot`)
and `save_*` tools (`save_reel`, `save_highlight`, `save_grid_post`,
`save_header`, etc.) — each `save_*` writes the in-progress scrape JSON to
disk immediately, so if the loop dies the file already contains everything
collected so far. The analyze layer is a single Claude call per readout
that turns the scrape JSON into a written card.

Output is HTML rendered server-side and served into an iframe on the
single-page frontend. SSE streams the agent's live narration so you can
watch it think.

## Install

Windows:

```cmd
setup.bat
```

This requires WSL2 with an Ubuntu distro (kuri only ships Linux binaries).
The script will check WSL is available, create a Python `.venv`, pip-install
requirements, install Google Chrome inside WSL (for kuri's managed browser),
install kuri via its `install.sh`, and prompt for your `ANTHROPIC_API_KEY`
which gets stored in `.env`. A `KURI_API_TOKEN` is also generated.

Note: at time of writing, kuri's stable install channel ships v0.4.4, but
the IG CDP fix landed in v0.4.5 (issue #172 / commit `648fe344`). If you see
recurring 502 "CDP command failed" errors after navigating to instagram.com,
build v0.4.5 from source — instructions are in `setup.bat`'s output.

macOS:

```bash
./setup.command
```

Same flow.

## Run

```cmd
instaclaw.bat        # Windows
./instaclaw.command  # macOS
```

The server starts on `http://localhost:8000` and your default browser
opens it automatically. First time you'll be prompted to connect Instagram —
that opens a separate Chromium window pointed at instagram.com. Log in,
close that window when you see your feed, and the setup modal closes
automatically.

In the settings modal, save your own handle so vibe-checks know who to
compare against. Then:

- Click **"Scrape my vibe"** in settings to get your aura readout.
- Type any handle in the main input + click **Vibe check** to get a
  compatibility readout (it uses your cached aura as the reference).

A full scrape runs about 25-35 minutes per profile (Claude driving the
browser through ~20 reposts, 5 highlights, 5 grid posts, header). Cost is
typically $1-2 per scrape on Sonnet 4.6.

## Architecture

- `server.py` — FastAPI app, SSE streaming, SQLite job persistence
- `agent_scrape.py` — Claude tool-use loop, custom save_* tools, task
  prompts for self vs target, driven through kuri
- `kuri_client.py` — thin HTTP wrapper around kuri (kuri runs in WSL,
  client talks to localhost:8080)
- `analyze.py` — aura / vibe prompts + analyze functions
- `render.py` — HTML card rendering with markdown-bold support
- `screenshot.py` — chromium CLI screenshot for sharing cards as PNGs
- `static/index.html` — single-page frontend (no framework)
- `main.py` — CLI orchestrator if you want to run without the server

`out/` holds the scrape JSON files and the SQLite DB (`instaclaw.db`).
Kuri persists its Chrome profile in WSL at `/root/.kuri/chrome-profile/`,
which is how the IG login carries over between runs.

## Configuration

`.env`:

- `ANTHROPIC_API_KEY` — required
- `INSTACLAW_MODEL` — defaults to `claude-sonnet-4-6`. Set to
  `claude-haiku-4-5-20251001` for ~3× cheaper / ~2× faster scrapes at
  some reliability cost.
- `INSTACLAW_MAX_TURNS` — agent loop step ceiling. Default 60.
- `INSTACLAW_LLM_TIMEOUT` — Anthropic client timeout in seconds. Default 180.
- `KURI_API_TOKEN` — bearer token for the local kuri server. Auto-generated
  by `setup.bat`.
- `KURI_BASE_URL` — defaults to `http://127.0.0.1:8080`. Override if you're
  running kuri on a different host.

## Other branches

- `main` — earlier production version using browser-use + Playwright. Slower
  and more expensive per scrape, but doesn't need WSL on Windows.
- `kuri-experiment` — earlier WIP kuri swap, superseded by this branch.

## Future work

See `FUTURE.md` for deferred items (mutuals scrape, compile-once-replay
for sub-30s subsequent scrapes, Haiku swap, kuri re-enablement).
