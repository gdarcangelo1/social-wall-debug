#!/usr/bin/env python3
"""SQLite helpers for the social wall debug data layer."""

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "gbraid",
    "wbraid",
    "msclkid",
    "mc_cid",
    "mc_eid",
    "igshid",
    "si",
    "feature",
}


def utc_now():
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def connect_db(db_path):
    """Connect to SQLite using a repository-relative or absolute path."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def ensure_db(db_path):
    """Create the SQLite database and required tables if they do not exist."""
    with connect_db(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS societies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                id_societa TEXT UNIQUE,
                societa TEXT NOT NULL,
                source_detail_url TEXT,
                logo_path TEXT,
                notes TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS social_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                id_societa TEXT,
                societa TEXT NOT NULL,
                platform TEXT NOT NULL,
                account_url TEXT NOT NULL,
                normalized_url TEXT,
                status TEXT DEFAULT 'ok',
                notes TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(id_societa, platform, normalized_url)
            );

            CREATE TABLE IF NOT EXISTS social_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                id_societa TEXT,
                societa TEXT NOT NULL,
                platform TEXT NOT NULL,
                account_url TEXT,
                post_url TEXT NOT NULL,
                post_id TEXT,
                post_date TEXT,
                title TEXT,
                text TEXT,
                author TEXT,
                media_url TEXT,
                thumbnail_url TEXT,
                screenshot_path TEXT,
                embed_html TEXT,
                collection_method TEXT,
                discovery_rank INTEGER,
                collection_run_id TEXT,
                item_type TEXT,
                status TEXT DEFAULT 'ok',
                error_message TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(platform, post_url)
            );

            CREATE TABLE IF NOT EXISTS competition_matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                competition_code TEXT,
                match_id TEXT,
                date TEXT,
                time TEXT,
                home_team TEXT,
                away_team TEXT,
                home_score TEXT,
                away_score TEXT,
                result_text TEXT,
                venue TEXT,
                source_url TEXT,
                status TEXT DEFAULT 'ok',
                raw_json TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(competition_code, match_id)
            );
            """
        )
        existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(social_posts)").fetchall()}
        missing_columns = {
            "discovery_rank": "INTEGER",
            "collection_run_id": "TEXT",
            "item_type": "TEXT",
        }
        for column, column_type in missing_columns.items():
            if column not in existing_columns:
                conn.execute(f"ALTER TABLE social_posts ADD COLUMN {column} {column_type}")
    return Path(db_path)


def parse_iso_date(value, label="date"):
    """Parse a YYYY-MM-DD string into a date, returning None for empty values."""
    if value is None or str(value).strip() == "":
        return None
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"{label} must be YYYY-MM-DD") from exc


def _competition_where(competition_code=None):
    where = ["date IS NOT NULL", "trim(date) != ''"]
    values = []
    if competition_code:
        where.append("competition_code = ?")
        values.append(str(competition_code))
    return " AND ".join(where), values


def get_competition_date_range(conn, competition_code=None):
    """Return (min_date, max_date) from usable competition_matches.date rows."""
    where_sql, values = _competition_where(competition_code)
    try:
        row = conn.execute(
            f"""
            SELECT MIN(substr(date, 1, 10)) AS date_from, MAX(substr(date, 1, 10)) AS date_to
            FROM competition_matches
            WHERE {where_sql}
              AND length(substr(date, 1, 10)) = 10
            """,
            values,
        ).fetchone()
    except sqlite3.Error:
        return None, None
    if not row or not row["date_from"] or not row["date_to"]:
        return None, None
    try:
        return (
            parse_iso_date(row["date_from"], "competition date_from"),
            parse_iso_date(row["date_to"], "competition date_to"),
        )
    except ValueError:
        return None, None


def resolve_effective_date_range(conn, date_from, date_to, competition_code=None):
    """Resolve optional explicit dates against competition_matches min/max dates.

    Returns (date_from, date_to, source), where source is explicit, competition_matches, or mixed.
    """
    explicit_from = parse_iso_date(date_from, "--date-from")
    explicit_to = parse_iso_date(date_to, "--date-to")
    comp_from = comp_to = None
    if explicit_from is None or explicit_to is None:
        comp_from, comp_to = get_competition_date_range(conn, competition_code)

    effective_from = explicit_from or comp_from
    effective_to = explicit_to or comp_to
    if effective_from is None or effective_to is None:
        raise ValueError("Missing date range and no competition match dates found. Provide --date-from and --date-to.")
    if effective_to < effective_from:
        raise ValueError("--date-to must be greater than or equal to --date-from")
    if explicit_from is not None and explicit_to is not None:
        source = "explicit"
    elif explicit_from is None and explicit_to is None:
        source = "competition_matches"
    else:
        source = "mixed"
    return effective_from, effective_to, source

def normalize_url(url):
    """Return a stable URL for matching, or None for empty input."""
    if url is None:
        return None
    value = str(url).strip()
    if not value:
        return None

    value = " ".join(value.split())
    candidate = value
    if "://" not in candidate and candidate.startswith("www."):
        candidate = "https://" + candidate

    try:
        parts = urlsplit(candidate)
    except ValueError:
        return value

    if not parts.scheme and not parts.netloc:
        return value

    scheme = parts.scheme.lower() if parts.scheme else "https"
    netloc = parts.netloc.lower()
    if not netloc and parts.path.startswith("//"):
        try:
            parts = urlsplit(f"{scheme}:{parts.path}")
            netloc = parts.netloc.lower()
        except ValueError:
            return value

    path = parts.path or ""
    if path != "/":
        path = path.rstrip("/")
    else:
        path = ""

    query_items = []
    for key, query_value in parse_qsl(parts.query, keep_blank_values=True):
        lower_key = key.lower()
        if lower_key.startswith("utm_") or lower_key in TRACKING_QUERY_KEYS:
            continue
        query_items.append((key, query_value))
    query = urlencode(query_items, doseq=True)

    normalized = urlunsplit((scheme, netloc, path, query, ""))
    return normalized or value


def _row_exists(conn, table, where_sql, values):
    row = conn.execute(f"SELECT id FROM {table} WHERE {where_sql} LIMIT 1", values).fetchone()
    return row is not None


def upsert_society(db_path, id_societa, societa, source_detail_url=None, logo_path=None, notes=None):
    now = utc_now()
    with connect_db(db_path) as conn:
        existed = _row_exists(conn, "societies", "id_societa = ?", (id_societa,))
        conn.execute(
            """
            INSERT INTO societies (id_societa, societa, source_detail_url, logo_path, notes, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id_societa) DO UPDATE SET
                societa = excluded.societa,
                source_detail_url = excluded.source_detail_url,
                logo_path = excluded.logo_path,
                notes = excluded.notes,
                updated_at = excluded.updated_at
            """,
            (id_societa, societa, source_detail_url, logo_path, notes, now),
        )
    return "updated" if existed else "inserted"


def upsert_social_account(
    db_path,
    id_societa,
    societa,
    platform,
    account_url,
    normalized_url=None,
    status="ok",
    notes=None,
):
    now = utc_now()
    with connect_db(db_path) as conn:
        existed = _row_exists(
            conn,
            "social_accounts",
            "id_societa = ? AND platform = ? AND normalized_url = ?",
            (id_societa, platform, normalized_url),
        )
        conn.execute(
            """
            INSERT INTO social_accounts
                (id_societa, societa, platform, account_url, normalized_url, status, notes, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id_societa, platform, normalized_url) DO UPDATE SET
                societa = excluded.societa,
                account_url = excluded.account_url,
                status = excluded.status,
                notes = excluded.notes,
                updated_at = excluded.updated_at
            """,
            (id_societa, societa, platform, account_url, normalized_url, status, notes, now),
        )
    return "updated" if existed else "inserted"


def upsert_social_post(
    db_path,
    id_societa,
    societa,
    platform,
    post_url,
    account_url=None,
    post_id=None,
    post_date=None,
    title=None,
    text=None,
    author=None,
    media_url=None,
    thumbnail_url=None,
    screenshot_path=None,
    embed_html=None,
    collection_method=None,
    discovery_rank=None,
    collection_run_id=None,
    item_type=None,
    status="ok",
    error_message=None,
):
    now = utc_now()
    with connect_db(db_path) as conn:
        existed = _row_exists(conn, "social_posts", "platform = ? AND post_url = ?", (platform, post_url))
        conn.execute(
            """
            INSERT INTO social_posts
                (id_societa, societa, platform, account_url, post_url, post_id, post_date, title,
                 text, author, media_url, thumbnail_url, screenshot_path, embed_html,
                 collection_method, discovery_rank, collection_run_id, item_type, status, error_message, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(platform, post_url) DO UPDATE SET
                id_societa = excluded.id_societa,
                societa = excluded.societa,
                account_url = excluded.account_url,
                post_id = excluded.post_id,
                post_date = excluded.post_date,
                title = excluded.title,
                text = excluded.text,
                author = excluded.author,
                media_url = excluded.media_url,
                thumbnail_url = excluded.thumbnail_url,
                screenshot_path = excluded.screenshot_path,
                embed_html = excluded.embed_html,
                collection_method = excluded.collection_method,
                discovery_rank = excluded.discovery_rank,
                collection_run_id = excluded.collection_run_id,
                item_type = excluded.item_type,
                status = excluded.status,
                error_message = excluded.error_message,
                updated_at = excluded.updated_at
            """,
            (
                id_societa,
                societa,
                platform,
                account_url,
                post_url,
                post_id,
                post_date,
                title,
                text,
                author,
                media_url,
                thumbnail_url,
                screenshot_path,
                embed_html,
                collection_method,
                discovery_rank,
                collection_run_id,
                item_type,
                status,
                error_message,
                now,
            ),
        )
    return "updated" if existed else "inserted"


def upsert_competition_match(
    db_path,
    competition_code,
    match_id,
    date=None,
    time=None,
    home_team=None,
    away_team=None,
    home_score=None,
    away_score=None,
    result_text=None,
    venue=None,
    source_url=None,
    status="ok",
    raw_json=None,
):
    now = utc_now()
    if raw_json is not None and not isinstance(raw_json, str):
        raw_json = json.dumps(raw_json, ensure_ascii=False, sort_keys=True)
    with connect_db(db_path) as conn:
        existed = _row_exists(
            conn,
            "competition_matches",
            "competition_code = ? AND match_id = ?",
            (competition_code, match_id),
        )
        conn.execute(
            """
            INSERT INTO competition_matches
                (competition_code, match_id, date, time, home_team, away_team, home_score,
                 away_score, result_text, venue, source_url, status, raw_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(competition_code, match_id) DO UPDATE SET
                date = excluded.date,
                time = excluded.time,
                home_team = excluded.home_team,
                away_team = excluded.away_team,
                home_score = excluded.home_score,
                away_score = excluded.away_score,
                result_text = excluded.result_text,
                venue = excluded.venue,
                source_url = excluded.source_url,
                status = excluded.status,
                raw_json = excluded.raw_json,
                updated_at = excluded.updated_at
            """,
            (
                competition_code,
                match_id,
                date,
                time,
                home_team,
                away_team,
                home_score,
                away_score,
                result_text,
                venue,
                source_url,
                status,
                raw_json,
                now,
            ),
        )
    return "updated" if existed else "inserted"


def _query(db_path, table, filters, order_sql):
    where = []
    values = []
    for column, value in filters:
        if value is None or value == "":
            continue
        where.append(f"{column} = ?")
        values.append(value)
    sql = f"SELECT * FROM {table}"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" {order_sql}"
    with connect_db(db_path) as conn:
        return [dict(row) for row in conn.execute(sql, values).fetchall()]


def query_posts(db_path, platform=None, status=None, id_societa=None):
    return _query(
        db_path,
        "social_posts",
        (("platform", platform), ("status", status), ("id_societa", id_societa)),
        "ORDER BY COALESCE(post_date, created_at) DESC, id DESC",
    )


def query_accounts(db_path, platform=None, status=None, id_societa=None):
    return _query(
        db_path,
        "social_accounts",
        (("platform", platform), ("status", status), ("id_societa", id_societa)),
        "ORDER BY societa COLLATE NOCASE, platform, id",
    )


def query_matches(db_path, competition_code=None, status=None):
    return _query(
        db_path,
        "competition_matches",
        (("competition_code", competition_code), ("status", status)),
        "ORDER BY date DESC, time DESC, id DESC",
    )


def main():
    parser = argparse.ArgumentParser(description="Create or update the social wall SQLite database schema.")
    parser.add_argument("--db", default="data/social_wall.db", help="SQLite database path")
    args = parser.parse_args()
    path = ensure_db(args.db)
    print(f"Database ready: {path}")


if __name__ == "__main__":
    main()
