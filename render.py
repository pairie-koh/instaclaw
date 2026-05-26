"""Render a readout JSON to a screenshotable HTML card."""
import html
import json
from pathlib import Path

# Accent: signal orange (#ff5b1f) — distinctive without being neon; reads as ink, not UI.
# Type: Fraunces (display) for the headline, Inter (text) for everything else.
FONTS = (
    "<link rel='preconnect' href='https://fonts.googleapis.com'>"
    "<link rel='preconnect' href='https://fonts.gstatic.com' crossorigin>"
    "<link href='https://fonts.googleapis.com/css2?"
    "family=Fraunces:opsz,wght@9..144,400;9..144,500&"
    "family=Inter:wght@400;500;600&"
    "family=JetBrains+Mono:wght@400&display=swap' rel='stylesheet'>"
)

_BASE = """
:root {
  --ink: #f4f1ea; --paper: #14110d; --rule: #2a261f;
  --mute: #8a8273; --quiet: #5b5447; --accent: #ff5b1f;
  --serif: 'Fraunces', 'Iowan Old Style', Georgia, serif;
  --sans: 'Inter', -apple-system, 'Segoe UI', sans-serif;
  --mono: 'JetBrains Mono', ui-monospace, Menlo, monospace;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { background: var(--paper); color: var(--ink); font-family: var(--sans);
  -webkit-font-smoothing: antialiased; text-rendering: optimizeLegibility; }
.eyebrow { font-family: var(--mono); font-size: 11px; letter-spacing: 0.14em;
  text-transform: uppercase; color: var(--mute); display: flex; align-items: center; gap: 10px; }
.eyebrow .dot { width: 6px; height: 6px; border-radius: 50%; background: var(--accent);
  box-shadow: 0 0 0 4px rgba(255,91,31,0.12); }
.headline { font-family: var(--serif); font-weight: 400; letter-spacing: -0.018em;
  color: var(--ink); font-variation-settings: 'opsz' 96; }
.headline em { font-style: italic; color: var(--accent); font-weight: 500; }
.subheadline { font-family: var(--serif); font-style: italic; color: var(--mute);
  font-weight: 400; }
.label { font-family: var(--mono); text-transform: uppercase; letter-spacing: 0.18em;
  color: var(--quiet); font-size: 10.5px; }
.body { color: #cbc4b3; }
.body ul { list-style: none; padding: 0; }
.body li { padding: 14px 0; border-bottom: 1px solid var(--rule);
  display: grid; grid-template-columns: 22px 1fr; align-items: baseline; gap: 6px; }
.body li:last-child { border-bottom: none; }
.body li::before { content: counter(item, decimal-leading-zero);
  counter-increment: item; font-family: var(--mono); font-size: 10px;
  color: var(--accent); letter-spacing: 0.08em; }
.body ul { counter-reset: item; }
.calibration { font-family: var(--serif); font-style: italic; color: var(--quiet); }
.rule { height: 1px; background: var(--rule); border: 0; }
.foot { font-family: var(--mono); font-size: 10px; letter-spacing: 0.22em;
  text-transform: uppercase; color: var(--quiet);
  display: flex; justify-content: space-between; align-items: center; }
"""

CARD_CSS = _BASE + """
body { padding: 72px 24px; }
.card { max-width: 620px; margin: 0 auto; padding: 56px 56px 44px;
  background: linear-gradient(180deg, #16130e 0%, #14110d 60%); border: 1px solid var(--rule);
  border-radius: 4px; box-shadow: 0 1px 0 rgba(255,255,255,0.02) inset,
  0 40px 80px -40px rgba(0,0,0,0.6); }
.header { display: flex; flex-direction: column; gap: 28px; }
.headline { font-size: 44px; line-height: 1.06; }
.subheadline { font-size: 18px; line-height: 1.45; max-width: 32ch; }
.section { margin-top: 40px; padding-top: 28px; border-top: 1px solid var(--rule); }
.section .label { margin-bottom: 16px; }
.section .body { font-size: 15.5px; line-height: 1.62; }
.section .body p + p { margin-top: 14px; }
.section .body strong { color: var(--ink); font-weight: 600; }
.calibration { margin-top: 44px; padding-top: 24px; border-top: 1px solid var(--rule);
  font-size: 14px; line-height: 1.55; }
.foot { margin-top: 36px; }
"""

STORY_CSS = _BASE + """
body { background: var(--paper); }
.story { width: 1080px; height: 1920px; margin: 0 auto; padding: 140px 96px 120px;
  display: flex; flex-direction: column; position: relative;
  background:
    radial-gradient(ellipse 80% 50% at 50% 0%, rgba(255,91,31,0.06), transparent 60%),
    linear-gradient(180deg, #16130e 0%, #100d09 100%); }
.story .eyebrow { font-size: 20px; letter-spacing: 0.18em; }
.story .eyebrow .dot { width: 10px; height: 10px; box-shadow: 0 0 0 8px rgba(255,91,31,0.12); }
.story .headline { font-size: 108px; line-height: 1.02; margin-top: 56px; }
.story .subheadline { font-size: 34px; line-height: 1.4; max-width: 22ch; margin-top: 36px; }
.story .sections { margin-top: 72px; display: flex; flex-direction: column; gap: 44px; }
.story .section { padding-top: 28px; border-top: 1px solid var(--rule); }
.story .label { font-size: 18px; margin-bottom: 18px; }
.story .body { font-size: 28px; line-height: 1.45; }
.story .body p + p { margin-top: 22px; }
.story .body strong { color: var(--ink); font-weight: 600; }
.story .body li { padding: 18px 0; grid-template-columns: 44px 1fr; }
.story .body li::before { font-size: 18px; }
.story .tail { margin-top: auto; }
.story .calibration { font-size: 24px; line-height: 1.5; padding-top: 36px;
  border-top: 1px solid var(--rule); }
.story .foot { margin-top: 40px; font-size: 18px; letter-spacing: 0.28em; }
.story .corner { position: absolute; top: 72px; right: 96px; font-family: var(--mono);
  font-size: 16px; letter-spacing: 0.2em; color: var(--quiet); text-transform: uppercase; }
"""


import re as _re


def _bold(s: str) -> str:
    """Convert **text** to <strong>text</strong>. Runs on already-escaped HTML."""
    return _re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)


def _body_html(body) -> str:
    if isinstance(body, list):
        items = "".join(f"<li><span>{_bold(html.escape(str(x)))}</span></li>" for x in body)
        return f"<ul>{items}</ul>"
    # Honor line breaks. Double newline = new paragraph, single = soft break.
    text = str(body)
    paragraphs = text.split("\n\n")
    rendered_paras = []
    for para in paragraphs:
        if not para.strip():
            continue
        escaped = "<br>".join(_bold(html.escape(line)) for line in para.split("\n"))
        rendered_paras.append(f"<p>{escaped}</p>")
    return "".join(rendered_paras) or "<p></p>"


def _sections_html(sections) -> str:
    return "".join(
        f'<div class="section"><div class="label">{html.escape(s.get("label",""))}</div>'
        f'<div class="body">{_body_html(s.get("body",""))}</div></div>'
        for s in sections
    )


def _eyebrow(target_handle: str | None) -> str:
    label = f"Vibe check / @{target_handle}" if target_handle else "Aura readout"
    return f'<div class="eyebrow"><span class="dot"></span><span>{html.escape(label)}</span></div>'


def _foot(target_handle: str | None) -> str:
    mode = "vibe" if target_handle else "self"
    return (f'<div class="foot"><span>ig &middot; aura</span>'
            f'<span>No.&nbsp;{mode.upper()}</span></div>')


def render(readout: dict, target_handle: str | None = None) -> str:
    title = readout.get("headline", "Aura")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{html.escape(title)}</title>
{FONTS}<style>{CARD_CSS}</style></head>
<body><article class="card">
  <header class="header">
    {_eyebrow(target_handle)}
    <h1 class="headline">{html.escape(title)}</h1>
    <p class="subheadline">{html.escape(readout.get('subheadline',''))}</p>
  </header>
  {_sections_html(readout.get('sections', []))}
  {_foot(target_handle)}
</article></body></html>"""


def render_story(readout: dict, target_handle: str | None = None) -> str:
    title = readout.get("headline", "Aura")
    handle = f"@{target_handle}" if target_handle else "self"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{html.escape(title)} / story</title>
<meta name="viewport" content="width=1080">
{FONTS}<style>{STORY_CSS}</style></head>
<body><section class="story">
  <div class="corner">{html.escape(handle)}</div>
  {_eyebrow(target_handle)}
  <h1 class="headline">{html.escape(title)}</h1>
  <p class="subheadline">{html.escape(readout.get('subheadline',''))}</p>
  <div class="sections">{_sections_html(readout.get('sections', []))}</div>
  <div class="tail">
      {_foot(target_handle)}
  </div>
</section></body></html>"""


def write(readout: dict, out_path: Path, target_handle: str | None = None) -> Path:
    out_path = Path(out_path)
    renderer = render_story if out_path.stem.endswith(".story") or "story" in out_path.stem.lower() else render
    out_path.write_text(renderer(readout, target_handle), encoding="utf-8")
    return out_path
