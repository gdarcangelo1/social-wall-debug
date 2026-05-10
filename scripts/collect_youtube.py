#!/usr/bin/env python3
"""Collect YouTube videos for imported society accounts using the YouTube Data API."""

import argparse
import html
import json
import os
from datetime import datetime, time, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import urlopen

from db import ensure_db, normalize_url, query_accounts, upsert_social_post

YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"


def parse_date(value, option_name):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{option_name} must be YYYY-MM-DD") from exc


def iso_boundary(day, end=False):
    clock = time.max if end else time.min
    return datetime.combine(day, clock, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def api_get(path, api_key, params):
    query = dict(params)
    query["key"] = api_key
    url = f"{YOUTUBE_API_BASE}/{path}?{urlencode(query)}"
    try:
        with urlopen(url, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"YouTube API HTTP {exc.code}: {body[:500]}") from exc
    except URLError as exc:
        raise RuntimeError(f"YouTube API request failed: {exc.reason}") from exc


def extract_youtube_identifier(account_url):
    candidate = account_url if "://" in account_url else f"https://{account_url}"
    parts = urlsplit(candidate)
    path_parts = [part for part in parts.path.split("/") if part]
    if not path_parts:
        return "search", account_url

    first = path_parts[0]
    if first == "channel" and len(path_parts) >= 2:
        return "channel", path_parts[1]
    if first.startswith("@"):
        return "handle", first
    if first == "user" and len(path_parts) >= 2:
        return "user", path_parts[1]
    if first == "c" and len(path_parts) >= 2:
        return "custom", path_parts[1]
    if first.startswith("@"):
        return "handle", first
    return "search", path_parts[-1]


def first_item(response):
    items = response.get("items") or []
    return items[0] if items else None


def resolve_channel_id(api_key, account_url):
    kind, value = extract_youtube_identifier(account_url)
    if kind == "channel":
        return value
    if kind == "user":
        response = api_get("channels", api_key, {"part": "id", "forUsername": value, "maxResults": 1})
        item = first_item(response)
        if item:
            return item.get("id")
    if kind == "handle":
        response = api_get("channels", api_key, {"part": "id", "forHandle": value, "maxResults": 1})
        item = first_item(response)
        if item:
            return item.get("id")

    query = value
    response = api_get("search", api_key, {"part": "snippet", "type": "channel", "q": query, "maxResults": 1})
    item = first_item(response)
    if item:
        return (item.get("id") or {}).get("channelId")
    return None


def youtube_embed_html(video_id):
    safe_id = html.escape(video_id, quote=True)
    return (
        '<iframe width="560" height="315" '
        f'src="https://www.youtube.com/embed/{safe_id}" '
        'title="YouTube video player" frameborder="0" '
        'allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" '
        'allowfullscreen></iframe>'
    )


def best_thumbnail(thumbnails):
    for key in ("maxres", "standard", "high", "medium", "default"):
        item = (thumbnails or {}).get(key)
        if item and item.get("url"):
            return item["url"]
    return None


def published_date(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return value[:10]


def search_videos(api_key, channel_id, date_from, date_to, max_results):
    videos = []
    page_token = None
    remaining = max_results
    while remaining > 0:
        params = {
            "part": "snippet",
            "type": "video",
            "channelId": channel_id,
            "order": "date",
            "publishedAfter": iso_boundary(date_from),
            "publishedBefore": iso_boundary(date_to, end=True),
            "maxResults": min(50, remaining),
        }
        if page_token:
            params["pageToken"] = page_token
        response = api_get("search", api_key, params)
        videos.extend(response.get("items") or [])
        remaining = max_results - len(videos)
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return videos[:max_results]


def collect(args):
    ensure_db(args.db)
    api_key = os.environ.get("YOUTUBE_API_KEY")
    if not api_key:
        print("YOUTUBE_API_KEY is not set; YouTube collection skipped.")
        print("Use export YOUTUBE_API_KEY=... and rerun, or pass --require-api-key to fail when absent.")
        print("YouTube collection summary")
        print("- accounts scanned: 0")
        print("- videos inserted/updated: 0")
        print("- failures: 0")
        print("- missing API key: yes")
        return 1 if args.require_api_key else 0

    date_from = parse_date(args.date_from, "--date-from")
    date_to = parse_date(args.date_to, "--date-to")
    if date_to < date_from:
        raise SystemExit("--date-to must be greater than or equal to --date-from")

    accounts = query_accounts(args.db, platform="youtube")
    if args.societa:
        needle = args.societa.casefold()
        accounts = [account for account in accounts if needle in (account.get("societa") or "").casefold()]

    summary = {"accounts_scanned": 0, "videos_upserted": 0, "failures": 0}
    for account in accounts:
        summary["accounts_scanned"] += 1
        account_url = account.get("account_url")
        try:
            channel_id = resolve_channel_id(api_key, account_url)
            if not channel_id:
                raise RuntimeError("could not resolve YouTube channel id")
            videos = search_videos(api_key, channel_id, date_from, date_to, args.max_results)
            for item in videos:
                snippet = item.get("snippet") or {}
                video_id = (item.get("id") or {}).get("videoId")
                if not video_id:
                    continue
                post_url = f"https://www.youtube.com/watch?v={video_id}"
                result = upsert_social_post(
                    args.db,
                    id_societa=account.get("id_societa"),
                    societa=account.get("societa") or "Unknown society",
                    platform="youtube",
                    account_url=normalize_url(account_url),
                    post_url=post_url,
                    post_id=video_id,
                    post_date=published_date(snippet.get("publishedAt")),
                    title=snippet.get("title"),
                    text=snippet.get("description"),
                    author=snippet.get("channelTitle"),
                    thumbnail_url=best_thumbnail(snippet.get("thumbnails")),
                    embed_html=youtube_embed_html(video_id),
                    collection_method="api",
                    status="ok",
                    error_message=None,
                )
                if result in {"inserted", "updated"}:
                    summary["videos_upserted"] += 1
        except Exception as exc:  # Keep one bad account from stopping the run.
            summary["failures"] += 1
            print(f"Failed account {account.get('societa')} ({account_url}): {exc}")

    print("YouTube collection summary")
    print(f"- accounts scanned: {summary['accounts_scanned']}")
    print(f"- videos inserted/updated: {summary['videos_upserted']}")
    print(f"- failures: {summary['failures']}")
    print("- missing API key: no")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Collect YouTube videos for imported social_accounts rows.")
    parser.add_argument("--db", default="data/social_wall.db", help="SQLite database path")
    parser.add_argument("--date-from", required=True, help="Start date, inclusive, as YYYY-MM-DD")
    parser.add_argument("--date-to", required=True, help="End date, inclusive, as YYYY-MM-DD")
    parser.add_argument("--societa", help="Optional case-insensitive society name substring")
    parser.add_argument("--max-results", type=int, default=50, help="Maximum videos per account")
    parser.add_argument("--require-api-key", action="store_true", help="Exit non-zero when YOUTUBE_API_KEY is missing")
    args = parser.parse_args()
    if args.max_results < 1:
        raise SystemExit("--max-results must be at least 1")
    raise SystemExit(collect(args))


if __name__ == "__main__":
    main()
