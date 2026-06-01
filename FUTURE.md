# Future improvements

Things deferred so the demo could ship. Each section has a short rationale and a
"how to enable" note since the scaffolding is usually already in `agent_scrape.py`
— the prompt instructions are what's missing.

## Deterministic scrapes via a tool-scoped agent (status: DONE, this branch)

Scrapes now run as a dedicated forge custom agent, `kuri-scraper`
(`.forge/agents/kuri-scraper.md`), whose `tools:` allowlist is scoped to
`mcp_kuri_tool_*` — ONLY the kuri MCP browser + checkpoint tools. The default
`forge` agent ships the full tool belt (read/write/shell/fetch/task + all MCP
servers), which let the nav loop wander off-surface (shelling out, web-search)
instead of driving the browser; scoping removes those tools entirely, so the
loop is structurally confined to kuri.

Enabled by **codegraff>=0.1.3**, the first SDK release to expose per-call agent
selection — `graff.chat(prompt, agent="kuri-scraper")` (also `set_active_agent`
/ `get_active_agent`). That's exactly the ask filed in justrach/codegraff#153
and tracked in instaclaw#3. The agent `.md` is version-controlled in the repo
and installed into forge's user-level agents dir (`~/.forge/agents/`) by
`register_mcp.py` — same reason kuri's MCP server is registered user-level:
project-local forge configs hit the headless trust gate (codegraff#152), while
the global agents dir is loaded regardless of cwd and has no trust gate.

`cg_agent.run()` gained an `agent_id` arg: `agent_scrape` (scrape + focused
follow-ups) passes `kuri-scraper`; `analyze`/`chat` reset to `forge`. The reset
is load-bearing — per-call selection is *sticky* across chats on the one
long-lived `Graff`, so a leaked kuri-only scope would strip the read tools that
analyze/chat depend on. (`forge` is also the historical default — every turn
already ran as it — so non-scrape behavior is unchanged.)

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

Each scrape is still an LLM-driven loop (~50 steps). On `mimo-v2.5`
the per-scrape cost is pennies, so the unit economics are no longer the
blocker — latency is. Still useful to compile once and replay:

The shape that does work: first scrape on a new user runs the LLM to learn the
IG DOM and emit per-surface JS scrapers. Subsequent scrapes call those scrapers
directly — no LLM-per-turn. First scrape: 10 min. Every scrape after: 30
seconds. This is how Skyvern / Multi-on / Adept do it.

## Vision for overlay text (status: HANDLED by MiMo-V2.5)

MiMo-V2.5 is natively omnimodal, so the `screenshot` tool no longer needs a
separate model — the side call goes back to MiMo with `image_url` input and
returns text the nav loop can consume as a string tool_result. The reason the
two-step (capture PNG → separate call → text back to loop) shape is still
there at all is OpenAI-spec: tool result messages must be string, not image
content. Cost is dominated by the agentic loop; per-screenshot cost is
fractions of a cent at MiMo pricing. If you want to swap to a different
omnimodal model later, change `INSTACLAW_MODEL` — the screenshot side call
uses the same env var.

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
on a custom MiMo-V2.5 tool-use loop (codegraff-routed, OpenAI-compatible API)
against kuri's compact a11y snapshots, with the same model handling screenshot
turns via image input. Token budget per turn is ~half what browser-use was
sending.

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
