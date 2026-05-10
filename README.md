# Social Wall Debug

Independent debug repository for a static one-page social wall. The project imports sport society social account URLs and competition matches into `data/social_wall.db`, collects or manually ingests lightweight public posts/videos, exports `data/posts.json`, and serves a plain `index.html` dashboard.

## Full workflow

Run all commands from the repository root. The collector date range is inferred from `competition_matches` when `--date-from` / `--date-to` are omitted.

```bash
python3 scripts/import_accounts.py \
  --csv data/raw/societa/societa_cuore_04052026_con_social_rev_debug.csv \
  --db data/social_wall.db

python3 scripts/import_aliases.py \
  --db data/social_wall.db

python3 scripts/import_competition.py \
  --csv data/raw/campionati/59243_debug.csv \
  --db data/social_wall.db \
  --competition-code 59243

python3 scripts/link_matches_to_societies.py \
  --db data/social_wall.db

python3 scripts/collect_all.py \
  --db data/social_wall.db \
  --out data/posts.json \
  --platforms youtube,facebook,instagram \
  --max-posts-per-account 20 \
  --max-scrolls 5 \
  --headful

python3 -m http.server 8000
```

Then open:

```text
http://localhost:8000/index.html
```

Do not open `index.html` with `file://`; browsers may block the `fetch()` request for `data/posts.json`.

## Date range defaults

Collectors and `collect_all.py` accept optional `--date-from YYYY-MM-DD`, `--date-to YYYY-MM-DD`, and `--competition-code CODE`.

When one or both date boundaries are omitted:

- missing `date_from` defaults to `MIN(date)` from usable `competition_matches.date` rows;
- missing `date_to` defaults to `MAX(date)` from usable `competition_matches.date` rows;
- `--competition-code` limits the inferred range to that competition.

If there are no usable competition dates and no explicit full range, collectors stop with:

```text
Missing date range and no competition match dates found. Provide --date-from and --date-to.
```

Explicit override example:

```bash
python3 scripts/collect_all.py \
  --db data/social_wall.db \
  --out data/posts.json \
  --date-from 2026-04-01 \
  --date-to 2026-05-31 \
  --platforms facebook,instagram
```

## Database tables

The core tables are:

- `societies`
- `social_accounts`
- `social_posts`
- `competition_matches`

`social_posts` stores lightweight useful fields only: society identifiers, platform/account/post URLs, `post_id`, `post_date`, title/text/author, YouTube thumbnails, simple embeds, collection method, status, and error message. Collectors do not save screenshots, full HTML dumps, page snapshots, or downloaded media files.

## Import society accounts

```bash
python3 scripts/import_accounts.py \
  --csv data/raw/societa/societa_cuore_04052026_con_social_rev_debug.csv \
  --db data/social_wall.db
```

The importer reads only the curated useful CSV columns (`id_societa`, `url`, `denominazione`, `SITOSOCIETÀ`, `FB`, `IG`, `YT`), stores malformed URLs with `status='invalid'`, and prints a final summary.

Inspect imported account counts:

```bash
sqlite3 data/social_wall.db "select platform, count(*) from social_accounts group by platform;"
```

## Import competition matches

```bash
python3 scripts/import_competition.py \
  --csv data/raw/campionati/59243_debug.csv \
  --db data/social_wall.db \
  --competition-code 59243
```

Inspect the available competition date range:

```bash
sqlite3 data/social_wall.db "select min(date), max(date), count(*) from competition_matches where nullif(trim(date),'') is not null;"
```

## Collectors

### Run all collectors

```bash
python3 scripts/collect_all.py \
  --db data/social_wall.db \
  --out data/posts.json \
  --platforms youtube,facebook,instagram \
  --max-posts-per-account 20 \
  --max-scrolls 5 \
  --headful
```

`collect_all.py` resolves the effective date range once, passes it to each selected collector, continues if one collector fails, and always runs the JSON exporter at the end.

### YouTube

The YouTube collector uses the YouTube Data API and `YOUTUBE_API_KEY` from the environment.

```bash
export YOUTUBE_API_KEY=...
python3 scripts/collect_youtube.py \
  --db data/social_wall.db \
  --max-results 50
```

If the key is missing, the collector reports `api_key_missing` and exits 0 unless `--require-api-key` is supplied.

### Facebook public

The Facebook collector is a minimal best-effort Playwright collector. It scans visible public links only, detects login/block pages, and stores real post candidates (`/posts/`, `/permalink.php`, `/photos/`, `/videos/`, `/reel/`, `/share/p/`, `/watch/`) without screenshots or HTML dumps.

```bash
python3 scripts/collect_facebook_public.py \
  --db data/social_wall.db \
  --headful \
  --max-scrolls 5 \
  --max-posts-per-account 20
```

### Instagram public

The Instagram collector is a minimal best-effort Playwright collector. It scans visible public `/p/`, `/reel/`, and `/tv/` links and stores real post candidates only.

```bash
python3 scripts/collect_instagram_public.py \
  --db data/social_wall.db \
  --headful \
  --max-scrolls 5 \
  --max-posts-per-account 20
```

Shared collector options:

- `--date-from YYYY-MM-DD`
- `--date-to YYYY-MM-DD`
- `--competition-code CODE`
- `--societa TEXT`
- `--keep-out-of-range` for Facebook/Instagram only

Default inserted statuses are `ok`, `candidate`, and `date_uncertain`. `date_out_of_range` rows are skipped unless `--keep-out-of-range` is passed.

## Manual post ingestion

Manual ingestion is the always-available fallback and does not require API keys.

```bash
python3 scripts/ingest_manual_posts.py \
  --csv data/raw/manual_posts.csv \
  --db data/social_wall.db
```

Minimal CSV example:

```csv
societa,platform,post_url,post_date,title,text
Migliarino Volley,facebook,https://www.facebook.com/...,2026-04-10,Match day,Post text
```

## Export frontend JSON

```bash
python3 scripts/export_posts_json.py \
  --db data/social_wall.db \
  --out data/posts.json
```

The exporter writes `generated_at`, `summary`, `societies`, `accounts`, `posts`, and `matches`. The summary includes account counts by platform, post counts by platform/status/society, and the available competition date range.

Optional filters:

```bash
python3 scripts/export_posts_json.py --platform youtube --status ok
python3 scripts/export_posts_json.py --date-from 2026-01-01 --date-to 2026-12-31
python3 scripts/export_posts_json.py --societa "Volley" --competition-code 59243
```

## Static frontend

Serve the repository root:

```bash
python3 -m http.server 8000
```

The page loads `data/posts.json`, provides society/platform/status/date/search filters, renders YouTube iframes and simple stored embeds, shows original post links, displays account links and collected post counts in society cards, and shows debug counters. It does not require screenshots and will not show broken screenshot placeholders.

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
python3 scripts/collect_facebook_public.py --help
python3 scripts/collect_instagram_public.py --help
python3 scripts/collect_all.py --help
```
