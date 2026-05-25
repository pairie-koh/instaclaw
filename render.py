"""Render a readout JSON to a screenshotable HTML card."""
import html
import json
from pathlib import Path

CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: #0a0a0a; color: #ededed; font-family: -apple-system, 'Inter', 'Segoe UI', sans-serif; padding: 60px 20px; }
.card { max-width: 560px; margin: 0 auto; background: #111; border: 1px solid #1f1f1f; border-radius: 18px; padding: 48px 40px; }
.eyebrow { font-size: 11px; letter-spacing: 0.18em; text-transform: uppercase; color: #888; margin-bottom: 18px; }
.headline { font-size: 32px; line-height: 1.15; font-weight: 600; letter-spacing: -0.02em; background: linear-gradient(135deg, #fff 0%, #b8b8b8 100%); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
.subheadline { font-size: 16px; color: #999; margin-top: 12px; line-height: 1.5; }
.section { margin-top: 36px; padding-top: 28px; border-top: 1px solid #1c1c1c; }
.section .label { font-size: 10px; letter-spacing: 0.16em; text-transform: uppercase; color: #666; margin-bottom: 12px; }
.section .body { font-size: 15px; line-height: 1.6; color: #d8d8d8; }
.section ul { list-style: none; padding: 0; }
.section li { padding: 10px 0; border-bottom: 1px solid #181818; }
.section li:last-child { border-bottom: none; }
.calibration { margin-top: 40px; padding-top: 24px; border-top: 1px solid #1c1c1c; font-size: 12px; color: #555; font-style: italic; line-height: 1.5; }
.footer { text-align: center; margin-top: 32px; font-size: 11px; color: #444; letter-spacing: 0.1em; text-transform: uppercase; }
"""

def _body_html(body):
    if isinstance(body, list):
        items = "".join(f"<li>{html.escape(str(x))}</li>" for x in body)
        return f"<ul>{items}</ul>"
    return html.escape(str(body))

def render(readout: dict, target_handle: str | None = None) -> str:
    eyebrow = f"vibe check · @{target_handle}" if target_handle else "aura readout"
    sections_html = "".join(
        f"""<div class="section">
            <div class="label">{html.escape(s.get('label', ''))}</div>
            <div class="body">{_body_html(s.get('body', ''))}</div>
        </div>""" for s in readout.get("sections", [])
    )
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{html.escape(eyebrow)}</title><style>{CSS}</style></head>
<body><div class="card">
  <div class="eyebrow">{html.escape(eyebrow)}</div>
  <div class="headline">{html.escape(readout.get('headline', ''))}</div>
  <div class="subheadline">{html.escape(readout.get('subheadline', ''))}</div>
  {sections_html}
  <div class="calibration">{html.escape(readout.get('calibration', ''))}</div>
  <div class="footer">ig aura · {('vibe' if target_handle else 'self')}</div>
</div></body></html>"""

def write(readout: dict, out_path: Path, target_handle: str | None = None):
    out_path.write_text(render(readout, target_handle), encoding="utf-8")
    return out_path
