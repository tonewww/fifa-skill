#!/usr/bin/env python3
"""Ingest source-checked completed match results from JSON."""

from __future__ import annotations

import argparse
import json
import unicodedata
from pathlib import Path

from common import connect, now_utc, slugify


ALIASES = {
    "cabo verde": "CPV",
    "cape verde": "CPV",
    "curacao": "CUW",
    "cote divoire": "CIV",
    "cote d ivoire": "CIV",
    "ivory coast": "CIV",
    "ir iran": "IRN",
    "iran": "IRN",
    "saudi": "KSA",
    "saudi arabia": "KSA",
    "turkiye": "TUR",
    "turkey": "TUR",
    "united states": "USA",
    "usa": "USA",
    "佛得角": "CPV",
    "沙特": "KSA",
    "伊朗": "IRN",
    "土耳其": "TUR",
    "美国": "USA",
    "挪威": "NOR",
    "法国": "FRA",
    "塞内加尔": "SEN",
    "伊拉克": "IRQ",
    "乌拉圭": "URU",
    "西班牙": "ESP",
    "埃及": "EGY",
    "新西兰": "NZL",
    "比利时": "BEL",
    "英格兰": "ENG",
    "刚果金": "COD",
    "波黑": "BIH",
    "厄瓜多尔": "ECU",
    "德国": "GER",
    "库拉索": "CUW",
    "科特迪瓦": "CIV",
    "突尼斯": "TUN",
    "荷兰": "NED",
    "日本": "JPN",
    "瑞典": "SWE",
    "巴拉圭": "PAR",
    "澳大利亚": "AUS",
    "南非": "RSA",
    "加拿大": "CAN",
    "巴西": "BRA",
    "巴拉圭": "PAR",
    "摩洛哥": "MAR",
    "墨西哥": "MEX",
}


def normalize_name(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(char for char in text if not unicodedata.combining(char))
    keep = [char.lower() if char.isalnum() else " " for char in text]
    return " ".join("".join(keep).split())


def team_name_map(conn) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for row in conn.execute("SELECT team_id, fifa_code, name, short_name FROM teams"):
        for key in (row["team_id"], row["fifa_code"], row["name"], row["short_name"]):
            normalized = normalize_name(key)
            if normalized:
                mapping[normalized] = row["team_id"]
    for alias, team_id in ALIASES.items():
        if conn.execute("SELECT 1 FROM teams WHERE team_id = ?", (team_id,)).fetchone():
            mapping[normalize_name(alias)] = team_id
    return mapping


def resolve_team(name: str, mapping: dict[str, str]) -> str:
    team_id = mapping.get(normalize_name(name))
    if not team_id:
        raise SystemExit(f"Could not map team name '{name}' to a local team_id.")
    return team_id


def source_id_for(date: str) -> str:
    return f"verified_results_{date.replace('-', '_')}"


def upsert_source(conn, source_id: str, date: str, url: str, notes: str) -> None:
    conn.execute(
        """
        INSERT INTO sources (
            source_id, name, url, category, publisher, retrieved_at,
            freshness_days, license_note, reliability, notes
        )
        VALUES (?, ?, ?, 'match_results', 'FIFA/media cross-check', ?, 2, ?, 'verified-media', ?)
        ON CONFLICT(source_id) DO UPDATE SET
            url = excluded.url,
            retrieved_at = excluded.retrieved_at,
            notes = excluded.notes
        """,
        (
            source_id,
            f"Verified 2026 World Cup results, {date}",
            url,
            now_utc(),
            "Final scores only; source URLs retained for provenance and re-checking.",
            notes,
        ),
    )


def result_notes(item: dict) -> str:
    urls = item.get("source_urls") or []
    source_text = ", ".join(urls) if urls else "source URLs not provided"
    notes = item.get("notes") or "Final score manually verified."
    return f"{notes} Sources: {source_text}"


def upsert_match(conn, item: dict, mapping: dict[str, str], date: str, source_id: str) -> dict:
    home_id = resolve_team(item["home_team"], mapping)
    away_id = resolve_team(item["away_team"], mapping)
    match_date = item.get("match_date") or date
    stage = item.get("stage") or "Group Stage"
    match_id = item.get("match_id") or f"verified-{match_date}-{home_id.lower()}-{away_id.lower()}"
    notes = result_notes(item)
    score_a = int(item["home_score"])
    score_b = int(item["away_score"])
    conn.execute(
        """
        INSERT INTO fixtures (
            match_id, competition, stage, group_name, match_date, venue, city, country,
            team_a_id, team_b_id, score_a, score_b, status, source_id, notes
        )
        VALUES (?, 'FIFA World Cup 2026', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'final', ?, ?)
        ON CONFLICT(match_id) DO UPDATE SET
            competition = excluded.competition,
            stage = excluded.stage,
            group_name = COALESCE(excluded.group_name, fixtures.group_name),
            match_date = excluded.match_date,
            venue = COALESCE(excluded.venue, fixtures.venue),
            city = COALESCE(excluded.city, fixtures.city),
            country = COALESCE(excluded.country, fixtures.country),
            team_a_id = excluded.team_a_id,
            team_b_id = excluded.team_b_id,
            score_a = excluded.score_a,
            score_b = excluded.score_b,
            status = excluded.status,
            source_id = excluded.source_id,
            notes = excluded.notes
        """,
        (
            match_id,
            stage,
            item.get("group_name"),
            match_date,
            item.get("venue"),
            item.get("city"),
            item.get("country"),
            home_id,
            away_id,
            score_a,
            score_b,
            source_id,
            notes,
        ),
    )
    result_rows = [
        (home_id, away_id, score_a, score_b, item.get("home_xg"), item.get("away_xg")),
        (away_id, home_id, score_b, score_a, item.get("away_xg"), item.get("home_xg")),
    ]
    for team_id, opponent_id, goals_for, goals_against, xg_for, xg_against in result_rows:
        result_id = f"{match_id}-{team_id.lower()}"
        conn.execute(
            """
            INSERT INTO team_results (
                result_id, match_date, team_id, opponent_team_id, venue_type,
                competition, is_neutral, goals_for, goals_against, xg_for,
                xg_against, source_id, notes
            )
            VALUES (?, ?, ?, ?, 'neutral', 'FIFA World Cup 2026', 1, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(result_id) DO UPDATE SET
                match_date = excluded.match_date,
                opponent_team_id = excluded.opponent_team_id,
                venue_type = excluded.venue_type,
                competition = excluded.competition,
                is_neutral = excluded.is_neutral,
                goals_for = excluded.goals_for,
                goals_against = excluded.goals_against,
                xg_for = excluded.xg_for,
                xg_against = excluded.xg_against,
                source_id = excluded.source_id,
                notes = excluded.notes
            """,
            (
                result_id,
                match_date,
                team_id,
                opponent_id,
                goals_for,
                goals_against,
                xg_for,
                xg_against,
                source_id,
                notes,
            ),
        )
    return {
        "match_id": match_id,
        "match_date": match_date,
        "team_a_id": home_id,
        "team_b_id": away_id,
        "score_a": score_a,
        "score_b": score_b,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, help="SQLite database path.")
    parser.add_argument("--results-json", required=True, help="Completed results JSON.")
    parser.add_argument("--date", required=True, help="Default match/source date, e.g. 2026-06-26.")
    args = parser.parse_args()

    path = Path(args.results_json)
    items = json.loads(path.read_text(encoding="utf-8"))
    conn = connect(args.db)
    try:
        mapping = team_name_map(conn)
        source_urls = []
        for item in items:
            source_urls.extend(item.get("source_urls") or [])
        primary_url = source_urls[0] if source_urls else str(path)
        source_id = source_id_for(args.date)
        upsert_source(
            conn,
            source_id,
            args.date,
            primary_url,
            f"Imported from {path}; {len(items)} completed matches. Extra URLs: {', '.join(source_urls[1:8])}",
        )
        rows = [upsert_match(conn, item, mapping, args.date, source_id) for item in items]
        conn.commit()
    finally:
        conn.close()
    print(json.dumps({"source_id": source_id, "matches": rows}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
