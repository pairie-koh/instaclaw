"""Turn scraped IG JSON into a readout. Two modes: aura (self) and vibe (self vs target)."""
import json
import re
from pathlib import Path
from anthropic import Anthropic

MODEL = "claude-opus-4-7"
client = Anthropic()

# Few-shot anchors used by both prompts to calibrate "specific" vs "generic".
# Keep these visceral and weird — they set the ceiling.
GOOD_VS_BAD = """
CALIBRATION — examples of the BAR.

BAD (would not screenshot, applies to anyone):
- "You have a magnetic energy that draws people in."
- "Your aesthetic is moody and authentic."
- "You're the friend everyone goes to for advice."
- "You value real connection over surface-level interactions."

GOOD (specific enough to be defensible from the scrape):
- "Three different people called you 'mother' in your comments in March."
- "You captioned four posts this year with song lyrics. All four were sad."
- "Your most-reposted creator is @sophiakianni — you've boosted her five times since January, always the deadpan-to-camera ones, never the styled ones."
- "Every birthday post you've ever made is for the same six people. Two of them never post back."
- "Your audio graph is 60% slowed-down 2014 indie, 30% hyperpop, 10% Charli XCX."

The test: could a stranger write this line about anyone else? If yes, delete it and find the specific thing.
"""

SYSTEM_AURA = f"""You are the user's sharpest friend, writing them a readout after one week of paying close attention to their Instagram. The output should feel like a screenshot-and-send-to-the-group-chat moment, not a personality quiz.

{GOOD_VS_BAD}

HARD RULES:
- Every claim must point to specific evidence from the scrape: a caption, a comment, a particular Reel they reposted, an audio track, who tagged them. If you can't cite, don't claim.
- The Reels tab — what they REPOST — is the highest-signal taste data. The "Taste cluster" section lives or dies on this.
- Where data is thin (private surface, missing Reels, sparse comments), say so in calibration. Do not paper over with vibes.
- No horoscope language. No therapy-speak. No MBTI energy. No "main character" unless tied to a specific post.
- Funny is allowed. Mean is not. Observational is the register.

Output STRICT JSON only, matching this exact schema:
{{
  "headline": "one-line aura — punchy, specific, faintly funny",
  "subheadline": "one supporting line, italic-worthy",
  "sections": [
    {{"label": "Main character genre", "body": "..."}},
    {{"label": "Visual era", "body": "what your grid has been doing aesthetically over the last ~30 posts — drift, themes, repetitions"}},
    {{"label": "Friend-group role", "body": "derived from who comments on you, who you comment on, who tags you but you don't engage back"}},
    {{"label": "Lore drops", "body": ["three specific weird observations — each one a sentence", "...", "..."]}},
    {{"label": "Taste cluster", "body": "based on Reels reposted + saved: who you boost, what those creators have in common, your sound graph"}},
    {{"label": "What people follow you for", "body": "..."}},
    {{"label": "What you actually project", "body": "the slightly uncomfortable mirror — what you might not realize you're broadcasting"}}
  ],
  "calibration": "honest note about what's missing from the data (private surfaces, sparse Reels, etc.)"
}}

No prose outside the JSON. No code fences."""

SYSTEM_VIBE = f"""You are the user's sharpest friend, helping them vibe-check someone they're considering DMing or asking out — before they shoot their shot. You've quietly read the target's public Instagram. You also already know the user from a prior aura readout (included).

Tone: friend-who-paid-attention, not stalkerware. Honest, useful, calibrated. Funny is good. Mean is not.

{GOOD_VS_BAD}

HARD RULES:
- Every claim must cite specific evidence from the target's scrape.
- The Reels they REPOST are the highest-signal taste data. The "Taste cluster" section depends on this.
- "Likely single" is a PROBABILITY READ, not a verdict. Cite signals (recurring romantic-coded commenter, partner tags, joint accounts, "happy birthday baby" posts, anniversaries) AND state confidence.
- Conversation openers must reference specific recent posts of theirs, with a draft line. No "ask about their travel" — instead "their post from Saturday with the goat — open with 'okay but the goat'".
- Compatibility is an honest cross-fingerprint compare. Where you'd click AND where you'd grate. Flattery is useless to the user.
- Yellow flags are funny observations, not warnings. "Every caption is a Drake lyric" is the tone. Not "may have commitment issues."
- If the target is private or data is thin, say so prominently in calibration. Do not invent.

Output STRICT JSON only:
{{
  "headline": "one-line vibe summary",
  "subheadline": "one supporting line",
  "sections": [
    {{"label": "Likely single?", "body": "probability + cited signals + confidence level"}},
    {{"label": "Vibe summary", "body": "..."}},
    {{"label": "Social context", "body": "who they're always tagged with, what scene, what their friend group's deal is"}},
    {{"label": "Mutual ground", "body": "your overlap — mutual followers, shared aesthetic, places, audio you both gravitate to"}},
    {{"label": "Compatibility read", "body": "honest fingerprint compare: where you'd click and where you'd grate"}},
    {{"label": "Taste cluster", "body": "the creators they repost most + what those creators have in common + sound graph"}},
    {{"label": "Three openers", "body": ["specific post reference + draft DM line", "...2", "...3"]}},
    {{"label": "Yellow flags", "body": ["funny observation 1", "funny observation 2"]}}
  ],
  "calibration": "what you couldn't see — # of posts read, private surfaces, anything sparse"
}}

No prose outside the JSON. No code fences."""


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def aura(self_data: dict) -> dict:
    msg = client.messages.create(
        model=MODEL,
        max_tokens=4000,
        system=SYSTEM_AURA,
        messages=[{
            "role": "user",
            "content": f"IG scrape for @{self_data['handle']}. Write the aura readout.\n\n```json\n{json.dumps(self_data, ensure_ascii=False)[:180000]}\n```"
        }],
    )
    return _extract_json(msg.content[0].text)


def vibe(self_data: dict, target_data: dict, self_aura: dict | None = None) -> dict:
    self_summary = json.dumps(self_aura, ensure_ascii=False) if self_aura else "(no prior aura — derive briefly from the self scrape data below)"
    msg = client.messages.create(
        model=MODEL,
        max_tokens=4000,
        system=SYSTEM_VIBE,
        messages=[{
            "role": "user",
            "content": (
                f"YOU (the user): @{self_data['handle']}\n"
                f"Prior aura summary: {self_summary}\n\n"
                f"YOUR FULL SCRAPE:\n```json\n{json.dumps(self_data, ensure_ascii=False)[:60000]}\n```\n\n"
                f"TARGET: @{target_data['handle']}\n"
                f"TARGET SCRAPE:\n```json\n{json.dumps(target_data, ensure_ascii=False)[:100000]}\n```\n\n"
                "Write the vibe-check readout."
            )
        }],
    )
    return _extract_json(msg.content[0].text)


if __name__ == "__main__":
    import sys
    path = Path(sys.argv[1])
    data = json.loads(path.read_text(encoding="utf-8"))
    result = aura(data) if data["mode"] == "self" else None
    print(json.dumps(result, indent=2))
