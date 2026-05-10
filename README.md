# Social Wall Debug

Independent debug repository for a static one-page social wall. The data layer stores sport society social accounts, collected or manually ingested public posts/videos from Facebook, Instagram and YouTube, and competition matches in a local SQLite database. The static frontend will read one exported JSON file from `data/posts.json`.

## Data layer purpose

The working data layer imports curated society account URLs and competition matches into `data/social_wall.db`, then exports a frontend-ready JSON payload.

It creates and uses these SQLite tables:

- `societies`
- `social_accounts`
- `social_posts`
- `competition_matches`

The frontend never queries SQLite directly. It reads only:

```text
data/posts.json
```

## Expected society CSV columns

The account importer reads only these curated columns:

- `id_societa`
- `url`
- `denominazione`
- `SITOSOCIETÀ`
- `FB`
- `IG`
- `YT`

All later candidate, best, debug, Google and logo columns are ignored.

Default input CSV:

```text
data/raw/societa/societa_cuore_04052026_con_social_rev_debug.csv
```

Default SQLite database:

```text
data/social_wall.db
```

Default exported JSON:

```text
data/posts.json
```

## Full static frontend data workflow

Run from the repository root:

```bash
python3 scripts/import_accounts.py
python3 scripts/import_competition.py --competition-code 59243
python3 scripts/export_posts_json.py
python3 -m http.server 8000
```

Then open the static site at:

```text
http://localhost:8000/index.html
```

Do not open `index.html` with `file://`; browsers may block the `fetch()` request for `data/posts.json`.

## Import society accounts

Run from the repository root:

```bash
python3 scripts/import_accounts.py \
  --csv data/raw/societa/societa_cuore_04052026_con_social_rev_debug.csv \
  --db data/social_wall.db
```

The importer:

- creates parent directories if needed;
- creates or updates the database schema;
- reads the CSV with `utf-8-sig` using `csv.DictReader`;
- inserts/updates one row in `societies` for each CSV row;
- imports non-empty `SITOSOCIETÀ`, `FB`, `IG` and `YT` cells into `social_accounts`;
- preserves the original account URL;
- stores a normalized URL for duplicate detection;
- stores malformed URLs with `status='invalid'` instead of crashing;
- prints a final summary.

## Inspect imported account counts

Run from the repository root after importing:

```bash
sqlite3 data/social_wall.db "select platform, count(*) from social_accounts group by platform;"
```

## Import competition matches

Run from the repository root:

```bash
python3 scripts/import_competition.py \
  --csv data/raw/campionati/59243_debug.csv \
  --db data/social_wall.db \
  --competition-code 59243
```

The competition importer:

- creates or updates the database schema;
- reads the CSV with `utf-8-sig` using `csv.DictReader`;
- maps FIPAV-style headers with case-insensitive normalized names;
- stores each original CSV row in `competition_matches.raw_json`;
- generates a stable synthetic match id from date, teams and row number when no match id column is available;
- normalizes dates to `YYYY-MM-DD` where possible while preserving uncertain originals in `raw_json`;
- stores malformed rows with status information instead of stopping the import;
- prints a final summary.

## Inspect imported competition counts

Run from the repository root after importing:

```bash
sqlite3 data/social_wall.db "select competition_code, count(*) from competition_matches group by competition_code;"
```


## Manual post ingestion

Manual ingestion is the always-available fallback for public Facebook, Instagram and YouTube posts. It does not require API keys.

Create `data/raw/manual_posts.csv` with the supported columns. Minimal example:

```csv
societa,platform,post_url,post_date,title,text
Migliarino Volley,facebook,https://www.facebook.com/...,2026-04-10,Match day,Post text
```

Supported columns are:

- `societa`
- `id_societa` optional
- `platform` optional; inferred from `post_url` when missing
- `account_url` optional
- `post_url`
- `post_date` optional
- `title` optional
- `text` optional
- `thumbnail_url` optional
- `screenshot_path` optional

Run from the repository root:

```bash
python3 scripts/ingest_manual_posts.py \
  --csv data/raw/manual_posts.csv \
  --db data/social_wall.db
```

The ingester upserts by `platform + post_url`, normalizes dates where possible, stores `date_missing` or `date_invalid` statuses when needed, creates YouTube video ids and iframe HTML for common YouTube URL formats, creates simple Facebook embed iframe HTML, and leaves Instagram posts as normal link fallbacks.

## Collect YouTube videos

The YouTube collector reads `social_accounts` rows where `platform='youtube'` and uses the YouTube Data API when `YOUTUBE_API_KEY` is available in the environment. Secrets must only be passed through the environment.

Set the API key and collect videos for a date range:

```bash
export YOUTUBE_API_KEY=...

python3 scripts/collect_youtube.py \
  --db data/social_wall.db \
  --date-from 2026-04-01 \
  --date-to 2026-04-30
```

Optional flags:

- `--societa TEXT` filters accounts by case-insensitive society substring.
- `--max-results 50` limits videos per account.
- `--require-api-key` exits non-zero if `YOUTUBE_API_KEY` is missing. Without this flag, a missing key is reported clearly and the script exits normally.

After either manual ingestion or YouTube collection, refresh the frontend JSON:

```bash
python3 scripts/export_posts_json.py \
  --db data/social_wall.db \
  --out data/posts.json
```

## Export frontend JSON

Run from the repository root:

```bash
python3 scripts/export_posts_json.py \
  --db data/social_wall.db \
  --out data/posts.json
```

The exporter:

- creates or updates the database schema if the database is empty or missing;
- reads `societies`, `social_accounts`, `social_posts` and `competition_matches`;
- writes one JSON object with `generated_at`, `summary`, `societies`, `accounts`, `posts` and `matches`;
- creates the parent output directory if needed;
- orders posts by `post_date` descending with unknown dates last;
- orders matches by `date` ascending with unknown dates last;
- supports optional filters for dates, society substring, platform, status and competition code;
- prints a final summary.

Optional filter examples:

```bash
python3 scripts/export_posts_json.py --platform youtube --status ok
python3 scripts/export_posts_json.py --date-from 2026-01-01 --date-to 2026-12-31
python3 scripts/export_posts_json.py --societa "Volley" --competition-code 59243
```

The exported structure is:

```json
{
  "generated_at": "...",
  "summary": {
    "societies": 0,
    "accounts": 0,
    "posts": 0,
    "matches": 0,
    "accounts_by_platform": {},
    "posts_by_platform": {},
    "posts_by_status": {}
  },
  "societies": [],
  "accounts": [],
  "posts": [],
  "matches": []
}
```

## Utility commands

Create or update the database schema only:

```bash
python3 scripts/db.py --db data/social_wall.db
```

Show script help:

```bash
python3 scripts/import_accounts.py --help
python3 scripts/import_competition.py --help
python3 scripts/export_posts_json.py --help
python3 scripts/ingest_manual_posts.py --help
python3 scripts/collect_youtube.py --help
```
