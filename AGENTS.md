# AGENTS.md

## Project purpose

This is an independent debug repository for a static one-page social wall.

The goal is to collect or manually ingest public posts/videos from Facebook, Instagram and YouTube accounts associated with sport societies, store them locally, export them to JSON, and visualize them in a single static `index.html`.

This project is intentionally independent from the main Flask/FIPAV project.

## Architecture

- `index.html` is the only frontend page.
- The page must not require a frontend framework.
- Use plain HTML, CSS and JavaScript.
- The page reads `data/posts.json`.
- Python scripts under `scripts/` generate/update the SQLite database and export JSON.
- SQLite database path: `data/social_wall.db`.
- Exported JSON path: `data/posts.json`.
- Raw input CSV path: `data/raw/societa_cuore_04052026_con_social_rev.csv`.

## Input CSV columns

Use only the curated first useful columns:

- `id_societa`
- `url`
- `denominazione`
- `SITOSOCIETÀ`
- `FB`
- `IG`
- `YT`

Ignore later candidate/best/debug columns.

Do not use:

- `facebook_best_url`
- `facebook_candidate_*`
- `instagram_best_url`
- `instagram_candidate_*`
- `youtube_best_url`
- `youtube_candidate_*`
- `website_best_url`
- `google_*`
- `LOGO`

## Database tables

Use SQLite.

Main tables:

- `social_accounts`
- `social_posts`

The frontend never queries SQLite directly. It only reads `data/posts.json`.

## Code rules

- Use Python standard library where possible.
- Use `sqlite3`, `csv`, `json`, `argparse`, `pathlib`, `datetime`.
- Avoid pandas unless strictly necessary.
- No hardcoded `/home/...` paths.
- All scripts must run from repository root.
- All scripts must expose `--help`.
- All scripts must print final summaries.
- Do not crash on one bad account or one bad URL.
- Store failures as status fields where possible.

## Platform strategy

- YouTube is the reliable collector.
- Facebook and Instagram public collection are best-effort only.
- Facebook/Instagram collectors may store candidate posts if dates are uncertain.
- Manual post ingestion must always work without API keys.

## Frontend rules

`index.html` must:

- load `data/posts.json`;
- show filters for society, platform, status, date from, date to, text search;
- show cards ordered by post date descending;
- show platform badge;
- show original post link;
- render YouTube iframe when available;
- render Facebook/Instagram embed HTML only if already stored;
- otherwise show screenshot, thumbnail or text-only fallback;
- show debug counters.

## Done criteria

A step is complete only if:

- commands in README work from repo root;
- generated files are valid;
- `python3 -m http.server 8000` serves `index.html`;
- the page can load `data/posts.json`;
- failures are handled gracefully.
