#!/usr/bin/env python3
"""Ingest FIFA men's ranking data from CSV or a best-effort official API endpoint."""

from __future__ import annotations

import argparse
import csv
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from common import coerce_value, connect, find_team_id, now_utc, slugify


API_CANDIDATES = [
    "https://api.fifa.com/api/v3/fifarankings/rankings/rankingsbyschedule?rankingScheduleId={schedule_id}",
    "https://inside.fifa.com/api/rankings/rankingsbyschedule?rankingScheduleId={schedule_id}",
    "https://inside.fifa.com/fifarankings/rankings/rankingsbyschedule?rankingScheduleId={schedule_id}",
]


def read_json_url(url: str) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json,text/plain,*/*",
            "User-Agent": "wc2026-skill/0.2 (+local research)",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def walk_records(payload: Any) -> list[dict[str, Any]]:
    """Find likely ranking rows in a changing FIFA JSON shape."""
    found: list[dict[str, Any]] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            keys = {str(k).lower() for k in value}
            has_rank = any(k in keys for k in ("rank", "ranking", "position", "rankposition"))
            has_team = any(
                k in keys or any(fragment in existing for existing in keys)
                for k, fragment in (
                    ("country", "country"),
                    ("team", "team"),
                    ("association", "association"),
                    ("fifacode", "fifacode"),
                    ("countrycode", "countrycode"),
                )
            )
            has_points = any("point" in k for k in keys)
            if has_rank and has_team and has_points:
                found.append(value)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload)
    return found


def first_value(row: dict[str, Any], *keys: str) -> Any:
    lower_map = {str(key).lower(): value for key, value in row.items()}
    for key in keys:
        if key.lower() in lower_map:
            return lower_map[key.lower()]
    return None


def nested_value(value: Any, *keys: str) -> Any:
    if isinstance(value, dict):
        return first_value(value, *keys)
    if isinstance(value, list):
        for item in value:
            found = nested_value(item, *keys)
            if found:
                return found
    return value


def normalize_api_row(row: dict[str, Any], ranking_date: str, ranking_type: str, source_id: str) -> dict[str, Any]:
    country = first_value(row, "country", "team", "association")
    code = first_value(row, "countryCode", "fifaCode", "code", "fifacode", "IdCountry")
    name = first_value(row, "teamName", "countryName", "name")
    name = nested_value(name, "name", "countryName", "description", "text") if name is not None else None
    if not name:
        name = nested_value(country, "name", "countryName", "description", "text")
    if not code:
        code = nested_value(country, "code", "fifaCode", "countryCode")
    rank = first_value(row, "rank", "ranking", "position", "rankPosition")
    points = first_value(row, "totalPoints", "points", "rankingPoints", "pointsTotal")
    previous_rank = first_value(row, "previousRank", "lastRank", "rankPrevious", "PrevRank")
    previous_points = first_value(row, "previousPoints", "lastPoints", "PrevPoints")
    confederation = first_value(row, "confederation", "confederationName")
    return {
        "ranking_date": ranking_date,
        "ranking_type": ranking_type,
        "team_id": code,
        "fifa_code": code,
        "team_name": name,
        "rank": rank,
        "points": points,
        "previous_rank": previous_rank,
        "previous_points": previous_points,
        "confederation": confederation,
        "source_id": source_id,
        "notes": "Fetched from FIFA ranking API candidate; verify API shape before production use.",
    }


def normalize_csv_row(row: dict[str, str], ranking_date: str, ranking_type: str, source_id: str) -> dict[str, Any]:
    return {
        "ranking_id": row.get("ranking_id"),
        "ranking_date": row.get("ranking_date") or ranking_date,
        "ranking_type": row.get("ranking_type") or ranking_type,
        "team_id": row.get("team_id"),
        "fifa_code": row.get("fifa_code") or row.get("code"),
        "team_name": row.get("team_name") or row.get("name"),
        "rank": row.get("rank") or row.get("ranking"),
        "points": row.get("points") or row.get("fifa_points"),
        "previous_rank": row.get("previous_rank"),
        "previous_points": row.get("previous_points"),
        "rank_change": row.get("rank_change"),
        "confederation": row.get("confederation"),
        "source_id": row.get("source_id") or source_id,
        "notes": row.get("notes"),
    }


def resolve_team(conn, row: dict[str, Any]) -> tuple[str | None, str | None]:
    for key in ("team_id", "fifa_code", "team_name"):
        value = row.get(key)
        if value:
            team_id = find_team_id(conn, str(value))
            if team_id:
                code = conn.execute("SELECT fifa_code FROM teams WHERE team_id = ?", (team_id,)).fetchone()["fifa_code"]
                return team_id, code
    return None, row.get("fifa_code")


def upsert_rows(conn, rows: list[dict[str, Any]]) -> int:
    inserted = 0
    for index, row in enumerate(rows, start=1):
        team_id, fifa_code = resolve_team(conn, row)
        ranking_date = str(row.get("ranking_date") or "")[:10]
        if not ranking_date:
            raise SystemExit("ranking_date is required for every FIFA ranking row.")
        ranking_type = str(row.get("ranking_type") or "official")
        rank = coerce_value("rank", row.get("rank"))
        points = coerce_value("points", row.get("points"))
        previous_rank = coerce_value("previous_rank", row.get("previous_rank"))
        previous_points = coerce_value("previous_points", row.get("previous_points"))
        rank_change = coerce_value("rank_change", row.get("rank_change"))
        if rank_change is None and rank is not None and previous_rank is not None:
            rank_change = int(previous_rank) - int(rank)
        ranking_id = row.get("ranking_id") or "ranking-" + slugify(
            "|".join([ranking_date, ranking_type, team_id or "", fifa_code or "", str(index)])
        )
        conn.execute(
            """
            INSERT INTO fifa_rankings (
                ranking_id, ranking_date, ranking_type, team_id, fifa_code, rank, points,
                previous_rank, previous_points, rank_change, confederation, source_id, notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ranking_id) DO UPDATE SET
                ranking_date = excluded.ranking_date,
                ranking_type = excluded.ranking_type,
                team_id = excluded.team_id,
                fifa_code = excluded.fifa_code,
                rank = excluded.rank,
                points = excluded.points,
                previous_rank = excluded.previous_rank,
                previous_points = excluded.previous_points,
                rank_change = excluded.rank_change,
                confederation = excluded.confederation,
                source_id = excluded.source_id,
                notes = excluded.notes
            """,
            (
                ranking_id,
                ranking_date,
                ranking_type,
                team_id,
                fifa_code,
                rank,
                points,
                previous_rank,
                previous_points,
                rank_change,
                row.get("confederation"),
                row.get("source_id") or "fifa_mens_ranking",
                row.get("notes"),
            ),
        )
        inserted += 1
    return inserted


def sync_latest_to_teams(conn) -> int:
    rows = conn.execute(
        """
        SELECT r.*
        FROM fifa_rankings r
        JOIN (
            SELECT team_id, MAX(ranking_date) AS ranking_date
            FROM fifa_rankings
            WHERE team_id IS NOT NULL
              AND rank IS NOT NULL
              AND lower(coalesce(ranking_type, 'official')) IN ('official', 'fifa-official')
            GROUP BY team_id
        ) latest
          ON latest.team_id = r.team_id
         AND latest.ranking_date = r.ranking_date
        """
    ).fetchall()
    for row in rows:
        conn.execute(
            """
            UPDATE teams
            SET fifa_rank = ?, fifa_points = ?, last_verified_at = COALESCE(last_verified_at, ?)
            WHERE team_id = ?
            """,
            (row["rank"], row["points"], now_utc(), row["team_id"]),
        )
    return len(rows)


def load_csv(path: Path, ranking_date: str, ranking_type: str, source_id: str) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        return [normalize_csv_row(row, ranking_date, ranking_type, source_id) for row in reader]


def load_api(schedule_id: str, ranking_date: str, ranking_type: str, source_id: str) -> tuple[list[dict[str, Any]], str]:
    errors = []
    for template in API_CANDIDATES:
        url = template.format(schedule_id=schedule_id)
        try:
            payload = read_json_url(url)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            errors.append(f"{url}: {exc}")
            continue
        rows = walk_records(payload)
        if rows:
            return [normalize_api_row(row, ranking_date, ranking_type, source_id) for row in rows], url
        errors.append(f"{url}: no ranking rows detected")
    raise SystemExit("Could not fetch FIFA ranking API. Tried:\n" + "\n".join(errors))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, help="SQLite database path.")
    parser.add_argument("--csv", help="CSV with ranking rows. Use this for stable/reviewed imports.")
    parser.add_argument("--schedule-id", help="Best-effort FIFA ranking schedule id, e.g. FRS_Male_Football_20260401.")
    parser.add_argument("--ranking-date", required=True, help="Ranking publication date, YYYY-MM-DD.")
    parser.add_argument("--ranking-type", default="official", help="official, live, provider, or manual.")
    parser.add_argument("--source-id", default="fifa_mens_ranking", help="Source id stored on rows.")
    parser.add_argument("--sync-teams", action="store_true", help="Copy latest official ranking into teams.")
    args = parser.parse_args()

    if bool(args.csv) == bool(args.schedule_id):
        raise SystemExit("Provide exactly one of --csv or --schedule-id.")

    if args.csv:
        rows = load_csv(Path(args.csv), args.ranking_date, args.ranking_type, args.source_id)
        source_label = args.csv
    else:
        rows, source_label = load_api(args.schedule_id, args.ranking_date, args.ranking_type, args.source_id)

    conn = connect(args.db)
    try:
        count = upsert_rows(conn, rows)
        synced = sync_latest_to_teams(conn) if args.sync_teams else 0
        conn.commit()
    finally:
        conn.close()
    print(f"Imported {count} FIFA ranking rows from {source_label}")
    if args.sync_teams:
        print(f"Synced latest official ranking to {synced} teams")


if __name__ == "__main__":
    main()
