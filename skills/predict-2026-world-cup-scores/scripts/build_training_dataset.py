#!/usr/bin/env python3
"""Build a persistent weighted training-match cache from local match tables."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from common import connect, dedupe_match_rows, now_utc, slugify, table_exists


SOURCE_ID = "local_training_dataset"


def ensure_schema(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS training_matches (
            training_match_id TEXT PRIMARY KEY,
            source_table TEXT NOT NULL,
            source_match_id TEXT NOT NULL,
            domain TEXT NOT NULL,
            competition TEXT,
            competition_family TEXT,
            match_date TEXT,
            stage TEXT,
            team_a_id TEXT,
            team_b_id TEXT,
            team_a_name TEXT,
            team_b_name TEXT,
            score_a INTEGER,
            score_b INTEGER,
            xg_a REAL,
            xg_b REAL,
            shots_a INTEGER,
            shots_b INTEGER,
            neutral_site INTEGER DEFAULT 1,
            is_world_cup INTEGER DEFAULT 0,
            is_knockout INTEGER DEFAULT 0,
            data_quality REAL,
            sample_weight REAL DEFAULT 1.0,
            source_id TEXT,
            imported_at TEXT,
            notes TEXT
        )
        """
    )
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_training_matches_source ON training_matches(source_table, source_match_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_training_matches_date ON training_matches(match_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_training_matches_domain_weight ON training_matches(domain, sample_weight)")
    conn.execute(
        """
        INSERT INTO sources (
            source_id, name, category, publisher, retrieved_at, freshness_days,
            reliability, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_id) DO UPDATE SET
            retrieved_at = excluded.retrieved_at,
            notes = excluded.notes
        """,
        (
            SOURCE_ID,
            "Local weighted training dataset",
            "derived_training_cache",
            "Local model",
            now_utc(),
            7,
            "model-derived",
            "Persistent weighted cache built from fixtures and club_matches to avoid repeated source scans during training.",
        ),
    )


def competition_family(competition: str | None, stage: str | None = None) -> str:
    text = f"{competition or ''} {stage or ''}".lower()
    if "world cup" in text and "qualification" not in text:
        return "world_cup"
    if "world cup" in text and "qualification" in text:
        return "world_cup_qualification"
    if "euro" in text or "copa am" in text or "african cup" in text or "asian cup" in text or "gold cup" in text:
        return "continental_cup"
    if "nations league" in text or "qualification" in text:
        return "qualifier_or_nations"
    if "champions league" in text:
        return "champions_league"
    if "league" in text or "bundesliga" in text or "liga" in text or "serie a" in text or "premier" in text:
        return "club_league"
    if "friendly" in text:
        return "friendly"
    return "other"


def is_knockout_stage(stage: str | None) -> bool:
    text = (stage or "").lower()
    return any(token in text for token in ("round", "quarter", "semi", "final", "knockout", "playoff", "play-off"))


def sample_weight(
    domain: str,
    competition: str | None,
    stage: str | None,
    has_xg: bool,
    world_cup_weight: float,
    club_weight: float,
) -> tuple[float, str]:
    family = competition_family(competition, stage)
    weight = 1.0
    reason = family
    if family == "world_cup":
        weight = world_cup_weight
        reason = "world_cup_main_tournament"
    elif family == "world_cup_qualification":
        weight = 1.08
    elif family == "continental_cup":
        weight = 1.06
    elif family == "qualifier_or_nations":
        weight = 1.00
    elif family == "friendly":
        weight = 0.72
    elif domain == "club":
        weight = club_weight
    if domain == "club" and family == "champions_league":
        weight = max(weight, 0.92)
    if has_xg:
        weight += 0.04
    if is_knockout_stage(stage):
        weight += 0.03
    return round(weight, 4), reason


def upsert_training_match(conn, row: dict) -> None:
    conn.execute(
        """
        INSERT INTO training_matches (
            training_match_id, source_table, source_match_id, domain, competition,
            competition_family, match_date, stage, team_a_id, team_b_id,
            team_a_name, team_b_name, score_a, score_b, xg_a, xg_b,
            shots_a, shots_b, neutral_site, is_world_cup, is_knockout,
            data_quality, sample_weight, source_id, imported_at, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_table, source_match_id) DO UPDATE SET
            domain = excluded.domain,
            competition = excluded.competition,
            competition_family = excluded.competition_family,
            match_date = excluded.match_date,
            stage = excluded.stage,
            team_a_id = excluded.team_a_id,
            team_b_id = excluded.team_b_id,
            team_a_name = excluded.team_a_name,
            team_b_name = excluded.team_b_name,
            score_a = excluded.score_a,
            score_b = excluded.score_b,
            xg_a = excluded.xg_a,
            xg_b = excluded.xg_b,
            shots_a = excluded.shots_a,
            shots_b = excluded.shots_b,
            neutral_site = excluded.neutral_site,
            is_world_cup = excluded.is_world_cup,
            is_knockout = excluded.is_knockout,
            data_quality = excluded.data_quality,
            sample_weight = excluded.sample_weight,
            source_id = excluded.source_id,
            imported_at = excluded.imported_at,
            notes = excluded.notes
        """,
        (
            row["training_match_id"],
            row["source_table"],
            row["source_match_id"],
            row["domain"],
            row["competition"],
            row["competition_family"],
            row["match_date"],
            row["stage"],
            row["team_a_id"],
            row["team_b_id"],
            row["team_a_name"],
            row["team_b_name"],
            row["score_a"],
            row["score_b"],
            row.get("xg_a"),
            row.get("xg_b"),
            row.get("shots_a"),
            row.get("shots_b"),
            row["neutral_site"],
            row["is_world_cup"],
            row["is_knockout"],
            row["data_quality"],
            row["sample_weight"],
            SOURCE_ID,
            row["imported_at"],
            row["notes"],
        ),
    )


def build_from_fixtures(conn, since: str | None, until: str | None, world_cup_weight: float, imported_at: str) -> tuple[int, int]:
    filters = [
        "team_a_id IS NOT NULL",
        "team_b_id IS NOT NULL",
        "score_a IS NOT NULL",
        "score_b IS NOT NULL",
        "lower(coalesce(status, 'final')) = 'final'",
    ]
    params: list[str] = []
    if since:
        filters.append("date(match_date) >= date(?)")
        params.append(since)
    if until:
        filters.append("date(match_date) <= date(?)")
        params.append(until)
    rows = conn.execute(
        f"""
        SELECT f.*, ta.name AS team_a_name, tb.name AS team_b_name
        FROM fixtures f
        LEFT JOIN teams ta ON ta.team_id = f.team_a_id
        LEFT JOIN teams tb ON tb.team_id = f.team_b_id
        WHERE {' AND '.join(filters)}
        ORDER BY match_date, match_id
        """,
        params,
    ).fetchall()
    rows, duplicates = dedupe_match_rows(
        [dict(row) for row in rows],
        team_a_key="team_a_id",
        team_b_key="team_b_id",
        score_a_key="score_a",
        score_b_key="score_b",
    )
    count = 0
    for raw in rows:
        row = raw
        family = competition_family(row.get("competition"), row.get("stage"))
        weight, reason = sample_weight(
            "international",
            row.get("competition"),
            row.get("stage"),
            False,
            world_cup_weight,
            0.88,
        )
        upsert_training_match(
            conn,
            {
                "training_match_id": f"training-fixture-{slugify(row['match_id'])}",
                "source_table": "fixtures",
                "source_match_id": row["match_id"],
                "domain": "international",
                "competition": row.get("competition"),
                "competition_family": family,
                "match_date": row.get("match_date"),
                "stage": row.get("stage"),
                "team_a_id": row.get("team_a_id"),
                "team_b_id": row.get("team_b_id"),
                "team_a_name": row.get("team_a_name") or row.get("team_a_id"),
                "team_b_name": row.get("team_b_name") or row.get("team_b_id"),
                "score_a": row.get("score_a"),
                "score_b": row.get("score_b"),
                "neutral_site": 1,
                "is_world_cup": 1 if family == "world_cup" else 0,
                "is_knockout": 1 if is_knockout_stage(row.get("stage")) else 0,
                "data_quality": 0.72,
                "sample_weight": weight,
                "imported_at": imported_at,
                "notes": f"Built from fixtures; weight_reason={reason}.",
            },
        )
        count += 1
    return count, duplicates


def build_from_club_matches(conn, since: str | None, until: str | None, club_weight: float, imported_at: str) -> int:
    if not table_exists(conn, "club_matches") or not table_exists(conn, "club_team_match_stats"):
        return 0
    filters = [
        "cm.home_score IS NOT NULL",
        "cm.away_score IS NOT NULL",
    ]
    params: list[str] = []
    if since:
        filters.append("date(cm.match_date) >= date(?)")
        params.append(since)
    if until:
        filters.append("date(cm.match_date) <= date(?)")
        params.append(until)
    rows = conn.execute(
        f"""
        SELECT
            cm.*,
            cc.competition_name,
            cc.season_name,
            cc.is_international,
            ht.name AS home_name,
            at.name AS away_name,
            hts.xg AS home_xg,
            ats.xg AS away_xg,
            hts.shots AS home_shots,
            ats.shots AS away_shots
        FROM club_matches cm
        LEFT JOIN club_competitions cc ON cc.club_competition_id = cm.club_competition_id
        LEFT JOIN club_teams ht ON ht.club_team_id = cm.home_team_id
        LEFT JOIN club_teams at ON at.club_team_id = cm.away_team_id
        LEFT JOIN club_team_match_stats hts
          ON hts.club_match_id = cm.club_match_id
         AND hts.club_team_id = cm.home_team_id
        LEFT JOIN club_team_match_stats ats
          ON ats.club_match_id = cm.club_match_id
         AND ats.club_team_id = cm.away_team_id
        WHERE {' AND '.join(filters)}
        ORDER BY cm.match_date, cm.club_match_id
        """,
        params,
    ).fetchall()
    count = 0
    for raw in rows:
        row = dict(raw)
        competition = row.get("competition_name") or "Club"
        stage = row.get("stage") or row.get("round_name")
        family = competition_family(competition, stage)
        provider_international = bool(row.get("is_international")) or family in {
            "world_cup",
            "world_cup_qualification",
            "continental_cup",
            "qualifier_or_nations",
        }
        domain = "statsbomb_international" if provider_international else "club"
        has_xg = row.get("home_xg") is not None and row.get("away_xg") is not None
        weight, reason = sample_weight(domain, competition, stage, has_xg, 1.25, club_weight)
        upsert_training_match(
            conn,
            {
                "training_match_id": f"training-club-{slugify(row['club_match_id'])}",
                "source_table": "club_matches",
                "source_match_id": row["club_match_id"],
                "domain": domain,
                "competition": competition,
                "competition_family": family,
                "match_date": row.get("match_date"),
                "stage": stage,
                "team_a_id": row.get("home_team_id"),
                "team_b_id": row.get("away_team_id"),
                "team_a_name": row.get("home_name") or row.get("home_team_id"),
                "team_b_name": row.get("away_name") or row.get("away_team_id"),
                "score_a": row.get("home_score"),
                "score_b": row.get("away_score"),
                "xg_a": row.get("home_xg"),
                "xg_b": row.get("away_xg"),
                "shots_a": row.get("home_shots"),
                "shots_b": row.get("away_shots"),
                "neutral_site": 1 if provider_international else 0,
                "is_world_cup": 1 if family == "world_cup" else 0,
                "is_knockout": 1 if is_knockout_stage(stage) else 0,
                "data_quality": 0.90 if has_xg else 0.70,
                "sample_weight": weight,
                "imported_at": imported_at,
                "notes": (
                    "Built from club_matches; "
                    f"domain={domain}; season={row.get('season_name')}; weight_reason={reason}."
                ),
            },
        )
        count += 1
    return count


def build_training_dataset(
    db_path: Path,
    since: str | None,
    until: str | None,
    include_club: bool,
    replace: bool,
    world_cup_weight: float,
    club_weight: float,
) -> dict:
    conn = connect(db_path)
    imported_at = now_utc()
    try:
        ensure_schema(conn)
        if replace:
            conn.execute("DELETE FROM training_matches")
        international, duplicate_fixtures_skipped = build_from_fixtures(conn, since, until, world_cup_weight, imported_at)
        club = build_from_club_matches(conn, since, until, club_weight, imported_at) if include_club else 0
        conn.commit()
        summary = conn.execute(
            """
            SELECT
                COUNT(*) AS rows,
                SUM(sample_weight) AS total_weight,
                SUM(CASE WHEN is_world_cup = 1 THEN 1 ELSE 0 END) AS world_cup_rows,
                SUM(CASE WHEN domain = 'club' THEN 1 ELSE 0 END) AS club_rows,
                SUM(CASE WHEN domain = 'statsbomb_international' THEN 1 ELSE 0 END) AS statsbomb_international_rows
            FROM training_matches
            """
        ).fetchone()
        family_rows = conn.execute(
            """
            SELECT competition_family, COUNT(*) AS rows, ROUND(AVG(sample_weight), 4) AS avg_weight
            FROM training_matches
            GROUP BY competition_family
            ORDER BY rows DESC
            LIMIT 12
            """
        ).fetchall()
        return {
            "training_matches": int(summary["rows"] or 0),
            "total_weight": round(float(summary["total_weight"] or 0.0), 3),
            "international_upserted": international,
            "duplicate_fixtures_skipped": duplicate_fixtures_skipped,
            "club_upserted": club,
            "world_cup_rows": int(summary["world_cup_rows"] or 0),
            "club_rows": int(summary["club_rows"] or 0),
            "statsbomb_international_rows": int(summary["statsbomb_international_rows"] or 0),
            "family_sample": [dict(row) for row in family_rows],
        }
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, help="SQLite database path.")
    parser.add_argument("--since", help="Inclusive start date, YYYY-MM-DD.")
    parser.add_argument("--until", help="Inclusive end date, YYYY-MM-DD.")
    parser.add_argument("--include-club", action="store_true", help="Include local club_matches rows.")
    parser.add_argument("--replace", action="store_true", help="Clear training_matches before rebuilding.")
    parser.add_argument("--world-cup-weight", type=float, default=1.25, help="Sample weight for World Cup main tournament matches.")
    parser.add_argument("--club-weight", type=float, default=0.88, help="Base sample weight for club league/cup matches.")
    args = parser.parse_args()
    result = build_training_dataset(
        Path(args.db),
        args.since,
        args.until,
        args.include_club,
        args.replace,
        args.world_cup_weight,
        args.club_weight,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
