#!/usr/bin/env python3
"""Ingest manually curated social posts into the social wall database."""

import argparse
import csv
import html
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus, urlsplit, parse_qs

from db import ensure_db, normalize_url, upsert_social_post

SUPPORTED_PLATFORMS = {"facebook", "instagram", "youtube"}


def clean(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def infer_platform(url):
    value = (url or "").lower()
    if "youtu.be" in value or "youtube.com" in value:
        return "youtube"
    if "instagram.com" in value:
        return "instagram"
    if "facebook.com" in value or "fb.watch" in value:
        return "facebook"
    return None


def normalize_platform(value, post_url):
    platform = clean(value)
    if platform:
        platform = platform.lower().strip()
        aliases = {"fb": "facebook", "ig": "instagram", "yt": "youtube"}
        platform = aliases.get(platform, platform)
    return platform or infer_platform(post_url)


def normalize_date(value):
    raw = clean(value)
    if not raw:
        return None, "date_missing"

    candidate = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
        return parsed.date().isoformat(), "ok"
    except ValueError:
        pass

    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat(), "ok"
        except ValueError:
            continue
    return None, "date_invalid"


def youtube_video_id(url):
    value = clean(url)
    if not value:
        return None
    candidate = value if "://" in value else f"https://{value}"
    try:
        parts = urlsplit(candidate)
    except ValueError:
        return None

    host = parts.netloc.lower().removeprefix("www.").removeprefix("m.")
    path_parts = [part for part in parts.path.split("/") if part]
    if host == "youtu.be" and path_parts:
        return path_parts[0]
    if host.endswith("youtube.com"):
        query_video = parse_qs(parts.query).get("v", [None])[0]
        if query_video:
            return query_video
        if len(path_parts) >= 2 and path_parts[0] in {"shorts", "embed"}:
            return path_parts[1]
    return None


def youtube_embed_html(video_id):
    if not video_id:
        return None
    safe_id = html.escape(video_id, quote=True)
    return (
        '<iframe width="560" height="315" '
        f'src="https://www.youtube.com/embed/{safe_id}" '
        'title="YouTube video player" frameborder="0" '
        'allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" '
        'allowfullscreen></iframe>'
    )


def facebook_embed_html(post_url):
    if not post_url:
        return None
    encoded = quote_plus(post_url)
    escaped = html.escape(post_url, quote=True)
    return (
        '<iframe '
        f'src="https://www.facebook.com/plugins/post.php?href={encoded}&show_text=true&width=500" '
        'width="500" height="650" style="border:none;overflow:hidden" scrolling="no" '
        'frameborder="0" allowfullscreen="true" '
        'allow="autoplay; clipboard-write; encrypted-media; picture-in-picture; web-share">'
        f'<a href="{escaped}">Facebook post</a></iframe>'
    )


def build_embed(platform, post_url, post_id):
    if platform == "youtube":
        return youtube_embed_html(post_id)
    if platform == "facebook":
        return facebook_embed_html(post_url)
    return None


def row_value(row, key):
    return clean(row.get(key))


def ingest(csv_path, db_path):
    ensure_db(db_path)
    summary = {
        "rows_read": 0,
        "inserted": 0,
        "updated": 0,
        "skipped": 0,
        "date_missing": 0,
        "date_invalid": 0,
        "unsupported_platform": 0,
        "errors": 0,
    }

    with Path(csv_path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row_number, row in enumerate(reader, start=2):
            summary["rows_read"] += 1
            post_url = normalize_url(row_value(row, "post_url"))
            societa = row_value(row, "societa") or "Unknown society"
            id_societa = row_value(row, "id_societa")
            platform = normalize_platform(row_value(row, "platform"), post_url)

            if not post_url:
                summary["skipped"] += 1
                print(f"Row {row_number}: skipped because post_url is empty")
                continue
            if platform not in SUPPORTED_PLATFORMS:
                summary["unsupported_platform"] += 1
                print(f"Row {row_number}: unsupported or unknown platform for {post_url}")
                continue

            post_date, status = normalize_date(row_value(row, "post_date"))
            if status != "ok":
                summary[status] += 1

            post_id = youtube_video_id(post_url) if platform == "youtube" else None
            embed_html = build_embed(platform, post_url, post_id)
            error_message = None if status == "ok" else f"Manual row {row_number}: {status}"

            try:
                result = upsert_social_post(
                    db_path,
                    id_societa=id_societa,
                    societa=societa,
                    platform=platform,
                    account_url=normalize_url(row_value(row, "account_url")),
                    post_url=post_url,
                    post_id=post_id,
                    post_date=post_date,
                    title=row_value(row, "title"),
                    text=row_value(row, "text"),
                    thumbnail_url=row_value(row, "thumbnail_url"),
                    screenshot_path=row_value(row, "screenshot_path"),
                    embed_html=embed_html,
                    collection_method="manual",
                    status=status,
                    error_message=error_message,
                )
                summary[result] += 1
            except Exception as exc:  # Keep manual ingestion resilient row-by-row.
                summary["errors"] += 1
                print(f"Row {row_number}: failed to ingest {post_url}: {exc}")

    print("Manual post ingestion summary")
    for key, value in summary.items():
        print(f"- {key}: {value}")


def main():
    parser = argparse.ArgumentParser(description="Ingest manually curated social post CSV rows.")
    parser.add_argument("--csv", default="data/raw/manual_posts.csv", help="Manual posts CSV path")
    parser.add_argument("--db", default="data/social_wall.db", help="SQLite database path")
    args = parser.parse_args()
    ingest(args.csv, args.db)


if __name__ == "__main__":
    main()
