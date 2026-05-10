#!/usr/bin/env python3
"""Run selected social collectors and export frontend JSON."""

import argparse
import subprocess
import sys

from db import connect_db, ensure_db, resolve_effective_date_range

COLLECTOR_SCRIPTS = {
    "youtube": "scripts/collect_youtube.py",
    "facebook": "scripts/collect_facebook_public.py",
    "instagram": "scripts/collect_instagram_public.py",
}


def split_platforms(value):
    platforms = [p.strip().lower() for p in value.split(",") if p.strip()]
    unknown = [p for p in platforms if p not in COLLECTOR_SCRIPTS]
    if unknown:
        raise SystemExit(f"Unsupported platform(s): {', '.join(unknown)}")
    return platforms


def run_command(cmd):
    print("\n$ " + " ".join(cmd), flush=True)
    completed = subprocess.run(cmd)
    return completed.returncode


def main():
    parser = argparse.ArgumentParser(description="Run social collectors and export data/posts.json.")
    parser.add_argument("--db", default="data/social_wall.db", help="SQLite database path")
    parser.add_argument("--out", default="data/posts.json", help="Exported JSON path")
    parser.add_argument("--platforms", default="youtube,facebook,instagram", help="Comma-separated collectors to run")
    parser.add_argument("--date-from", help="Start date, inclusive, as YYYY-MM-DD; defaults to competition min date")
    parser.add_argument("--date-to", help="End date, inclusive, as YYYY-MM-DD; defaults to competition max date")
    parser.add_argument("--competition-code", help="Limit inferred date range to one competition code")
    parser.add_argument("--societa", help="Optional case-insensitive society name substring")
    parser.add_argument("--headful", action="store_true", help="Run Playwright collectors with a visible browser")
    parser.add_argument("--max-posts-per-account", type=int, default=20, help="Maximum Facebook/Instagram post links per account")
    parser.add_argument("--max-scrolls", type=int, default=5, help="Maximum Facebook/Instagram scrolls per account")
    parser.add_argument("--max-results", type=int, default=50, help="Maximum YouTube videos per account")
    parser.add_argument("--keep-out-of-range", action="store_true", help="Store date_out_of_range candidates")
    args = parser.parse_args()
    platforms = split_platforms(args.platforms)
    ensure_db(args.db)
    try:
        with connect_db(args.db) as conn:
            date_from, date_to, source = resolve_effective_date_range(
                conn, args.date_from, args.date_to, args.competition_code
            )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"Effective date range: {date_from.isoformat()} to {date_to.isoformat()}")
    print(f"Source: {source}")

    summary = {}
    for platform in platforms:
        cmd = [
            sys.executable,
            COLLECTOR_SCRIPTS[platform],
            "--db",
            args.db,
            "--date-from",
            date_from.isoformat(),
            "--date-to",
            date_to.isoformat(),
        ]
        if args.competition_code:
            cmd.extend(["--competition-code", args.competition_code])
        if args.societa:
            cmd.extend(["--societa", args.societa])
        if platform == "youtube":
            cmd.extend(["--max-results", str(args.max_results)])
        else:
            cmd.extend(["--max-posts-per-account", str(args.max_posts_per_account), "--max-scrolls", str(args.max_scrolls)])
            if args.headful:
                cmd.append("--headful")
            if args.keep_out_of_range:
                cmd.append("--keep-out-of-range")
        code = run_command(cmd)
        summary[platform] = code
        if code != 0:
            print(f"Warning: {platform} collector exited with code {code}; continuing.")

    export_cmd = [sys.executable, "scripts/export_posts_json.py", "--db", args.db, "--out", args.out]
    if args.competition_code:
        export_cmd.extend(["--competition-code", args.competition_code])
    export_code = run_command(export_cmd)
    print("\nCollect all summary")
    for platform in platforms:
        print(f"- {platform}: exit {summary[platform]}")
    print(f"- export: exit {export_code}")
    return 0 if export_code == 0 else export_code


if __name__ == "__main__":
    raise SystemExit(main())
