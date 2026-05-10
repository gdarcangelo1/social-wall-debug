#!/usr/bin/env python3
"""Export social wall SQLite data to the static frontend JSON file."""

import argparse
import json
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path

from db import connect_db, ensure_db, get_competition_date_range, utc_now

DEFAULT_DB = "data/social_wall.db"
DEFAULT_OUT = "data/posts.json"

SOCIETY_FIELDS = ("id_societa", "societa", "source_detail_url", "logo_path", "notes")
ACCOUNT_FIELDS = (
    "id_societa",
    "societa",
    "platform",
    "account_url",
    "normalized_url",
    "status",
    "notes",
)
POST_FIELDS = (
    "id_societa",
    "societa",
    "platform",
    "account_url",
    "post_url",
    "post_id",
    "post_date",
    "title",
    "text",
    "author",
    "media_url",
    "thumbnail_url",
    "screenshot_path",
    "embed_html",
    "collection_method",
    "discovery_rank",
    "collection_run_id",
    "item_type",
    "status",
    "error_message",
)
MATCH_FIELDS = (
    "competition_code",
    "match_id",
    "date",
    "time",
    "home_team",
    "away_team",
    "home_score",
    "away_score",
    "result_text",
    "venue",
    "source_url",
    "status",
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Export societies, accounts, posts and competition matches from SQLite to JSON."
    )
    parser.add_argument("--db", default=DEFAULT_DB, help=f"SQLite database path (default: {DEFAULT_DB})")
    parser.add_argument("--out", default=DEFAULT_OUT, help=f"Output JSON path (default: {DEFAULT_OUT})")
    parser.add_argument("--date-from", help="Include posts/matches on or after YYYY-MM-DD")
    parser.add_argument("--date-to", help="Include posts/matches on or before YYYY-MM-DD")
    parser.add_argument("--societa", help="Case-insensitive substring filter for society name")
    parser.add_argument("--platform", help="Comma-separated platform filter for accounts/posts, e.g. facebook,instagram")
    parser.add_argument("--status", help="Comma-separated status filter for accounts/posts/matches")
    parser.add_argument("--competition-code", help="Filter matches by competition code")
    return parser.parse_args()


def split_filter(value):
    if not value:
        return None
    items = [item.strip().lower() for item in value.split(",") if item.strip()]
    return set(items) or None


def validate_date(value, label):
    if not value:
        return None
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise SystemExit(f"Invalid {label}: {value!r}. Expected YYYY-MM-DD.") from exc
    return value


def select_rows(conn, table, fields):
    columns = ", ".join(fields)
    try:
        rows = conn.execute(f"SELECT {columns} FROM {table}").fetchall()
    except sqlite3.Error as exc:
        print(f"Warning: could not read table {table}: {exc}")
        return []
    return [dict(row) for row in rows]


def text_value(row, key):
    value = row.get(key)
    if value is None:
        return ""
    return str(value)


def normalized_date(value):
    if value is None:
        return None
    value = str(value).strip()
    if len(value) >= 10:
        candidate = value[:10]
        try:
            datetime.strptime(candidate, "%Y-%m-%d")
            return candidate
        except ValueError:
            return None
    return None


def matches_date_filter(row, field, date_from, date_to):
    if not date_from and not date_to:
        return True
    row_date = normalized_date(row.get(field))
    if row_date is None:
        return False
    if date_from and row_date < date_from:
        return False
    if date_to and row_date > date_to:
        return False
    return True


def matches_text_filter(row, key, substring):
    if not substring:
        return True
    return substring.lower() in text_value(row, key).lower()


def matches_set_filter(row, key, accepted):
    if not accepted:
        return True
    return text_value(row, key).strip().lower() in accepted


def filter_societies(rows, societa):
    return [row for row in rows if matches_text_filter(row, "societa", societa)]


def filter_accounts(rows, societa, platforms, statuses):
    return [
        row
        for row in rows
        if matches_text_filter(row, "societa", societa)
        and matches_set_filter(row, "platform", platforms)
        and matches_set_filter(row, "status", statuses)
    ]


def filter_posts(rows, societa, platforms, statuses, date_from, date_to):
    return [
        row
        for row in rows
        if matches_text_filter(row, "societa", societa)
        and matches_set_filter(row, "platform", platforms)
        and matches_set_filter(row, "status", statuses)
        and matches_date_filter(row, "post_date", date_from, date_to)
    ]


def filter_matches(rows, statuses, date_from, date_to, competition_code):
    return [
        row
        for row in rows
        if matches_set_filter(row, "status", statuses)
        and matches_date_filter(row, "date", date_from, date_to)
        and (not competition_code or text_value(row, "competition_code") == str(competition_code))
    ]


def sort_societies(rows):
    return sorted(rows, key=lambda row: (text_value(row, "societa").lower(), text_value(row, "id_societa")))


def sort_accounts(rows):
    return sorted(
        rows,
        key=lambda row: (
            text_value(row, "societa").lower(),
            text_value(row, "platform").lower(),
            text_value(row, "account_url").lower(),
        ),
    )


def date_sort_ordinal(value):
    row_date = normalized_date(value)
    if row_date is None:
        return None
    return datetime.strptime(row_date, "%Y-%m-%d").toordinal()


def sort_posts(rows):
    return sorted(
        rows,
        key=lambda row: (
            date_sort_ordinal(row.get("post_date")) is None,
            -(date_sort_ordinal(row.get("post_date")) or 0),
            text_value(row, "post_url"),
        ),
    )


def sort_matches(rows):
    return sorted(
        rows,
        key=lambda row: (
            normalized_date(row.get("date")) is None,
            normalized_date(row.get("date")) or "9999-12-31",
            text_value(row, "time"),
            text_value(row, "match_id"),
        ),
    )


def count_by(rows, key):
    counter = Counter(text_value(row, key) or "unknown" for row in rows)
    return {name: counter[name] for name in sorted(counter)}


def build_payload(db_path, filters):
    ensure_db(db_path)
    with connect_db(db_path) as conn:
        societies = select_rows(conn, "societies", SOCIETY_FIELDS)
        accounts = select_rows(conn, "social_accounts", ACCOUNT_FIELDS)
        posts = select_rows(conn, "social_posts", POST_FIELDS)
        matches = select_rows(conn, "competition_matches", MATCH_FIELDS)

    societies = sort_societies(filter_societies(societies, filters["societa"]))
    accounts = sort_accounts(filter_accounts(accounts, filters["societa"], filters["platforms"], filters["statuses"]))
    posts = sort_posts(
        filter_posts(
            posts,
            filters["societa"],
            filters["platforms"],
            filters["statuses"],
            filters["date_from"],
            filters["date_to"],
        )
    )
    matches = sort_matches(
        filter_matches(
            matches,
            filters["statuses"],
            filters["date_from"],
            filters["date_to"],
            filters["competition_code"],
        )
    )

    with connect_db(db_path) as conn:
        comp_from, comp_to = get_competition_date_range(conn, filters["competition_code"])

    summary = {
        "societies": len(societies),
        "accounts": len(accounts),
        "posts": len(posts),
        "matches": len(matches),
        "accounts_by_platform": count_by(accounts, "platform"),
        "posts_by_platform": count_by(posts, "platform"),
        "posts_by_status": count_by(posts, "status"),
        "posts_by_society": count_by(posts, "societa"),
        "competition_date_range": {
            "date_from": comp_from.isoformat() if comp_from else None,
            "date_to": comp_to.isoformat() if comp_to else None,
            "competition_code": filters["competition_code"],
        },
    }
    return {
        "generated_at": utc_now(),
        "summary": summary,
        "societies": societies,
        "accounts": accounts,
        "posts": posts,
        "matches": matches,
    }


def write_json(payload, out_path):
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def print_summary(payload, db_path, out_path):
    summary = payload["summary"]
    print("Export complete")
    print(f"Database: {db_path}")
    print(f"Output: {out_path}")
    print(f"Societies: {summary['societies']}")
    print(f"Accounts: {summary['accounts']}")
    print(f"Posts: {summary['posts']}")
    print(f"Matches: {summary['matches']}")
    print(f"Accounts by platform: {json.dumps(summary['accounts_by_platform'], ensure_ascii=False, sort_keys=True)}")
    print(f"Posts by platform: {json.dumps(summary['posts_by_platform'], ensure_ascii=False, sort_keys=True)}")
    print(f"Posts by status: {json.dumps(summary['posts_by_status'], ensure_ascii=False, sort_keys=True)}")
    print(f"Posts by society: {json.dumps(summary['posts_by_society'], ensure_ascii=False, sort_keys=True)}")
    print(f"Competition date range: {json.dumps(summary['competition_date_range'], ensure_ascii=False, sort_keys=True)}")


def main():
    args = parse_args()
    filters = {
        "date_from": validate_date(args.date_from, "--date-from"),
        "date_to": validate_date(args.date_to, "--date-to"),
        "societa": args.societa,
        "platforms": split_filter(args.platform),
        "statuses": split_filter(args.status),
        "competition_code": args.competition_code,
    }
    payload = build_payload(args.db, filters)
    output_path = write_json(payload, args.out)
    print_summary(payload, args.db, output_path)


if __name__ == "__main__":
    main()
