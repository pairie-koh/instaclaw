"""Screenshot an HTML card (from render.py) to PNG via headless Chromium.

Three viewport modes: 'card' (600w, full-page), 'story' (1080x1920), 'square' (1080x1080).
No persistent profile — pure rendering, unlike scrape.py.
"""
import sys
from pathlib import Path

VIEWPORTS = {
    "card":   {"width": 600,  "height": 800,  "full_page": True},
    "story":  {"width": 1080, "height": 1920, "full_page": False},
    "square": {"width": 1080, "height": 1080, "full_page": False},
}


def screenshot(html_path: Path, out_path: Path, mode: str = "card") -> Path:
    if mode not in VIEWPORTS:
        raise ValueError(f"unknown mode {mode!r}; expected one of {list(VIEWPORTS)}")
    cfg = VIEWPORTS[mode]
    from playwright.sync_api import sync_playwright  # lazy: optional card-PNG dep
    url = "file:///" + str(html_path.resolve()).replace("\\", "/").lstrip("/")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": cfg["width"], "height": cfg["height"]},
                                    device_scale_factor=2)
            page.goto(url, wait_until="networkidle")
            page.evaluate("document.fonts.ready")
            out_path.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(out_path), full_page=cfg["full_page"])
        finally:
            browser.close()
    return out_path


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("usage: python screenshot.py <input.html> <output.png> [mode=card|story|square]")
        sys.exit(1)
    in_path = Path(sys.argv[1])
    png_path = Path(sys.argv[2])
    m = sys.argv[3] if len(sys.argv) > 3 else "card"
    written = screenshot(in_path, png_path, m)
    print(f"wrote {written}")
