"""Orchestrator. Usage:
    python main.py --self pairie.koh
    python main.py --self pairie.koh --target some.handle
    python main.py --target some.handle
    python main.py --self pairie.koh --skip-scrape          # reuse cached JSON
    python main.py --self pairie.koh --story --screenshot   # also produce story PNG
"""
import argparse
import json
import webbrowser
from pathlib import Path

import agent_scrape as scrape
import analyze
import render
import screenshot as shotgun

OUT = Path(__file__).parent / "out"


def _load_or_scrape(handle: str, mode: str, skip: bool) -> dict:
    path = OUT / f"{mode}_{handle}.json"
    if skip and path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return scrape.scrape_self(handle) if mode == "self" else scrape.scrape_target(handle)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--self", dest="self_handle", help="your IG handle")
    p.add_argument("--target", dest="target_handle", help="target IG handle for vibe check")
    p.add_argument("--skip-scrape", action="store_true", help="reuse existing JSON in out/")
    p.add_argument("--story", action="store_true", help="also render 1080x1920 story version")
    p.add_argument("--screenshot", action="store_true", help="also produce PNG of the card")
    args = p.parse_args()

    if not args.self_handle and not args.target_handle:
        p.error("provide --self and/or --target")

    self_data = _load_or_scrape(args.self_handle, "self", args.skip_scrape) if args.self_handle else None
    target_data = _load_or_scrape(args.target_handle, "target", args.skip_scrape) if args.target_handle else None

    if target_data:
        readout = analyze.vibe(
            self_data or {"handle": "unknown", "header": {}, "grid": [], "reels": []},
            target_data,
        )
        stem = f"vibe_{args.target_handle}"
        target_for_render = args.target_handle
    else:
        readout = analyze.aura(self_data)
        stem = f"aura_{args.self_handle}"
        target_for_render = None

    card_html = OUT / f"{stem}.html"
    render.write(readout, card_html, target_handle=target_for_render)
    print(f"wrote {card_html}")
    webbrowser.open(card_html.as_uri())

    if args.screenshot:
        card_png = OUT / f"{stem}.png"
        shotgun.screenshot(card_html, card_png, "card")
        print(f"wrote {card_png}")

    if args.story:
        story_html = OUT / f"{stem}.story.html"
        render.write(readout, story_html, target_handle=target_for_render)
        story_png = OUT / f"{stem}.story.png"
        shotgun.screenshot(story_html, story_png, "story")
        print(f"wrote {story_html}")
        print(f"wrote {story_png}")


if __name__ == "__main__":
    main()
