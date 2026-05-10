#!/usr/bin/env python3
"""Shared helpers for lightweight social collectors."""

import html
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, quote, urljoin, urlsplit, urlunsplit

from db import connect_db, ensure_db, normalize_url, query_accounts, resolve_effective_date_range

POST_STATUSES_TO_INSERT = {"ok", "candidate", "date_uncertain"}


def resolve_range_for_args(args):
    ensure_db(args.db)
    with connect_db(args.db) as conn:
        date_from, date_to, source = resolve_effective_date_range(
            conn, args.date_from, args.date_to, getattr(args, "competition_code", None)
        )
    print(f"Effective date range: {date_from.isoformat()} to {date_to.isoformat()}")
    print(f"Source: {source}")
    return date_from, date_to, source


def filtered_accounts(db_path, platform, societa=None):
    accounts = query_accounts(db_path, platform=platform)
    if societa:
        needle = societa.casefold()
        accounts = [a for a in accounts if needle in (a.get("societa") or "").casefold()]
    return accounts


def short_text(value, limit=700):
    if not value:
        return None
    cleaned = " ".join(str(value).split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def absolute_url(base_url, href):
    if not href:
        return None
    return normalize_url(urljoin(base_url, href))


def in_date_range(post_date, date_from, date_to):
    if not post_date:
        return None
    try:
        day = datetime.strptime(post_date[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    return date_from <= day <= date_to


def should_insert_status(status, keep_out_of_range=False):
    return status in POST_STATUSES_TO_INSERT or (status == "date_out_of_range" and keep_out_of_range)


def facebook_embed(post_url):
    safe = html.escape(quote(post_url, safe=""), quote=True)
    return (
        '<iframe src="https://www.facebook.com/plugins/post.php?href='
        f'{safe}&show_text=true&width=500" width="500" height="650" '
        'style="border:none;overflow:hidden" scrolling="no" frameborder="0" '
        'allowfullscreen="true" allow="autoplay; clipboard-write; encrypted-media; picture-in-picture; web-share"></iframe>'
    )


def youtube_video_id(url):
    candidate = url if "://" in url else f"https://{url}"
    parts = urlsplit(candidate)
    host = parts.netloc.lower()
    path_parts = [p for p in parts.path.split("/") if p]
    if "youtu.be" in host and path_parts:
        return path_parts[0]
    if path_parts and path_parts[0] in {"shorts", "embed", "live"} and len(path_parts) > 1:
        return path_parts[1]
    query = parse_qs(parts.query)
    return (query.get("v") or [None])[0]


def canonicalize_url_without_query_noise(url, keep_query_keys=None):
    keep_query_keys = set(keep_query_keys or [])
    parts = urlsplit(url)
    if keep_query_keys:
        query = parse_qs(parts.query, keep_blank_values=True)
        pairs = []
        for key in keep_query_keys:
            for value in query.get(key, []):
                pairs.append(f"{quote(key)}={quote(value)}")
        new_query = "&".join(pairs)
    else:
        new_query = ""
    path = parts.path.rstrip("/") or parts.path
    return normalize_url(urlunsplit((parts.scheme, parts.netloc, path, new_query, "")))


ITALIAN_MONTHS = {
    "gennaio": 1,
    "febbraio": 2,
    "marzo": 3,
    "aprile": 4,
    "maggio": 5,
    "giugno": 6,
    "luglio": 7,
    "agosto": 8,
    "settembre": 9,
    "ottobre": 10,
    "novembre": 11,
    "dicembre": 12,
}


def parse_visible_date(text):
    """Best-effort parser for visible social timestamps; returns (date, confident)."""
    if not text:
        return None, False
    raw = " ".join(str(text).strip().split())
    if not raw:
        return None, False

    iso_datetime = re.search(r"\b(20\d{2}-\d{1,2}-\d{1,2})(?:[T\s]\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?", raw)
    if iso_datetime:
        try:
            return datetime.fromisoformat(iso_datetime.group(0).replace("Z", "+00:00")).date().isoformat(), True
        except ValueError:
            try:
                return datetime.strptime(iso_datetime.group(1), "%Y-%m-%d").date().isoformat(), True
            except ValueError:
                pass

    for fmt in (
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%B %d, %Y",
        "%b %d, %Y",
        "%d %B %Y",
        "%d %b %Y",
        "%d %B, %Y",
        "%d %b, %Y",
    ):
        try:
            return datetime.strptime(raw[:40], fmt).date().isoformat(), True
        except ValueError:
            pass

    iso = re.search(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b", raw)
    if iso:
        y, m, d = iso.groups()
        try:
            return datetime(int(y), int(m), int(d)).date().isoformat(), True
        except ValueError:
            pass
    euro = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](20\d{2})\b", raw)
    if euro:
        d, m, y = euro.groups()
        try:
            return datetime(int(y), int(m), int(d)).date().isoformat(), True
        except ValueError:
            pass

    lower = raw.lower()
    month_names = "|".join(ITALIAN_MONTHS)
    italian = re.search(rf"\b(\d{{1,2}})\s+(?:di\s+)?({month_names})\s+(20\d{{2}})\b", lower)
    if italian:
        d, month_name, y = italian.groups()
        try:
            return datetime(int(y), ITALIAN_MONTHS[month_name], int(d)).date().isoformat(), True
        except ValueError:
            pass

    relative = re.search(
        r"\b(\d+\s*)?(s|min|minute|minutes|m|h|hr|hour|hours|ora|ore|giorn|day|days|d|sett|week|weeks|settimane|mese|mesi|month|months)\b",
        lower,
    )
    if relative:
        return None, False
    if "yesterday" in lower or "ieri" in lower:
        return (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat(), False
    if "today" in lower or "oggi" in lower:
        return datetime.now(timezone.utc).date().isoformat(), False
    return None, False


async def click_cookie_buttons(page):
    labels = [
        "Allow all cookies", "Accept all", "Accept", "Only allow essential cookies", "Consenti tutti i cookie",
        "Accetta tutti", "Accetta", "Rifiuta cookie facoltativi", "Decline optional cookies",
    ]
    for label in labels:
        locator = page.get_by_role("button", name=re.compile(re.escape(label), re.I))
        if await locator.count():
            try:
                await locator.first.click(timeout=1500)
                await page.wait_for_timeout(500)
                return True
            except Exception:
                continue
    return False


async def page_text_limited(page, limit=20000):
    try:
        text = await page.locator("body").inner_text(timeout=5000)
    except Exception:
        return ""
    return text[:limit]
