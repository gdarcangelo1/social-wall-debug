#!/usr/bin/env python3
"""Best-effort summary of competition matches that mention imported society names."""

import argparse

from db import connect_db, ensure_db


def normalize(value):
    return " ".join(str(value or "").casefold().split())


def main():
    parser = argparse.ArgumentParser(description="Report best-effort links between competition matches and imported societies.")
    parser.add_argument("--db", default="data/social_wall.db", help="SQLite database path")
    args = parser.parse_args()
    ensure_db(args.db)
    with connect_db(args.db) as conn:
        societies = conn.execute("SELECT id_societa, societa FROM societies").fetchall()
        matches = conn.execute("SELECT id, home_team, away_team FROM competition_matches").fetchall()
        linked = 0
        for match in matches:
            teams = normalize(f"{match['home_team'] or ''} {match['away_team'] or ''}")
            if any(normalize(s["societa"]) and normalize(s["societa"]) in teams for s in societies):
                linked += 1
    print("Match linking summary")
    print(f"Societies scanned: {len(societies)}")
    print(f"Matches scanned: {len(matches)}")
    print(f"Matches with direct society-name mentions: {linked}")
    print("No schema changes were required; collectors use social_accounts and competition_matches directly.")


if __name__ == "__main__":
    main()
