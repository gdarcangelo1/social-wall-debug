#!/usr/bin/env python3
"""Best-effort public Instagram collector using Playwright Chromium."""

import argparse
import asyncio
import importlib.util
import re
from urllib.parse import urlsplit

from collector_utils import (
    absolute_url,
    click_cookie_buttons,
    filtered_accounts,
    in_date_range,
    page_text_limited,
    parse_visible_date,
    resolve_range_for_args,
    short_text,
    should_insert_status,
)
from db import ensure_db, upsert_social_post

POST_RE = re.compile(r"/(p|reel|tv)/([^/?#]+)")
LOGIN_RE = re.compile(r"log in|login|sign up|accedi|iscriviti", re.I)
BLOCK_RE = re.compile(r"temporarily blocked|try again later|challenge|required|riprova più tardi", re.I)


def is_instagram_post_url(url):
    parts = urlsplit(url)
    return "instagram.com" in parts.netloc.lower() and POST_RE.search(parts.path) is not None


def instagram_post_id(url):
    match = POST_RE.search(urlsplit(url).path)
    return match.group(2) if match else None


async def link_context(link):
    href = await link.get_attribute("href")
    text = None
    title = None
    post_date = None
    confident = False
    for xpath in ("ancestor::article[1]", "ancestor::div[1]", "ancestor::div[2]", "ancestor::div[3]"):
        try:
            container = link.locator(f"xpath={xpath}").first
            if await container.count():
                text = short_text(await container.inner_text(timeout=1200))
                time_loc = container.locator("time, a[aria-label]")
                count = min(await time_loc.count(), 6)
                for idx in range(count):
                    node = time_loc.nth(idx)
                    for attr in ("datetime", "title", "aria-label"):
                        value = await node.get_attribute(attr)
                        post_date, confident = parse_visible_date(value)
                        if post_date:
                            break
                    if not post_date:
                        post_date, confident = parse_visible_date(await node.inner_text(timeout=500))
                    if post_date:
                        break
                break
        except Exception:
            continue
    if text:
        title = text[:90]
    return href, title, text, post_date, confident


async def scan_account(browser, account, args, date_from, date_to, summary):
    page = await browser.new_page(viewport={"width": 1365, "height": 900})
    account_url = account.get("account_url")
    try:
        await page.goto(account_url, wait_until="domcontentloaded", timeout=45000)
        await click_cookie_buttons(page)
        body_text = await page_text_limited(page)
        if BLOCK_RE.search(body_text):
            summary["temporarily_blocked"] += 1
            print(f"temporarily_blocked: {account.get('societa')} ({account_url})")
            return
        if LOGIN_RE.search(body_text) and not await page.locator("a[href*='/p/'], a[href*='/reel/'], a[href*='/tv/']").count():
            summary["login_required"] += 1
            print(f"login_required: {account.get('societa')} ({account_url})")
            return
        seen = {}
        for scroll_no in range(max(1, args.max_scrolls)):
            links = page.locator("a[href]")
            count = await links.count()
            for idx in range(count):
                link = links.nth(idx)
                href, title, text, post_date, confident = await link_context(link)
                post_url = absolute_url(account_url, href)
                if not post_url or not is_instagram_post_url(post_url):
                    continue
                if post_url in seen:
                    continue
                seen[post_url] = (title, text, post_date, confident)
                if len(seen) >= args.max_posts_per_account:
                    break
            if len(seen) >= args.max_posts_per_account:
                break
            await page.mouse.wheel(0, 1800)
            await page.wait_for_timeout(int(args.sleep * 1000))
        if not seen:
            summary["no_public_posts_found"] += 1
            print(f"no_public_posts_found: {account.get('societa')} ({account_url})")
            return
        for post_url, (title, text, post_date, confident) in seen.items():
            summary["candidate_urls_found"] += 1
            range_state = in_date_range(post_date, date_from, date_to)
            if post_date and confident and range_state is True:
                status = "ok"
            elif post_date and range_state is False:
                status = "date_out_of_range"
            elif post_date:
                status = "date_uncertain"
            else:
                status = "candidate"
            if not should_insert_status(status, args.keep_out_of_range):
                summary["skipped_out_of_range"] += 1
                continue
            result = upsert_social_post(
                args.db,
                id_societa=account.get("id_societa"),
                societa=account.get("societa") or "Unknown society",
                platform="instagram",
                account_url=account_url,
                post_url=post_url,
                post_id=instagram_post_id(post_url),
                post_date=post_date,
                title=title,
                text=text,
                collection_method="playwright",
                status=status,
                error_message=None if status != "date_uncertain" else "Date parsed from uncertain visible timestamp.",
            )
            if result in {"inserted", "updated"}:
                summary["inserted_updated"] += 1
    except Exception as exc:
        summary["failed"] += 1
        print(f"failed: {account.get('societa')} ({account_url}): {exc}")
    finally:
        await page.close()


async def collect_async(args):
    ensure_db(args.db)
    date_from, date_to, _source = resolve_range_for_args(args)
    accounts = filtered_accounts(args.db, "instagram", args.societa)
    summary = {
        "accounts_scanned": 0,
        "candidate_urls_found": 0,
        "inserted_updated": 0,
        "skipped_out_of_range": 0,
        "login_required": 0,
        "temporarily_blocked": 0,
        "no_public_posts_found": 0,
        "failed": 0,
    }
    if importlib.util.find_spec("playwright") is None:
        summary["failed"] = len(accounts)
        print("failed: Playwright is not installed; install the playwright package and Chromium browser to run this collector.")
        print("Instagram public collection summary")
        for label, key in (
            ("accounts scanned", "accounts_scanned"),
            ("candidate URLs found", "candidate_urls_found"),
            ("inserted/updated rows", "inserted_updated"),
            ("skipped out-of-range", "skipped_out_of_range"),
            ("login_required accounts", "login_required"),
            ("temporarily_blocked accounts", "temporarily_blocked"),
            ("no_public_posts_found accounts", "no_public_posts_found"),
            ("failed accounts", "failed"),
        ):
            print(f"- {label}: {summary[key]}")
        return 0
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=not args.headful)
        except Exception as exc:
            summary["failed"] = len(accounts)
            print(f"failed: could not launch Playwright Chromium: {exc}")
            browser = None
        if browser is not None:
            for account in accounts:
                summary["accounts_scanned"] += 1
                await scan_account(browser, account, args, date_from, date_to, summary)
            await browser.close()
    print("Instagram public collection summary")
    for label, key in (
        ("accounts scanned", "accounts_scanned"),
        ("candidate URLs found", "candidate_urls_found"),
        ("inserted/updated rows", "inserted_updated"),
        ("skipped out-of-range", "skipped_out_of_range"),
        ("login_required accounts", "login_required"),
        ("temporarily_blocked accounts", "temporarily_blocked"),
        ("no_public_posts_found accounts", "no_public_posts_found"),
        ("failed accounts", "failed"),
    ):
        print(f"- {label}: {summary[key]}")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Collect visible public Instagram post links with Playwright.")
    parser.add_argument("--db", default="data/social_wall.db", help="SQLite database path")
    parser.add_argument("--date-from", help="Start date, inclusive, as YYYY-MM-DD; defaults to competition min date")
    parser.add_argument("--date-to", help="End date, inclusive, as YYYY-MM-DD; defaults to competition max date")
    parser.add_argument("--competition-code", help="Limit inferred date range to one competition code")
    parser.add_argument("--societa", help="Optional case-insensitive society name substring")
    parser.add_argument("--headful", action="store_true", help="Run Chromium with a visible browser window")
    parser.add_argument("--max-scrolls", type=int, default=5, help="Maximum page scrolls per account")
    parser.add_argument("--max-posts-per-account", type=int, default=20, help="Maximum candidate post links per account")
    parser.add_argument("--sleep", type=float, default=1.5, help="Seconds to wait after each scroll")
    parser.add_argument("--keep-out-of-range", action="store_true", help="Store date_out_of_range candidates")
    args = parser.parse_args()
    if args.max_scrolls < 1 or args.max_posts_per_account < 1:
        raise SystemExit("--max-scrolls and --max-posts-per-account must be at least 1")
    try:
        raise SystemExit(asyncio.run(collect_async(args)))
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
