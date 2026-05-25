"""Orchestrator. Usage:
    python main.py --self pairie.koh                      # aura readout on yourself
    python main.py --self pairie.koh --target some.handle # vibe check on target, with compatibility
    python main.py --target some.handle                   # vibe check on target only (no compatibility)
"""
import argparse
import json
import webbrowser
from pathlib import Path

import scrape
import analyze
import render

OUT = Path(__file__).parent / "out"

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--self", dest="self_handle", help="your IG handle")
    p.add_argument("--target", dest="target_handle", help="target IG handle for vibe check")
    p.add_argument("--skip-scrape", action="store_true", help="reuse existing JSON in out/")
    args = p.parse_args()

    if not args.self_handle and not args.target_handle:
        p.error("provide --self and/or --target")

    self_data, target_data = None, None

    if args.self_handle:
        path = OUT / f"self_{args.self_handle}.json"
        if args.skip_scrape and path.exists():
            self_data = json.loads(path.read_text(encoding="utf-8"))
        else:
            self_data = scrape.scrape_self(args.self_handle)

    if args.target_handle:
        path = OUT / f"target_{args.target_handle}.json"
        if args.skip_scrape and path.exists():
            target_data = json.loads(path.read_text(encoding="utf-8"))
        else:
            target_data = scrape.scrape_target(args.target_handle)

    if target_data:
        readout = analyze.vibe(self_data, target_data) if self_data else analyze.vibe(
            {"handle": "unknown", "header": {}, "grid": [], "reels": []}, target_data
        )
        out_html = OUT / f"vibe_{args.target_handle}.html"
        render.write(readout, out_html, target_handle=args.target_handle)
    else:
        readout = analyze.aura(self_data)
        out_html = OUT / f"aura_{args.self_handle}.html"
        render.write(readout, out_html)

    print(f"wrote {out_html}")
    webbrowser.open(out_html.as_uri())

if __name__ == "__main__":
    main()
