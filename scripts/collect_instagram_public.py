#!/usr/bin/env python3
"""Best-effort public Instagram collector using Playwright Chromium."""

import argparse
import asyncio
import importlib.util
import json
import re
from urllib.parse import urlsplit

from collector_utils import (
    absolute_url,
    clean_card_text,
    click_cookie_buttons,
    filtered_accounts,
    in_date_range,
    page_text_limited,
    parse_visible_date,
    resolve_range_for_args,
    short_text,
    first_useful_line,
    canonicalize_url_without_query_noise,
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


def merge_detail(link_title, link_text, link_date, link_confident, detail):
    title = detail.get("title") or link_title
    text = detail.get("text") or link_text
    post_date = detail.get("post_date") or link_date
    confident = detail.get("confident") if detail.get("post_date") else link_confident
    return title, text, post_date, confident, detail.get("author"), detail.get("error_message")


async def first_meta_content(page, selectors):
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if await locator.count():
                value = await locator.get_attribute("content", timeout=1200)
                if value:
                    return value
        except Exception:
            continue
    return None


async def json_ld_date(page):
    try:
        scripts = await page.locator("script[type='application/ld+json']").all_inner_texts(timeout=2500)
    except Exception:
        return None, False
    stack = []
    for raw in scripts:
        try:
            stack.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    while stack:
        node = stack.pop(0)
        if isinstance(node, list):
            stack.extend(node)
            continue
        if not isinstance(node, dict):
            continue
        for key in ("datePublished", "uploadDate", "dateCreated"):
            post_date, confident = parse_visible_date(node.get(key))
            if post_date:
                return post_date, confident
        for value in node.values():
            if isinstance(value, (dict, list)):
                stack.append(value)
    return None, False


async def extract_instagram_detail(detail_page, post_url):
    detail = {"post_date": None, "confident": False, "text": None, "title": None, "author": None, "error_message": None}
    try:
        await detail_page.goto(post_url, wait_until="domcontentloaded", timeout=45000)
        await click_cookie_buttons(detail_page)
        body_text = await page_text_limited(detail_page)
        if BLOCK_RE.search(body_text):
            detail["error_message"] = "Instagram detail page blocked or challenged."
            return detail, "blocked"
        if LOGIN_RE.search(body_text) and not await detail_page.locator("time, article").count():
            detail["error_message"] = "Instagram detail page requires login."
            return detail, "login"

        summary_text = await first_meta_content(
            detail_page,
            ["meta[property='og:description']", "meta[name='description']", "meta[property='twitter:description']"],
        )
        if summary_text:
            detail["text"] = short_text(summary_text, 2000)
            detail["title"] = short_text(summary_text, 90)
        try:
            article = detail_page.locator("article").first
            if await article.count():
                article_text = short_text(await article.inner_text(timeout=2500), 2000)
                if article_text:
                    detail["text"] = article_text
                    detail["title"] = article_text[:90]
        except Exception:
            pass
        try:
            author = await detail_page.locator("article header a[href^='/'], article a[role='link']").first.inner_text(timeout=1500)
            detail["author"] = short_text(author, 200)
        except Exception:
            pass

        for selector, attr in (("time[datetime]", "datetime"),):
            try:
                value = await detail_page.locator(selector).first.get_attribute(attr, timeout=2500)
            except Exception:
                value = None
            post_date, confident = parse_visible_date(value)
            if post_date:
                detail.update({"post_date": post_date, "confident": confident})
                return detail, None
        meta_date = await first_meta_content(detail_page, ["meta[property='article:published_time']"])
        post_date, confident = parse_visible_date(meta_date)
        if post_date:
            detail.update({"post_date": post_date, "confident": confident})
            return detail, None
        post_date, confident = await json_ld_date(detail_page)
        if post_date:
            detail.update({"post_date": post_date, "confident": confident})
        return detail, None
    except Exception as exc:
        detail["error_message"] = f"Instagram detail extraction failed: {exc}"
        return detail, "failed"


INSTAGRAM_CARD_SELECTORS = (
    "article",
    'div[role="main"] article',
    'div:has(a[href*="/p/"])',
    'div:has(a[href*="/reel/"])',
    'div:has(a[href*="/tv/"])',
)
INSTAGRAM_DATE_TEXT_RE = re.compile(
    r"\b(20\d{2}-\d{1,2}-\d{1,2}|\d{1,2}[/-]\d{1,2}[/-]20\d{2}|"
    r"\d{1,2}\s+(?:gen|feb|mar|apr|mag|giu|lug|ago|set|sett|ott|nov|dic|gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|settembre|ottobre|novembre|dicembre|jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|january|february|march|april|june|july|august|september|october|november|december)|"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|january|february|march|april|june|july|august|september|october|november|december)\s+\d{1,2}|"
    r"ieri|oggi|yesterday|today|\d+\s*(?:h|d|giorni?|ore?|settimane?|weeks?|days?)(?:\s+fa|\s+ago)?)\b",
    re.I,
)


def canonicalize_instagram_post_url(url):
    return canonicalize_url_without_query_noise(url)


async def date_from_instagram_card(card, date_from, date_to, resolve_relative):
    uncertain = (None, False)
    probes = (("time[datetime]", "datetime"), ("a[aria-label]", "aria-label"), ("span[aria-label]", "aria-label"), ("[aria-label]", "aria-label"))
    for selector, attr in probes:
        try:
            nodes = card.locator(selector)
            count = min(await nodes.count(), 40)
        except Exception:
            count = 0
        for idx in range(count):
            try:
                value = await nodes.nth(idx).get_attribute(attr, timeout=500)
            except Exception:
                value = None
            post_date, confident = parse_visible_date(value, date_from, date_to, resolve_relative)
            if post_date and confident:
                return post_date, True
            if post_date and not uncertain[0]:
                uncertain = (post_date, False)

    try:
        text = await card.inner_text(timeout=1200)
    except Exception:
        text = ""
    for line in [line.strip() for line in text.splitlines() if line.strip()][:80]:
        if not INSTAGRAM_DATE_TEXT_RE.search(line):
            continue
        post_date, confident = parse_visible_date(line, date_from, date_to, resolve_relative)
        if post_date and confident:
            return post_date, True
        if post_date and not uncertain[0]:
            uncertain = (post_date, False)
    return uncertain


async def extract_instagram_card(card, account_url, date_from, date_to, resolve_relative):
    try:
        text_raw = await card.inner_text(timeout=1200)
    except Exception:
        text_raw = ""
    text = clean_card_text(text_raw, 2000)
    title = first_useful_line(text_raw, 120)
    post_urls = []
    try:
        links = card.locator("a[href]")
        link_count = min(await links.count(), 80)
    except Exception:
        link_count = 0
    for idx in range(link_count):
        try:
            href = await links.nth(idx).get_attribute("href", timeout=500)
        except Exception:
            href = None
        post_url = absolute_url(account_url, href)
        if post_url and is_instagram_post_url(post_url):
            post_urls.append(canonicalize_instagram_post_url(post_url))
    if not post_urls:
        return None
    post_url = sorted(set(post_urls), key=len)[0]
    post_date, confident = await date_from_instagram_card(card, date_from, date_to, resolve_relative)
    return {"post_url": post_url, "title": title, "text": text, "post_date": post_date, "confident": confident}


async def scan_visible_instagram_cards(page, account_url, args, date_from, date_to, seen, summary):
    for selector in INSTAGRAM_CARD_SELECTORS:
        try:
            cards = page.locator(selector)
            count = min(await cards.count(), 100)
        except Exception:
            count = 0
        for idx in range(count):
            summary["cards_scanned"] += 1
            card_data = await extract_instagram_card(cards.nth(idx), account_url, date_from, date_to, args.resolve_relative_dates)
            if not card_data:
                continue
            summary["cards_with_post_url"] += 1
            if card_data["post_date"]:
                summary["cards_with_date"] += 1
                if card_data["confident"]:
                    summary["cards_with_confident_date"] += 1
                    summary["visible_dates_extracted"] += 1
            post_url = card_data["post_url"]
            if post_url not in seen:
                seen[post_url] = card_data
            if len(seen) >= args.max_posts_per_account:
                return


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
        for _scroll_no in range(max(1, args.max_scrolls)):
            await scan_visible_instagram_cards(page, account_url, args, date_from, date_to, seen, summary)
            if len(seen) >= args.max_posts_per_account:
                break
            await page.mouse.wheel(0, 1800)
            await page.wait_for_timeout(int(args.sleep * 1000))
        if not seen:
            summary["no_public_posts_found"] += 1
            print(f"no_public_posts_found: {account.get('societa')} ({account_url})")
            return

        detail_page = None
        if args.open_detail_for_missing_date:
            detail_page = await browser.new_page(viewport={"width": 1365, "height": 900})
        try:
            for post_url, card_data in seen.items():
                summary["candidate_urls_found"] += 1
                title = card_data.get("title")
                text = card_data.get("text")
                post_date = card_data.get("post_date")
                confident = card_data.get("confident", False)
                author = None
                error_message = None

                if args.open_detail_for_missing_date and not post_date and detail_page is not None:
                    summary["detail_pages_opened"] += 1
                    detail, failure = await extract_instagram_detail(detail_page, post_url)
                    if failure == "login":
                        summary["login_required"] += 1
                        summary["login_block_failures"] += 1
                    elif failure == "blocked":
                        summary["temporarily_blocked"] += 1
                        summary["login_block_failures"] += 1
                    elif failure == "failed":
                        summary["failed"] += 1
                    title, text, post_date, confident, author, error_message = merge_detail(
                        title, text, post_date, confident, detail
                    )

                if post_date:
                    summary["dates_extracted"] += 1
                else:
                    summary["candidates_without_date"] += 1
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
                if status == "ok":
                    summary["rows_ok"] += 1
                elif status in {"date_uncertain", "candidate"}:
                    summary["left_uncertain_candidate"] += 1
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
                    author=author,
                    collection_method="playwright_visible_card",
                    status=status,
                    error_message=error_message if status != "ok" else None,
                )
                if result in {"inserted", "updated"}:
                    summary["inserted_updated"] += 1
        finally:
            if detail_page is not None:
                await detail_page.close()
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
        "cards_scanned": 0,
        "cards_with_post_url": 0,
        "cards_with_date": 0,
        "cards_with_confident_date": 0,
        "visible_dates_extracted": 0,
        "candidates_without_date": 0,
        "inserted_updated": 0,
        "detail_pages_opened": 0,
        "dates_extracted": 0,
        "rows_ok": 0,
        "left_uncertain_candidate": 0,
        "skipped_out_of_range": 0,
        "login_block_failures": 0,
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
            ("visible cards scanned", "cards_scanned"),
            ("cards with post URL", "cards_with_post_url"),
            ("cards with any printed date", "cards_with_date"),
            ("cards with confident printed date", "cards_with_confident_date"),
            ("candidate URLs found", "candidate_urls_found"),
            ("detail pages opened", "detail_pages_opened"),
            ("visible dates extracted", "visible_dates_extracted"),
            ("dates extracted", "dates_extracted"),
            ("candidates without date", "candidates_without_date"),
            ("rows updated to ok", "rows_ok"),
            ("rows left date_uncertain/candidate", "left_uncertain_candidate"),
            ("inserted/updated rows", "inserted_updated"),
            ("skipped out-of-range", "skipped_out_of_range"),
            ("login/block failures", "login_block_failures"),
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
        ("visible cards scanned", "cards_scanned"),
        ("cards with post URL", "cards_with_post_url"),
        ("cards with any printed date", "cards_with_date"),
        ("cards with confident printed date", "cards_with_confident_date"),
        ("candidate URLs found", "candidate_urls_found"),
        ("detail pages opened", "detail_pages_opened"),
        ("visible dates extracted", "visible_dates_extracted"),
        ("dates extracted", "dates_extracted"),
        ("candidates without date", "candidates_without_date"),
        ("rows updated to ok", "rows_ok"),
        ("rows left date_uncertain/candidate", "left_uncertain_candidate"),
        ("inserted/updated rows", "inserted_updated"),
        ("skipped out-of-range", "skipped_out_of_range"),
        ("login/block failures", "login_block_failures"),
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
    parser.add_argument("--verbose", action="store_true", help="Print additional diagnostics")
    parser.add_argument("--resolve-relative-dates", action="store_true", help="Resolve relative timestamps such as 2 giorni fa or 3 d")
    parser.add_argument("--open-detail-for-missing-date", action="store_true", help="Open post detail pages only for visible cards that have no date")
    args = parser.parse_args()
    if args.max_scrolls < 1 or args.max_posts_per_account < 1:
        raise SystemExit("--max-scrolls and --max-posts-per-account must be at least 1")
    try:
        raise SystemExit(asyncio.run(collect_async(args)))
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
