# instaclaw

Pre-DM recon, agent-driven. You point it at your own Instagram and it tells
you what your feed is actually saying about you. Then you point it at someone
you're about to DM and it tells you whether you'd vibe, whether they're
likely single, and gives you three openers tied to specific posts they
reposted.

Runs on [kuri](https://github.com/justrach/kuri) (v0.4.5+).

Two modes:

- **Aura readout** (self) — DeepSeek reads your Reposts tab, story highlights,
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
                     ├─ /scrape  →  DeepSeek V4-flash tool-use loop  ─┐
                     |                  ↓ (navigate / snap / click /  │  (all via
                     |                     screenshot / save_*)        │   OpenRouter)
                     |               kuri_client  →  HTTP  →  kuri    │
                     |                                          ↓     │
                     |                                       Chrome  →  instagram.com
                     |                                                 │
                     |     screenshot →  Qwen2.5-VL-72B  ──── returns ─┤  (only on
                     |                                       text desc │   screenshot
                     |                                       to loop   │   turns)
                     |                                                 │
                     ├─ analyze.py  →  DeepSeek V4-flash (JSON mode)  ─┤
                     |                  (aura for self, vibe for target │
                     |                   with cached self as context)  │
                     |                                                 │
                     └─ /chat  →  DeepSeek V4-flash tool-use loop with ─┘
                                  fetch_more tool, fetch_more kicks off
                                  focused_scrape (another kuri-driven
                                  loop returning prose evidence)
```

Kuri runs in WSL on Windows and natively on macOS / Linux. It exposes
Chrome's CDP via an HTTP API and serves compact a11y snapshots that the
nav loop uses by default. The DeepSeek tool loop has browser tools
(`navigate`, `snap_interactive`, `snap_text`, `page_text`, `click`,
`type`, `scroll`, `back`, `current_url`, `screenshot`) and `save_*` tools
(`save_reel`, `save_highlight`, `save_grid_post`, `save_header`, etc.) —
each `save_*` writes the in-progress scrape JSON to disk immediately, so
if the loop dies the file already contains everything collected so far.
The `screenshot` tool captures a PNG and routes it through a separate
Qwen2.5-VL-72B call; the vision model's text description is returned to
the nav loop as the tool result. This keeps the 60-turn nav loop on a
cheap text model while still allowing overlay text on reel video frames
to be read on demand. Both models are accessed through OpenRouter — one
API key, one base URL.

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
install kuri via its `install.sh`, and prompt for your `OPENROUTER_API_KEY`
which gets stored in `.env`. A `KURI_API_TOKEN` is also generated.
(One OpenRouter key routes to both DeepSeek for the nav loop and Qwen-VL
for screenshot turns — no second key needed.)

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

A full scrape runs about 25-35 minutes per profile (DeepSeek driving the
browser through ~20 reposts, 5 highlights, 5 grid posts, header). Cost is
typically well under 5 cents per scrape — pennies on `deepseek-v4-flash`
for the nav loop plus ~$0.001 per `screenshot` turn through Qwen-VL.
Roughly an order of magnitude cheaper than the prior Sonnet 4.6 setup.

## Architecture

- `server.py` — FastAPI app, SSE streaming, SQLite job persistence
- `agent_scrape.py` — DeepSeek tool-use loop (OpenRouter-routed,
  OpenAI-compatible API), custom save_* tools, screenshot tool via side
  call to Qwen-VL, task prompts for self vs target, driven through kuri
- `kuri_client.py` — thin HTTP wrapper around kuri (kuri runs in WSL,
  client talks to localhost:8080)
- `analyze.py` — aura / vibe prompts + analyze functions (DeepSeek JSON mode
  via OpenRouter)
- `render.py` — HTML card rendering with markdown-bold support
- `screenshot.py` — chromium CLI screenshot for sharing cards as PNGs
- `static/index.html` — single-page frontend (no framework)
- `main.py` — CLI orchestrator if you want to run without the server

`out/` holds the scrape JSON files and the SQLite DB (`instaclaw.db`).
Kuri persists its Chrome profile in WSL at `/root/.kuri/chrome-profile/`,
which is how the IG login carries over between runs.

## Configuration

`.env`:

- `OPENROUTER_API_KEY` — required. Grab one at https://openrouter.ai. Routes
  to both the nav-loop model and the vision model through one key.
- `INSTACLAW_MODEL` — nav-loop model. Defaults to `deepseek/deepseek-v4-flash`.
  Other plausible picks via OpenRouter: `moonshotai/kimi-k2` (stronger
  agentic tool-use, slightly pricier), `deepseek/deepseek-v4-pro` (too slow
  for a 60-turn loop, listed for completeness).
- `INSTACLAW_VISION_MODEL` — model the `screenshot` tool routes through.
  Defaults to `qwen/qwen2.5-vl-72b-instruct`. Other picks:
  `zhipuai/glm-4.5v`, `openai/gpt-4o-mini` (English-leaning, slightly
  pricier).
- `INSTACLAW_MAX_TURNS` — agent loop step ceiling. Default 60.
- `INSTACLAW_LLM_TIMEOUT` — LLM client timeout in seconds. Default 180.
- `OPENROUTER_BASE_URL` — defaults to `https://openrouter.ai/api/v1`.
  Override to point at any OpenAI-compatible endpoint.
- `KURI_API_TOKEN` — bearer token for the local kuri server. Auto-generated
  by `setup.bat`.
- `KURI_BASE_URL` — defaults to `http://127.0.0.1:8080`. Override if you're
  running kuri on a different host.

## Future work

See `FUTURE.md` for deferred items (mutuals scrape, compile-once-replay
for sub-30s subsequent scrapes, kuri re-enablement).
