#!/usr/bin/env python3
"""Import public men's international results into fixtures and team_results."""

from __future__ import annotations

import argparse
import csv
import io
import json
import unicodedata
from pathlib import Path
from urllib.request import urlopen

from common import connect, ensure_parent, now_utc, slugify


DEFAULT_RESULTS_URL = "https://raw.githubusercontent.com/martj42/international_results/master/results.csv"
SOURCE_ID = "martj42_international_results"


ALIASES = {
    "bosnia herzegovina": "BIH",
    "bosnia and herzegovina": "BIH",
    "cabo verde": "CPV",
    "cape verde": "CPV",
    "congo dr": "COD",
    "dr congo": "COD",
    "democratic republic of congo": "COD",
    "cote d ivoire": "CIV",
    "cote divoire": "CIV",
    "ivory coast": "CIV",
    "curacao": "CUW",
    "czech republic": "CZE",
    "czechia": "CZE",
    "iran": "IRN",
    "ir iran": "IRN",
    "korea republic": "KOR",
    "south korea": "KOR",
    "saudi arabia": "KSA",
    "south africa": "RSA",
    "turkey": "TUR",
    "turkiye": "TUR",
    "united states": "USA",
    "usa": "USA",
}


def normalize_name(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(char for char in text if not unicodedata.combining(char))
    keep = [char.lower() if char.isalnum() else " " for char in text]
    return " ".join("".join(keep).split())


def truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"true", "1", "yes", "y"}


def parse_score(value: str | None) -> int | None:
    try:
        return int(str(value or "").strip())
    except ValueError:
        return None


def team_name_map(conn) -> dict[str, str]:
    mapping = {}
    for row in conn.execute("SELECT team_id, fifa_code, name, short_name FROM teams"):
        for key in (row["team_id"], row["fifa_code"], row["name"], row["short_name"]):
            normalized = normalize_name(key)
            if normalized:
                mapping[normalized] = row["team_id"]
    for alias, team_id in ALIASES.items():
        if conn.execute("SELECT 1 FROM teams WHERE team_id = ?", (team_id,)).fetchone():
            mapping[normalize_name(alias)] = team_id
    return mapping


def read_csv_text(csv_path: Path | None, url: str, raw_out: Path | None) -> str:
    if csv_path:
        text = csv_path.read_text(encoding="utf-8")
    else:
        with urlopen(url, timeout=30) as response:
            text = response.read().decode("utf-8")
    if raw_out:
        ensure_parent(raw_out).write_text(text, encoding="utf-8")
    return text


def upsert_source(conn, url: str) -> None:
    conn.execute(
        """
        INSERT INTO sources (
            source_id, name, url, category, publisher, retrieved_at,
            freshness_days, license_note, reliability, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_id) DO UPDATE SET
            url = excluded.url,
            retrieved_at = excluded.retrieved_at,
            notes = excluded.notes
        """,
        (
            SOURCE_ID,
            "International men's football results",
            url,
            "historical_results",
            "martj42/international_results",
            now_utc(),
            365,
            "CC0-1.0 dataset; verify before production use.",
            "public-dataset",
            "Imported by import_historical_results.py for model calibration/backtesting.",
        ),
    )


def row_in_window(row: dict, since: str | None, until: str | None) -> bool:
    date = row.get("date") or ""
    if since and date < since:
        return False
    if until and date > until:
        return False
    return True


def fixture_id(row: dict, home_id: str, away_id: str) -> str:
    parts = [
        "hist",
        row.get("date"),
        home_id,
        away_id,
        row.get("home_score"),
        row.get("away_score"),
        row.get("tournament"),
        row.get("city"),
    ]
    return slugify("-".join(str(part or "") for part in parts))


def result_id(row: dict, team_id: str, opponent_id: str | None, venue_type: str) -> str:
    parts = [
        "hist-result",
        row.get("date"),
        team_id,
        opponent_id or "unknown",
        venue_type,
        row.get("home_score"),
        row.get("away_score"),
        row.get("tournament"),
        row.get("city"),
    ]
    return slugify("-".join(str(part or "") for part in parts))


def insert_fixture(conn, row: dict, home_id: str, away_id: str, stage: str) -> None:
    neutral = truthy(row.get("neutral"))
    home_score = parse_score(row.get("home_score"))
    away_score = parse_score(row.get("away_score"))
    if home_score is None or away_score is None:
        return
    conn.execute(
        """
        INSERT INTO fixtures (
            match_id, competition, stage, match_date, venue, city, country,
            team_a_id, team_b_id, score_a, score_b, status, source_id, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'final', ?, ?)
        ON CONFLICT(match_id) DO UPDATE SET
            competition = excluded.competition,
            stage = excluded.stage,
            match_date = excluded.match_date,
            venue = excluded.venue,
            city = excluded.city,
            country = excluded.country,
            team_a_id = excluded.team_a_id,
            team_b_id = excluded.team_b_id,
            score_a = excluded.score_a,
            score_b = excluded.score_b,
            status = excluded.status,
            source_id = excluded.source_id,
            notes = excluded.notes
        """,
        (
            fixture_id(row, home_id, away_id),
            row.get("tournament") or "International",
            stage,
            row.get("date"),
            row.get("city"),
            row.get("city"),
            row.get("country"),
            home_id,
            away_id,
            home_score,
            away_score,
            SOURCE_ID,
            (
                "Historical full international; "
                f"home_team={row.get('home_team')}; away_team={row.get('away_team')}; "
                f"neutral={str(neutral).lower()}."
            ),
        ),
    )


def insert_team_result(
    conn,
    row: dict,
    team_id: str,
    opponent_id: str | None,
    goals_for: int,
    goals_against: int,
    venue_type: str,
) -> None:
    neutral = truthy(row.get("neutral"))
    conn.execute(
        """
        INSERT INTO team_results (
            result_id, match_date, team_id, opponent_team_id, venue_type,
            competition, is_neutral, goals_for, goals_against, source_id, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(result_id) DO UPDATE SET
            match_date = excluded.match_date,
            opponent_team_id = excluded.opponent_team_id,
            venue_type = excluded.venue_type,
            competition = excluded.competition,
            is_neutral = excluded.is_neutral,
            goals_for = excluded.goals_for,
            goals_against = excluded.goals_against,
            source_id = excluded.source_id,
            notes = excluded.notes
        """,
        (
            result_id(row, team_id, opponent_id, venue_type),
            row.get("date"),
            team_id,
            opponent_id,
            venue_type,
            row.get("tournament") or "International",
            1 if neutral else 0,
            goals_for,
            goals_against,
            SOURCE_ID,
            (
                "Historical full international from public results CSV; "
                "xG/shots unavailable in this source."
            ),
        ),
    )


def import_results(
    db_path: Path,
    csv_path: Path | None,
    url: str,
    raw_out: Path | None,
    since: str | None,
    until: str | None,
    replace_source: bool,
    stage: str,
) -> dict:
    conn = connect(db_path)
    stats = {
        "rows_read": 0,
        "rows_in_window": 0,
        "fixtures_upserted": 0,
        "team_results_upserted": 0,
        "unmapped_team_names": {},
    }
    try:
        if replace_source:
            conn.execute("DELETE FROM fixtures WHERE source_id = ?", (SOURCE_ID,))
            conn.execute("DELETE FROM team_results WHERE source_id = ?", (SOURCE_ID,))
        upsert_source(conn, url)
        mapping = team_name_map(conn)
        text = read_csv_text(csv_path, url, raw_out)
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            stats["rows_read"] += 1
            if not row_in_window(row, since, until):
                continue
            home_score = parse_score(row.get("home_score"))
            away_score = parse_score(row.get("away_score"))
            if home_score is None or away_score is None:
                continue
            stats["rows_in_window"] += 1
            home_id = mapping.get(normalize_name(row.get("home_team")))
            away_id = mapping.get(normalize_name(row.get("away_team")))
            if not home_id:
                name = row.get("home_team") or ""
                stats["unmapped_team_names"][name] = stats["unmapped_team_names"].get(name, 0) + 1
            if not away_id:
                name = row.get("away_team") or ""
                stats["unmapped_team_names"][name] = stats["unmapped_team_names"].get(name, 0) + 1
            if home_id and away_id:
                insert_fixture(conn, row, home_id, away_id, stage)
                stats["fixtures_upserted"] += 1
            if home_id:
                insert_team_result(
                    conn,
                    row,
                    home_id,
                    away_id,
                    home_score,
                    away_score,
                    "neutral" if truthy(row.get("neutral")) else "home",
                )
                stats["team_results_upserted"] += 1
            if away_id:
                insert_team_result(
                    conn,
                    row,
                    away_id,
                    home_id,
                    away_score,
                    home_score,
                    "neutral" if truthy(row.get("neutral")) else "away",
                )
                stats["team_results_upserted"] += 1
        conn.commit()
    finally:
        conn.close()

    stats["unmapped_team_names"] = dict(
        sorted(stats["unmapped_team_names"].items(), key=lambda item: item[1], reverse=True)[:20]
    )
    return stats


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, help="SQLite database path.")
    parser.add_argument("--csv", help="Optional local results.csv path.")
    parser.add_argument("--url", default=DEFAULT_RESULTS_URL, help="Remote results.csv URL.")
    parser.add_argument("--raw-out", help="Optional path to archive the downloaded CSV.")
    parser.add_argument("--since", default="2018-01-01", help="Inclusive start date, YYYY-MM-DD.")
    parser.add_argument("--until", help="Inclusive end date, YYYY-MM-DD.")
    parser.add_argument("--replace-source", action="store_true", help="Delete existing imported rows from this source first.")
    parser.add_argument("--stage", default="Group Stage", help="Stage label used for imported fixtures.")
    args = parser.parse_args()

    result = import_results(
        Path(args.db),
        Path(args.csv) if args.csv else None,
        args.url,
        Path(args.raw_out) if args.raw_out else None,
        args.since,
        args.until,
        args.replace_source,
        args.stage,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
