# Future improvements

Things deferred so the demo could ship. Each section has a short rationale and a
"how to enable" note since the scaffolding is usually already in `agent_scrape.py`
— the prompt instructions are what's missing.

## Mutuals scrape on target profiles

Capture the "Followed by @X, @Y, and N others you follow" section that IG renders
under the bio on a target's profile when you're viewing it logged in. This is the
**cross-graph signal** — the only data point that explicitly tells you how the
user and the target are socially adjacent.

**Why it's high-signal**: every mutual is a real person the user follows AND who
also follows the target. For dating-recon, this is gold — it tells you who you
could ask about them IRL, what scene they overlap in, and gives the vibe-check
section "Mutual ground" something to chew on.

**What's already wired**:
- `ScrapeResult.mutuals: list[Follow]` field exists (`agent_scrape.py`)
- `save_mutual(handle, display_name)` controller action exists
- `analyze.py`'s SYSTEM_VIBE prompt already mentions mutuals as the cross-graph
  signal — it'll use the data once it's populated

**What's missing**: a `STEP: MUTUALS` block in `_target_task()` telling the agent
to find the "Followed by @X and N others you follow" line on the target's
profile, click "and N others" if expandable, and call `save_mutual` for each
handle/display_name surfaced.

**Why deferred**: the previous run got stuck in the Following modal which is the
same UI pattern. Want to first verify the reels-tab reposts flow lands cleanly
before adding another modal step.

## Faster scrape path (compile-once, replay)

Each scrape is still an LLM-driven loop (~50 steps). On `deepseek-chat` the
per-scrape cost is pennies, so the unit economics are no longer the blocker —
latency is. Still useful to compile once and replay:

The shape that does work: first scrape on a new user runs the LLM to learn the
IG DOM and emit per-surface JS scrapers. Subsequent scrapes call those scrapers
directly — no LLM-per-turn. First scrape: 10 min. Every scrape after: 30
seconds. This is how Skyvern / Multi-on / Adept do it.

## Vision for overlay text (status: REROUTED via Qwen-VL)

The prior Claude version had a `screenshot` tool that returned an image
content block to Claude directly. DeepSeek V4-flash is text-only, so an
intermediate vision call is now in the loop: when the nav model calls
`screenshot`, kuri captures the PNG, `_describe_screenshot` posts it to
Qwen2.5-VL-72B (via OpenRouter), and the vision model's text description
is returned to the nav loop as the tool_result string. The nav model
never sees the raw image. Cost is ~$0.001 per screenshot call, so even
heavy use stays well under a cent per scrape. Override
`INSTACLAW_VISION_MODEL` to swap the vision route (GLM-4.5V,
gpt-4o-mini, etc.).

## Kuri migration (status: DONE, this branch)

The issue (https://github.com/justrach/kuri/issues/172) was fixed in kuri
v0.4.5 (commit `648fe344`): `CdpClient` marks dead on WS send/receive failure,
`getCdpClient` re-fetches `/json/list` to pick up the fresh
`webSocketDebuggerUrl` Chrome assigned after the renderer swap, rebuilds the
client. `kuri_client._get` wraps that with one retry on `502 "CDP command
failed"` so the recovery is invisible to callers.

Caveat: v0.4.5 is tagged but not yet published as a release binary. Until
Rach cuts a release, `setup.bat` falls back to `install.sh` which still ships
v0.4.4 — see the setup script's NOTE about building from source if the IG
nav 502s prove unrecoverable. When the binary lands, the install script
picks it up automatically.

Win realized: dropped Playwright + browser-use entirely; the scrape now runs
on a custom DeepSeek tool-use loop (OpenRouter-routed, OpenAI-compatible API)
against kuri's compact a11y snapshots, with Qwen-VL handling screenshot turns.
Token budget per turn is ~half what browser-use was sending.

## Instagrapi / network interception as a backend fallback

For accounts the user owns or doesn't mind risking, `instagrapi` (private mobile
API) is 100× cheaper and 10× faster than browser automation. ~30 seconds per
scrape, cents instead of dollars. Trade-off: against IG ToS, accounts get
banned.

Could be a power-user setting: "Power mode (faster, account-risk)" toggle that
swaps the backend.

## Reposts tab URL pattern — CONFIRMED

Pattern is `https://www.instagram.com/<handle>/reposts/` — observed in the
2026-05-27 self scrape of @pairie.koh_ (the agent ended up at
`/pairie.koh_/reposts/` after clicking the tab icon).

**Optimization to apply**: in `_self_task` and `_target_task`, replace the
"find and click the REPOSTS tab icon" instruction with `goto /{handle}/reposts/`
directly. Saves 1-2 LLM turns per scrape (the icon-identification step) and
removes the fallback-to-Reels-tab branch.
