"""Instagram scraper. Two modes: self (logged-in, full surfaces) and target (public only).

Selectors WILL break — Instagram rewrites class names constantly. Every extractor is
wrapped in try/except and returns partial data so we never lose the whole pass.
"""
import json
import random
import time
from pathlib import Path
from playwright.sync_api import sync_playwright, Page, BrowserContext

ROOT = Path(__file__).parent
PROFILE_DIR = ROOT / ".chrome-profile"
OUT_DIR = ROOT / "out"
OUT_DIR.mkdir(exist_ok=True)
IG = "https://www.instagram.com"


def pause(short=False):
    time.sleep(random.uniform(0.8, 1.4) if short else random.uniform(2.0, 3.5))


def safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def open_browser():
    pw = sync_playwright().start()
    ctx = pw.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        channel="chrome",
        headless=False,
        viewport={"width": 1280, "height": 900},
    )
    return pw, ctx


def ensure_logged_in(ctx: BrowserContext):
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(IG)
    pause()
    if "accounts/login" in page.url or page.locator("input[name='username']").count() > 0:
        print("\n>>> Log in to Instagram in the browser window, then press Enter here.")
        input()
    return page


def scroll_collect(page: Page, selector: str, target_count: int, max_scrolls=15):
    seen = set()
    for _ in range(max_scrolls):
        elems = page.locator(selector).all()
        for e in elems:
            href = safe(lambda: e.get_attribute("href"))
            if href:
                seen.add(href)
        if len(seen) >= target_count:
            break
        page.mouse.wheel(0, 2000)
        pause(short=True)
    return list(seen)[:target_count]


def scrape_profile_header(page: Page) -> dict:
    name = safe(lambda: page.locator("header h2, header h1").first.text_content(), "")
    bio = safe(lambda: page.locator("header section").first.inner_text(), "")
    stats = safe(lambda: page.locator("header li").all_text_contents(), [])
    return {"name": (name or "").strip(), "header_text": (bio or "").strip(), "stats_raw": stats}


def scrape_post(page: Page, href: str) -> dict:
    page.goto(IG + href if href.startswith("/") else href)
    pause()
    caption = safe(lambda: page.locator("article h1").first.inner_text(), "")
    if not caption:
        caption = safe(lambda: page.locator("article ul li span").first.inner_text(), "")
    comments = safe(lambda: page.locator("article ul ul span").all_text_contents()[:25], [])
    likes = safe(lambda: page.locator("section span:has-text('likes'), section a:has-text('likes')").first.inner_text(), "")
    return {"url": href, "caption": caption, "comments": comments, "likes_raw": likes}


def scrape_grid(page: Page, profile_url: str, max_posts=15) -> list:
    page.goto(profile_url)
    pause()
    hrefs = scroll_collect(page, "article a[href*='/p/']", max_posts)
    out = []
    for h in hrefs:
        try:
            out.append(scrape_post(page, h))
        except Exception as e:
            out.append({"url": h, "error": str(e)})
    return out


def scrape_reels(page: Page, profile_url: str, max_reels=20) -> list:
    """The Reels tab. Includes their own AND reposted — the curation signal."""
    page.goto(profile_url + "reels/")
    pause()
    hrefs = scroll_collect(page, "a[href*='/reel/']", max_reels)
    out = []
    for h in hrefs:
        try:
            page.goto(IG + h if h.startswith("/") else h)
            pause()
            caption = safe(lambda: page.locator("article h1").first.inner_text(), "")
            creator = safe(lambda: page.locator("article header a").first.inner_text(), "")
            audio = safe(lambda: page.locator("a[href*='/reels/audio/']").first.inner_text(), "")
            out.append({"url": h, "caption": caption, "creator": creator, "audio": audio})
        except Exception as e:
            out.append({"url": h, "error": str(e)})
    return out


def scrape_tagged(page: Page, profile_url: str, max_posts=10) -> list:
    page.goto(profile_url + "tagged/")
    pause()
    hrefs = scroll_collect(page, "a[href*='/p/']", max_posts)
    return [{"url": h} for h in hrefs]


def scrape_saved(page: Page, self_handle: str, max_items=20) -> list:
    """Self-only. Saved collection is private to the account owner."""
    page.goto(f"{IG}/{self_handle}/saved/all-posts/")
    pause()
    hrefs = scroll_collect(page, "a[href*='/p/'], a[href*='/reel/']", max_items)
    return [{"url": h} for h in hrefs]


def scrape_self(handle: str) -> dict:
    pw, ctx = open_browser()
    try:
        page = ensure_logged_in(ctx)
        profile_url = f"{IG}/{handle}/"
        page.goto(profile_url); pause()
        data = {
            "mode": "self",
            "handle": handle,
            "header": scrape_profile_header(page),
            "grid": scrape_grid(page, profile_url, max_posts=15),
            "reels": scrape_reels(page, profile_url, max_reels=20),
            "tagged": scrape_tagged(page, profile_url, max_posts=10),
            "saved": scrape_saved(page, handle, max_items=20),
        }
        out_path = OUT_DIR / f"self_{handle}.json"
        out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"wrote {out_path}")
        return data
    finally:
        ctx.close(); pw.stop()


def scrape_target(handle: str) -> dict:
    pw, ctx = open_browser()
    try:
        page = ensure_logged_in(ctx)
        profile_url = f"{IG}/{handle}/"
        page.goto(profile_url); pause()
        if page.locator("text=This Account is Private").count() > 0:
            data = {"mode": "target", "handle": handle, "private": True,
                    "header": scrape_profile_header(page)}
        else:
            data = {
                "mode": "target",
                "handle": handle,
                "private": False,
                "header": scrape_profile_header(page),
                "grid": scrape_grid(page, profile_url, max_posts=15),
                "reels": scrape_reels(page, profile_url, max_reels=20),
                "tagged": scrape_tagged(page, profile_url, max_posts=10),
            }
        out_path = OUT_DIR / f"target_{handle}.json"
        out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"wrote {out_path}")
        return data
    finally:
        ctx.close(); pw.stop()
