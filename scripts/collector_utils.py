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
    "gen": 1,
    "febbraio": 2,
    "feb": 2,
    "marzo": 3,
    "mar": 3,
    "aprile": 4,
    "apr": 4,
    "maggio": 5,
    "mag": 5,
    "giugno": 6,
    "giu": 6,
    "luglio": 7,
    "lug": 7,
    "agosto": 8,
    "ago": 8,
    "settembre": 9,
    "set": 9,
    "sett": 9,
    "ottobre": 10,
    "ott": 10,
    "novembre": 11,
    "nov": 11,
    "dicembre": 12,
    "dic": 12,
}

ENGLISH_MONTHS = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}

ALL_MONTHS = {**ENGLISH_MONTHS, **ITALIAN_MONTHS}
MONTH_RE = "|".join(sorted((re.escape(name) for name in ALL_MONTHS), key=len, reverse=True))


def _coerce_date(value):
    if value is None:
        return None
    if hasattr(value, "date") and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _date_from_parts(year, month, day):
    try:
        return datetime(int(year), int(month), int(day)).date()
    except (TypeError, ValueError):
        return None


def _resolve_missing_year(day, month, date_from=None, date_to=None):
    """Resolve day/month with no year; returns (iso_date, confident)."""
    start = _coerce_date(date_from)
    end = _coerce_date(date_to)
    current_year = datetime.now(timezone.utc).date().year
    candidate_years = {current_year - 1, current_year, current_year + 1}
    if start:
        candidate_years.update({start.year - 1, start.year, start.year + 1})
    if end:
        candidate_years.update({end.year - 1, end.year, end.year + 1})
    candidates = []
    for year in sorted(candidate_years):
        candidate = _date_from_parts(year, month, day)
        if candidate:
            candidates.append(candidate)
    if start and end:
        in_range = [candidate for candidate in candidates if start <= candidate <= end]
        if len(in_range) == 1:
            return in_range[0].isoformat(), True
        return None, False
    return None, False


def _parse_relative_date(lower, resolve_relative):
    today = datetime.now(timezone.utc).date()
    if re.search(r"\b(ieri|yesterday)\b", lower):
        return (today - timedelta(days=1)).isoformat(), False
    if re.search(r"\b(oggi|today)\b", lower):
        return today.isoformat(), False
    if not resolve_relative:
        return None, False

    amount_match = re.search(
        r"\b(\d+)\s*(secondi?|sec|s|minuti?|mins?|m|ore?|hours?|hrs?|h|giorni?|days?|d|settimane?|weeks?|w|mesi|months?|mo)\b(?:\s+fa|\s+ago)?",
        lower,
    )
    if not amount_match:
        return None, False
    amount = int(amount_match.group(1))
    unit = amount_match.group(2)
    if unit.startswith(("mes", "month", "mo")):
        delta = timedelta(days=amount * 30)
    elif unit.startswith(("sett", "week", "w")):
        delta = timedelta(days=amount * 7)
    elif unit.startswith(("giorn", "day", "d")):
        delta = timedelta(days=amount)
    elif unit.startswith(("second", "sec", "s", "min", "m", "or", "hour", "hr", "h")):
        delta = timedelta(days=0)
    else:
        return None, False
    return (today - delta).isoformat(), False


def parse_visible_date(text, date_from=None, date_to=None, resolve_relative=False):
    """Best-effort parser for visible social timestamps; returns (date, confident).

    Dates printed with a year are confident. Dates without a year are confident only
    when the effective range resolves them to exactly one possible year. Relative
    timestamps are unresolved by default except for today/yesterday, which remain
    non-confident because they depend on collection time.
    """
    if not text:
        return None, False
    raw = " ".join(str(text).strip().split())
    if not raw:
        return None, False
    lower = raw.lower()

    iso_datetime = re.search(r"\b(20\d{2}-\d{1,2}-\d{1,2})(?:[T\s]\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?", raw)
    if iso_datetime:
        value = iso_datetime.group(0)
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat(), True
        except ValueError:
            day = _date_from_parts(*iso_datetime.group(1).split("-"))
            if day:
                return day.isoformat(), True

    iso = re.search(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b", raw)
    if iso:
        day = _date_from_parts(iso.group(1), iso.group(2), iso.group(3))
        if day:
            return day.isoformat(), True

    numeric = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](20\d{2})\b", raw)
    if numeric:
        first, second, year = (int(numeric.group(1)), int(numeric.group(2)), int(numeric.group(3)))
        # Prefer Italian/European day-month-year. Parse US month-day-year only when
        # the European interpretation is impossible and the value is unambiguous.
        day = _date_from_parts(year, second, first)
        if day:
            return day.isoformat(), True
        if first <= 12 and second > 12:
            day = _date_from_parts(year, first, second)
            if day:
                return day.isoformat(), True

    month_day_year = re.search(rf"\b({MONTH_RE})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?[,]?\s+(20\d{{2}})\b", lower)
    if month_day_year:
        month_name, day_num, year = month_day_year.groups()
        day = _date_from_parts(year, ALL_MONTHS[month_name.rstrip('.')], day_num)
        if day:
            return day.isoformat(), True

    day_month_year = re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:di\s+)?({MONTH_RE})\.?[,]?\s+(?:alle\s+\d{{1,2}}[:.]\d{{2}}\s+)?(20\d{{2}})\b", lower)
    if day_month_year:
        day_num, month_name, year = day_month_year.groups()
        day = _date_from_parts(year, ALL_MONTHS[month_name.rstrip('.')], day_num)
        if day:
            return day.isoformat(), True

    day_month_no_year = re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:di\s+)?({MONTH_RE})\.?\b(?:\s+alle\s+\d{{1,2}}[:.]\d{{2}})?", lower)
    if day_month_no_year:
        day_num, month_name = day_month_no_year.groups()
        resolved, confident = _resolve_missing_year(int(day_num), ALL_MONTHS[month_name.rstrip('.')], date_from, date_to)
        if resolved:
            return resolved, confident
        return None, False

    month_day_no_year = re.search(rf"\b({MONTH_RE})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", lower)
    if month_day_no_year:
        month_name, day_num = month_day_no_year.groups()
        resolved, confident = _resolve_missing_year(int(day_num), ALL_MONTHS[month_name.rstrip('.')], date_from, date_to)
        if resolved:
            return resolved, confident
        return None, False

    return _parse_relative_date(lower, resolve_relative)


def clean_card_text(value, limit=2000):
    """Collapse and trim visible card text without storing heavy debug content."""
    if not value:
        return None
    lines = []
    ignored = {
        "like", "comment", "share", "mi piace", "commenta", "condividi", "send", "invia",
        "see more", "altro", "most relevant is selected", "all comments", "tutti i commenti",
    }
    for line in str(value).splitlines():
        cleaned = " ".join(line.split())
        if not cleaned:
            continue
        if cleaned.casefold() in ignored:
            continue
        if lines and cleaned == lines[-1]:
            continue
        lines.append(cleaned)
    return short_text(" ".join(lines), limit)


def first_useful_line(value, limit=120):
    if not value:
        return None
    for line in str(value).splitlines():
        cleaned = " ".join(line.split())
        if cleaned and len(cleaned) > 1:
            return short_text(cleaned, limit)
    return short_text(value, limit)


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
