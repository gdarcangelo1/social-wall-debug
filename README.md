# Social Wall Debug

Independent debug repository for a static one-page social wall. The data layer stores sport society social accounts and, later, collected public posts/videos from Facebook, Instagram and YouTube in a local SQLite database. The frontend is intentionally not implemented in this first step.

## Data layer purpose

The first working data layer imports curated society account URLs from CSV into `data/social_wall.db`.

It creates these SQLite tables:

- `societies`
- `social_accounts`
- `social_posts`
- `competition_matches`

`social_posts` and `competition_matches` are created now so later collectors/importers can use the same database schema.

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

## Utility commands

Create or update the database schema only:

```bash
python3 scripts/db.py --db data/social_wall.db
```

Show importer help:

```bash
python3 scripts/import_accounts.py --help
```
