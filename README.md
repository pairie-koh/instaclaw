# instaclaw

Pre-DM recon, agent-driven. You point it at your own Instagram and it tells
you what your feed is actually saying about you. Then you point it at someone
you're about to DM and it tells you whether you'd vibe, whether they're
likely single, and gives you three openers tied to specific posts they
reposted.

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
                     ├─ /scrape  →  browser-use Agent  →  Playwright Chromium  →  instagram.com
                     |                  (Claude Sonnet 4.6 drives it via tool use,
                     |                   incremental save_* actions checkpoint each
                     |                   reel / highlight / grid post to disk)
                     |
                     ├─ analyze.py  →  Claude Sonnet 4.6
                     |                  (aura prompt for self, vibe prompt for target
                     |                   with cached self as comparison context)
                     |
                     └─ /chat  →  Anthropic tool-use loop, can call fetch_more
                                  which kicks off a focused_scrape agent run
```

Browser-use carries the Playwright integration. Claude reads each surface
(profile header, Reposts tab, highlight bubbles, grid, comments) and emits
structured data via custom Controller actions that persist after every
extraction — so the run survives mid-loop timeouts without losing collected
data. The analyze layer is a single Claude call per readout that turns the
scrape JSON into a written card.

Output is HTML rendered server-side and served into an iframe on the
single-page frontend. SSE streams the agent's live narration so you can
watch it think.

## Install

Windows:

```cmd
setup.bat
```

This creates `.venv`, pip-installs the requirements (browser-use, anthropic,
fastapi, playwright, etc.), runs `playwright install chromium`, and prompts
for your `ANTHROPIC_API_KEY` which gets stored in `.env`.

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
- `agent_scrape.py` — browser-use agent loop, custom save_* Controller
  actions, task prompts for self vs target
- `analyze.py` — aura / vibe prompts + analyze functions
- `render.py` — HTML card rendering with markdown-bold support
- `screenshot.py` — chromium CLI screenshot for sharing cards as PNGs
- `static/index.html` — single-page frontend (no framework)
- `main.py` — CLI orchestrator if you want to run without the server

`out/` holds the scrape JSON files and the SQLite DB (`instaclaw.db`).
`.chrome-profile/` is the persisted Playwright profile that keeps you
logged in to IG between runs.

## Configuration

`.env`:

- `ANTHROPIC_API_KEY` — required
- `INSTACLAW_MODEL` — defaults to `claude-sonnet-4-6`. Set to
  `claude-haiku-4-5-20251001` for ~3× cheaper / ~2× faster scrapes at
  some reliability cost.
- `INSTACLAW_MAX_TURNS` — agent loop step ceiling. Default 60.

## Other branches

- `kuri-experiment` — refactor that swaps browser-use for justrach/kuri.
  Currently blocked on a CDP-session-detachment bug on instagram.com,
  filed as [kuri#172](https://github.com/justrach/kuri/issues/172). If
  that lands upstream, kuri becomes a viable cheaper path.

## Future work

See `FUTURE.md` for deferred items (mutuals scrape, compile-once-replay
for sub-30s subsequent scrapes, Haiku swap, kuri re-enablement).
