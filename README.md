# instaclaw

Pre-DM recon, agent-driven. You point it at your own Instagram and it tells
you what your feed is actually saying about you. Then you point it at someone
you're about to DM and it tells you whether you'd vibe, whether they're
likely single, and gives you three openers tied to specific posts they
reposted.

Runs on [kuri](https://github.com/justrach/kuri) (v0.4.5+).

Two modes:

- **Aura readout** (self) — MiMo reads your Reposts tab, story highlights,
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
                     ├─ /scrape  →  MiMo-V2.5 tool-use loop  ─────┐
                     |                  ↓ (navigate / snap /     │  (all via
                     |                     click / screenshot /  │   codegraff)
                     |                     save_*)               │
                     |               kuri_client  →  HTTP  →  kuri server (WSL)
                     |                                          ↓
                     |                                       Chrome  →  instagram.com
                     |                                                 │
                     |     screenshot →  MiMo-V2.5 (image input)  ────┤  (side call
                     |                       returns text description │   on screenshot
                     |                       to nav loop              │   turns only)
                     |                                                 │
                     ├─ analyze.py  →  MiMo-V2.5 (JSON mode)  ────────┤
                     |                  (aura for self, vibe for target
                     |                   with cached self as context) │
                     |                                                 │
                     └─ /chat  →  MiMo-V2.5 tool-use loop with ───────┘
                                  fetch_more tool, fetch_more kicks off
                                  focused_scrape (another kuri-driven
                                  loop returning prose evidence)
```

Kuri runs in WSL on Windows and natively on macOS / Linux. It exposes
Chrome's CDP via an HTTP API and serves compact a11y snapshots that the
nav loop uses by default. The MiMo tool loop has browser tools (`navigate`,
`snap_interactive`, `snap_text`, `page_text`, `click`, `type`, `scroll`,
`back`, `current_url`, `screenshot`) and `save_*` tools (`save_reel`,
`save_highlight`, `save_grid_post`, `save_header`, etc.) — each `save_*`
writes the in-progress scrape JSON to disk immediately, so if the loop
dies the file already contains everything collected so far. MiMo-V2.5 is
natively omnimodal, but OpenAI-compatible tool results must be strings —
so the `screenshot` tool captures a PNG, posts it back to MiMo in a
separate single-shot call with `image_url` input, and returns the model's
text description to the nav loop. One model, one API key, one base URL.

The whole stack talks to MiMo-V2.5 through codegraff's OpenAI-compatible
gateway (`https://gateway.codegraff.com/v1`), so it stays a standard
`openai`-SDK app — only the base URL, key, and model alias point at
codegraff.

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
install kuri via its `install.sh`, and prompt for your `CODEGRAFF_API_KEY`
which gets stored in `.env`. A `KURI_API_TOKEN` is also generated.
(One codegraff key routes to MiMo-V2.5 for both the nav loop and
screenshot turns — no second key needed.)

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

A full scrape runs about 25-35 minutes per profile (MiMo driving the
browser through ~20 reposts, 5 highlights, 5 grid posts, header). Cost is
typically a few cents per scrape on `mimo-v2.5` (MiMo-V2.5, routed through
the codegraff gateway) — far cheaper than a frontier model would be for the
same loop. See https://codegraff.com/docs/models for current gateway pricing.

## Architecture

- `server.py` — FastAPI app, SSE streaming, SQLite job persistence
- `agent_scrape.py` — MiMo-V2.5 tool-use loop (codegraff-routed,
  OpenAI-compatible API), custom save_* tools, screenshot tool via side
  call to MiMo with image input, task prompts for self vs target, driven
  through kuri
- `kuri_client.py` — thin HTTP wrapper around kuri (kuri runs in WSL,
  client talks to localhost:8080)
- `analyze.py` — aura / vibe prompts + analyze functions (MiMo-V2.5 JSON
  mode via the codegraff gateway)
- `render.py` — HTML card rendering with markdown-bold support
- `screenshot.py` — chromium CLI screenshot for sharing cards as PNGs
- `static/index.html` — single-page frontend (no framework)
- `main.py` — CLI orchestrator if you want to run without the server

`out/` holds the scrape JSON files and the SQLite DB (`instaclaw.db`).
Kuri persists its Chrome profile in WSL at `/root/.kuri/chrome-profile/`,
which is how the IG login carries over between runs.

## Configuration

`.env`:

- `CODEGRAFF_API_KEY` — required. A `cg_sk_` key from
  https://codegraff.com/dashboard/keys. Drives both the nav loop and the
  screenshot tool's vision side call through MiMo. (`CG_API_KEY` also works.)
- `INSTACLAW_MODEL` — model used for everything (nav loop, analyze, chat,
  screenshot side call). Defaults to `mimo-v2.5`, the only codegraff alias
  that accepts image input (`input_modalities: ["text", "image"]`) — the
  screenshot vision tool needs it. The other aliases (`mimo-v2.5-pro`,
  `deepseek-v4-pro`, `kimi-k2.6`, `gpt-5.5`, `grok-build`) are text-only on
  the gateway, so switching to one would need a separate vision route for the
  screenshot path. See https://codegraff.com/docs/models.
- `INSTACLAW_MAX_TOKENS` — max_tokens per LLM call. Default 24000. `mimo-v2.5`
  is a reasoning model (it spends tokens thinking before answering), so the
  cap must be generous or `content` comes back empty. Lower it only if you
  switch to a non-reasoning model.
- `INSTACLAW_MAX_TURNS` — agent loop step ceiling. Default 60.
- `INSTACLAW_LLM_TIMEOUT` — LLM client timeout in seconds. Default 180.
- `CODEGRAFF_BASE_URL` — defaults to `https://gateway.codegraff.com/v1`.
  Override to point at any OpenAI-compatible endpoint (must include `/v1`).
- `KURI_API_TOKEN` — bearer token for the local kuri server. Auto-generated
  by `setup.bat`.
- `KURI_BASE_URL` — defaults to `http://127.0.0.1:8080`. Override if you're
  running kuri on a different host.

## Future work

See `FUTURE.md` for deferred items (mutuals scrape, compile-once-replay
for sub-30s subsequent scrapes, kuri re-enablement).
