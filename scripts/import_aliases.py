#!/usr/bin/env python3
"""Create a lightweight society_aliases table from imported societies."""

import argparse

from db import connect_db, ensure_db, utc_now


def main():
    parser = argparse.ArgumentParser(description="Populate society_aliases with basic aliases from societies.")
    parser.add_argument("--db", default="data/social_wall.db", help="SQLite database path")
    args = parser.parse_args()
    ensure_db(args.db)
    now = utc_now()
    with connect_db(args.db) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS society_aliases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                id_societa TEXT,
                societa TEXT NOT NULL,
                alias TEXT NOT NULL,
                source TEXT DEFAULT 'societies',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(id_societa, alias)
            )
            """
        )
        rows = conn.execute("SELECT id_societa, societa FROM societies ORDER BY societa COLLATE NOCASE").fetchall()
        inserted = updated = 0
        for row in rows:
            existed = conn.execute(
                "SELECT 1 FROM society_aliases WHERE id_societa IS ? AND alias = ?",
                (row["id_societa"], row["societa"]),
            ).fetchone()
            conn.execute(
                """
                INSERT INTO society_aliases (id_societa, societa, alias, source, updated_at)
                VALUES (?, ?, ?, 'societies', ?)
                ON CONFLICT(id_societa, alias) DO UPDATE SET
                    societa = excluded.societa,
                    updated_at = excluded.updated_at
                """,
                (row["id_societa"], row["societa"], row["societa"], now),
            )
            if existed:
                updated += 1
            else:
                inserted += 1
    print("Alias import complete")
    print(f"Societies scanned: {len(rows)}")
    print(f"Aliases inserted/updated: {inserted}/{updated}")
    print(f"Database path: {args.db}")


if __name__ == "__main__":
    main()
