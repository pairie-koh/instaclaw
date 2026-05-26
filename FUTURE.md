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

Currently each scrape is an LLM-driven loop: ~50 steps × $0.02-0.03 each. For a
viral product the unit economics don't work.

The shape that does work: first scrape on a new user runs the LLM to learn the
IG DOM and emit per-surface JS scrapers. Subsequent scrapes call those scrapers
directly — no LLM-per-turn. First scrape: 10 min, $4. Every scrape after: 30
seconds, 10 cents. This is how Skyvern / Multi-on / Adept do it.

## Drop vision for the navigation loop

Each turn currently sends a 2500×1340 screenshot to Sonnet (~2k tokens). Setting
`use_vision=False` in browser-use would drop that — ~70% input-token reduction,
~2x faster per turn. Trade-off: lose ability to read text burned into reel video
overlays.

A hybrid would be: text-only by default, agent calls a `take_screenshot` action
only when it specifically needs to read overlay text.

## Switch to Haiku 4.5 for the agent driver

Currently using Sonnet 4.6 ($3/M input, $15/M output). Haiku 4.5 ($1/M input,
$5/M output) is ~3× cheaper and noticeably faster per call. Less reliable on
multi-step navigation but for IG specifically — once the prompt is well-tuned —
should be fine.

Analysis (`analyze.py`) should stay on Sonnet since it's only 1-2 calls per
compatibility check and quality matters.

## Move kuri off the back-burner once justrach ships the fix

Open issue: https://github.com/justrach/kuri/issues/172 — Chrome's site
isolation kicks in on Instagram (cross-origin Facebook iframe + service worker)
and detaches the CDP session, but kuri's `CdpClient` never handles
`Target.detachedFromTarget` / `attachedToTarget` events. All work lives on the
`kuri-experiment` branch ready to re-enable.

Win if it lands: token costs drop ~2× (no vision screenshots, just compact a11y
snaps), and the surface is designed for agent loops (stable refs, batch
endpoint). Same architecture, fewer turns.

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
