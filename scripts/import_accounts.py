#!/usr/bin/env python3
"""Import society social account URLs from the curated CSV into SQLite."""

import argparse
import csv
from pathlib import Path
from urllib.parse import urlsplit

from db import ensure_db, normalize_url, upsert_social_account, upsert_society

DEFAULT_CSV = "data/raw/societa/societa_cuore_04052026_con_social_rev_debug.csv"
DEFAULT_DB = "data/social_wall.db"
PLATFORMS = (
    ("website", "SITOSOCIETÀ"),
    ("facebook", "FB"),
    ("instagram", "IG"),
    ("youtube", "YT"),
)
REQUIRED_COLUMNS = ("id_societa", "url", "denominazione", "SITOSOCIETÀ", "FB", "IG", "YT")


def clean_cell(value):
    if value is None:
        return ""
    return str(value).strip()


def validate_url(normalized_url):
    if not normalized_url:
        return False, "empty URL"
    try:
        parts = urlsplit(normalized_url)
    except ValueError as exc:
        return False, f"malformed URL: {exc}"
    if parts.scheme not in {"http", "https"}:
        return False, "URL must start with http:// or https://"
    if not parts.netloc:
        return False, "URL is missing a host"
    if any(char.isspace() for char in normalized_url):
        return False, "URL contains whitespace"
    return True, None


def import_accounts(csv_path, db_path):
    ensure_db(db_path)

    rows_read = 0
    society_counts = {"inserted": 0, "updated": 0}
    account_counts = {platform: {"inserted": 0, "updated": 0} for platform, _column in PLATFORMS}
    empty_counts = {platform: 0 for platform, _column in PLATFORMS}
    invalid_counts = {platform: 0 for platform, _column in PLATFORMS}

    with Path(csv_path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = [column for column in REQUIRED_COLUMNS if column not in (reader.fieldnames or [])]
        if missing:
            raise SystemExit(f"Missing required CSV columns: {', '.join(missing)}")

        for row in reader:
            rows_read += 1
            id_societa = clean_cell(row.get("id_societa"))
            societa = clean_cell(row.get("denominazione")) or f"Societa {id_societa or rows_read}"
            source_detail_url = clean_cell(row.get("url")) or None

            society_result = upsert_society(
                db_path,
                id_societa=id_societa,
                societa=societa,
                source_detail_url=source_detail_url,
            )
            society_counts[society_result] += 1

            for platform, column in PLATFORMS:
                account_url = clean_cell(row.get(column))
                if not account_url:
                    empty_counts[platform] += 1
                    continue

                normalized = normalize_url(account_url)
                is_valid, note = validate_url(normalized)
                status = "ok" if is_valid else "invalid"
                if not is_valid:
                    invalid_counts[platform] += 1

                account_result = upsert_social_account(
                    db_path,
                    id_societa=id_societa,
                    societa=societa,
                    platform=platform,
                    account_url=account_url,
                    normalized_url=normalized,
                    status=status,
                    notes=note,
                )
                account_counts[platform][account_result] += 1

    return {
        "rows_read": rows_read,
        "society_counts": society_counts,
        "account_counts": account_counts,
        "empty_counts": empty_counts,
        "invalid_counts": invalid_counts,
        "db_path": str(db_path),
    }


def print_summary(summary):
    print("Import complete")
    print(f"Rows read: {summary['rows_read']}")
    print(
        "Societies inserted/updated: "
        f"{summary['society_counts']['inserted']}/{summary['society_counts']['updated']}"
    )
    print("Accounts inserted/updated by platform:")
    for platform in summary["account_counts"]:
        counts = summary["account_counts"][platform]
        print(f"  {platform}: {counts['inserted']}/{counts['updated']}")
    print("Empty counts by platform:")
    for platform, count in summary["empty_counts"].items():
        print(f"  {platform}: {count}")
    print("Invalid counts by platform:")
    for platform, count in summary["invalid_counts"].items():
        print(f"  {platform}: {count}")
    print(f"Database path: {summary['db_path']}")


def main():
    parser = argparse.ArgumentParser(description="Import curated society account URLs into SQLite.")
    parser.add_argument("--csv", default=DEFAULT_CSV, help="Input society CSV path")
    parser.add_argument("--db", default=DEFAULT_DB, help="SQLite database path")
    args = parser.parse_args()

    summary = import_accounts(args.csv, args.db)
    print_summary(summary)


if __name__ == "__main__":
    main()
