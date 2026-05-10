#!/usr/bin/env python3
"""Import competition match rows from a FIPAV-style CSV into SQLite."""

import argparse
import csv
import hashlib
import re
import unicodedata
from datetime import datetime
from pathlib import Path

from db import ensure_db, upsert_competition_match

DEFAULT_CSV = "data/raw/campionati/59243_debug.csv"
DEFAULT_DB = "data/social_wall.db"

COLUMN_ALIASES = {
    "match_id": (
        "match_id",
        "id_gara",
        "gara_id",
        "codice_gara",
        "codicegara",
        "numero_gara",
        "numerogara",
        "num_gara",
        "n_gara",
        "gara",
        "id",
    ),
    "date": (
        "date",
        "data",
        "data_gara",
        "datagara",
        "giorno",
        "data_incontro",
        "dataincontro",
    ),
    "date_text": (
        "data_testo",
        "datatesto",
        "data_ora_testo",
        "dataora_testo",
        "quando",
    ),
    "time": (
        "time",
        "ora",
        "orario",
        "ora_gara",
        "oragara",
        "hour",
    ),
    "home_team": (
        "home_team",
        "squadra_casa",
        "squadracasa",
        "casa",
        "squadra_1",
        "squadra1",
        "team_casa",
        "teamcasa",
        "formazione_casa",
        "societa_casa",
    ),
    "away_team": (
        "away_team",
        "squadra_ospite",
        "squadraospite",
        "ospite",
        "squadra_2",
        "squadra2",
        "team_ospite",
        "teamospite",
        "formazione_ospite",
        "societa_ospite",
    ),
    "home_score": (
        "home_score",
        "set_casa",
        "setcasa",
        "punti_casa",
        "punticasa",
        "score_casa",
        "scorecasa",
        "risultato_casa",
    ),
    "away_score": (
        "away_score",
        "set_ospite",
        "setospite",
        "punti_ospite",
        "puntiospite",
        "score_ospite",
        "scoreospite",
        "risultato_ospite",
    ),
    "result_text": (
        "result",
        "risultato",
        "risultato_gara",
        "risultatogara",
        "esito",
        "score",
        "sets",
    ),
    "venue": (
        "venue",
        "palestra",
        "campo",
        "impianto",
        "luogo",
        "sede",
        "indirizzo_campo",
        "indirizzocampo",
    ),
    "source_url": (
        "source_url",
        "sourceurl",
        "url",
        "link",
        "href",
        "pagina",
        "pagina_url",
        "fipav_url",
    ),
}

CONTAINS_ALIASES = {
    "match_id": (("codice", "gara"), ("numero", "gara")),
    "date": (("data", "gara"),),
    "time": (("ora", "gara"),),
    "home_team": (("squadra", "casa"), ("team", "casa")),
    "away_team": (("squadra", "ospite"), ("team", "ospite")),
    "home_score": (("set", "casa"), ("punti", "casa"), ("score", "casa")),
    "away_score": (("set", "ospite"), ("punti", "ospite"), ("score", "ospite")),
    "result_text": (("risultato",),),
    "venue": (("palestra",), ("campo",), ("impianto",)),
    "source_url": (("url",),),
}

DATE_FORMATS = (
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%Y/%m/%d",
    "%d/%m/%y",
    "%d-%m-%y",
)

TIME_FORMATS = (
    "%H:%M:%S",
    "%H:%M",
    "%I:%M:%S %p",
    "%I:%M %p",
    "%H.%M",
)


def clean_cell(value):
    if value is None:
        return ""
    return " ".join(str(value).strip().split())


def normalize_name(value):
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return value.strip("_")


def infer_competition_code(csv_path):
    return Path(csv_path).stem


def build_header_map(fieldnames):
    normalized_to_header = {}
    for header in fieldnames or []:
        normalized = normalize_name(header)
        if normalized and normalized not in normalized_to_header:
            normalized_to_header[normalized] = header

    mapped = {}
    used_headers = set()
    for concept, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            header = normalized_to_header.get(normalize_name(alias))
            if header is not None and header not in used_headers:
                mapped[concept] = header
                used_headers.add(header)
                break

    for concept, token_sets in CONTAINS_ALIASES.items():
        if concept in mapped:
            continue
        for normalized, header in normalized_to_header.items():
            if header in used_headers:
                continue
            if any(all(token in normalized for token in tokens) for tokens in token_sets):
                mapped[concept] = header
                used_headers.add(header)
                break
    return mapped


def value_for(row, header_map, concept):
    header = header_map.get(concept)
    if not header:
        return ""
    return clean_cell(row.get(header))


def normalize_date(value):
    value = clean_cell(value)
    if not value:
        return ""
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            pass

    match = re.search(r"(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})", value)
    if match:
        day, month, year = match.groups()
        if len(year) == 2:
            year = "20" + year
        try:
            return datetime(int(year), int(month), int(day)).date().isoformat()
        except ValueError:
            return value
    return value


def normalize_time(value):
    value = clean_cell(value).replace(".", ":")
    if not value:
        return ""
    for fmt in TIME_FORMATS:
        try:
            return datetime.strptime(value, fmt).strftime("%H:%M")
        except ValueError:
            pass
    match = re.search(r"\b(\d{1,2})[:.](\d{2})\b", value)
    if match:
        hour, minute = match.groups()
        try:
            return f"{int(hour):02d}:{int(minute):02d}"
        except ValueError:
            return value
    return value


def synthetic_match_id(date_value, home_team, away_team, row_number):
    base = f"{date_value}|{home_team}|{away_team}|{row_number}"
    digest = hashlib.sha1(base.encode("utf-8")).hexdigest()[:12]
    return f"synthetic-{digest}"


def derive_scores(home_score, away_score, result_text):
    if home_score and away_score:
        return home_score, away_score
    match = re.search(r"(\d+)\s*[-:]\s*(\d+)", result_text or "")
    if not match:
        return home_score, away_score
    return home_score or match.group(1), away_score or match.group(2)


def row_status(missing_date, missing_teams, had_error=False):
    if had_error:
        return "import_error"
    problems = []
    if missing_date:
        problems.append("missing_date")
    if missing_teams:
        problems.append("missing_teams")
    return ";".join(problems) if problems else "ok"


def import_competition(csv_path, db_path, competition_code=None):
    csv_path = Path(csv_path)
    db_path = Path(db_path)
    competition_code = competition_code or infer_competition_code(csv_path)
    ensure_db(db_path)

    rows_read = 0
    inserted = 0
    updated = 0
    missing_date_count = 0
    missing_teams_count = 0
    malformed_rows = 0

    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        header_map = build_header_map(reader.fieldnames or [])

        for row_number, row in enumerate(reader, start=1):
            rows_read += 1
            try:
                date_raw = value_for(row, header_map, "date") or value_for(row, header_map, "date_text")
                date_value = normalize_date(date_raw)
                time_value = normalize_time(value_for(row, header_map, "time"))
                home_team = value_for(row, header_map, "home_team")
                away_team = value_for(row, header_map, "away_team")
                home_score = value_for(row, header_map, "home_score")
                away_score = value_for(row, header_map, "away_score")
                result_text = value_for(row, header_map, "result_text")
                home_score, away_score = derive_scores(home_score, away_score, result_text)
                venue = value_for(row, header_map, "venue")
                source_url = value_for(row, header_map, "source_url")
                match_id = value_for(row, header_map, "match_id")
                if not match_id:
                    match_id = synthetic_match_id(date_value, home_team, away_team, row_number)

                missing_date = not bool(date_raw)
                missing_teams = not (home_team and away_team)
                if missing_date:
                    missing_date_count += 1
                if missing_teams:
                    missing_teams_count += 1

                result = upsert_competition_match(
                    db_path,
                    competition_code=competition_code,
                    match_id=match_id,
                    date=date_value or None,
                    time=time_value or None,
                    home_team=home_team or None,
                    away_team=away_team or None,
                    home_score=home_score or None,
                    away_score=away_score or None,
                    result_text=result_text or None,
                    venue=venue or None,
                    source_url=source_url or None,
                    status=row_status(missing_date, missing_teams),
                    raw_json=row,
                )
            except Exception as exc:  # Keep importing after a malformed row.
                malformed_rows += 1
                match_id = synthetic_match_id("", "", "", row_number)
                result = upsert_competition_match(
                    db_path,
                    competition_code=competition_code,
                    match_id=match_id,
                    status=row_status(False, False, had_error=True),
                    raw_json={"row": row, "error": str(exc)},
                )

            if result == "inserted":
                inserted += 1
            else:
                updated += 1

    return {
        "rows_read": rows_read,
        "inserted": inserted,
        "updated": updated,
        "missing_date_count": missing_date_count,
        "missing_teams_count": missing_teams_count,
        "malformed_rows": malformed_rows,
        "db_path": str(db_path),
        "competition_code": competition_code,
    }


def print_summary(summary):
    print("Competition import complete")
    print(f"Rows read: {summary['rows_read']}")
    print(f"Competition code: {summary['competition_code']}")
    print(f"Matches inserted/updated: {summary['inserted']}/{summary['updated']}")
    print(f"Rows with missing date: {summary['missing_date_count']}")
    print(f"Rows with missing teams: {summary['missing_teams_count']}")
    print(f"Malformed rows stored with import_error: {summary['malformed_rows']}")
    print(f"Database path: {summary['db_path']}")


def main():
    parser = argparse.ArgumentParser(description="Import competition match data into SQLite.")
    parser.add_argument("--csv", default=DEFAULT_CSV, help="Input competition CSV path")
    parser.add_argument("--db", default=DEFAULT_DB, help="SQLite database path")
    parser.add_argument(
        "--competition-code",
        help="Competition code to store; defaults to the input CSV filename stem",
    )
    args = parser.parse_args()

    summary = import_competition(args.csv, args.db, args.competition_code)
    print_summary(summary)


if __name__ == "__main__":
    main()
