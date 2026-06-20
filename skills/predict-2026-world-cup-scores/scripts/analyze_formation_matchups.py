#!/usr/bin/env python3
"""Aggregate historical formation-vs-formation outcomes for matchup priors."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from common import connect, slugify


def latest_formation_before(conn, team_id: str, match_date: str | None) -> str | None:
    if match_date:
        row = conn.execute(
            """
            SELECT formation_primary
            FROM team_style_profiles
            WHERE team_id = ?
              AND date(profile_date) <= date(?)
              AND formation_primary IS NOT NULL
            ORDER BY
                profile_date DESC,
                CASE source_id
                    WHEN 'derived_team_features' THEN 0
                    WHEN 'manual_enhancement_feed' THEN 1
                    ELSE 2
                END
            LIMIT 1
            """,
            (team_id, match_date),
        ).fetchone()
        if row is not None:
            return row["formation_primary"]
    row = conn.execute(
        """
        SELECT formation_primary
        FROM team_style_profiles
        WHERE team_id = ?
          AND formation_primary IS NOT NULL
        ORDER BY
            profile_date DESC,
            CASE source_id
                WHEN 'derived_team_features' THEN 0
                WHEN 'manual_enhancement_feed' THEN 1
                ELSE 2
            END
        LIMIT 1
        """,
        (team_id,),
    ).fetchone()
    return None if row is None else row["formation_primary"]


def completed_matches(conn, since: str | None, until: str | None) -> list[dict]:
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
        SELECT *
        FROM fixtures
        WHERE {' AND '.join(filters)}
        ORDER BY match_date
        """,
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def analyze(db_path: Path, since: str | None, until: str | None, min_sample: int) -> dict:
    conn = connect(db_path)
    buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
    try:
        for match in completed_matches(conn, since, until):
            formation_a = latest_formation_before(conn, match["team_a_id"], match["match_date"])
            formation_b = latest_formation_before(conn, match["team_b_id"], match["match_date"])
            if not formation_a or not formation_b:
                continue
            buckets[(formation_a, formation_b)].append(match)

        written = 0
        for (formation_a, formation_b), matches in buckets.items():
            sample_size = len(matches)
            if sample_size < min_sample:
                continue
            a_wins = sum(1 for match in matches if match["score_a"] > match["score_b"])
            draws = sum(1 for match in matches if match["score_a"] == match["score_b"])
            b_wins = sample_size - a_wins - draws
            avg_goals_a = sum(float(match["score_a"]) for match in matches) / sample_size
            avg_goals_b = sum(float(match["score_b"]) for match in matches) / sample_size
            score_counter = Counter(f"{match['score_a']}-{match['score_b']}" for match in matches)
            scorelines = [
                {"score": score, "count": count, "rate": round(count / sample_size, 4)}
                for score, count in score_counter.most_common(8)
            ]
            matchup_id = f"formation-{slugify(formation_a)}-vs-{slugify(formation_b)}"
            conn.execute(
                """
                INSERT INTO formation_matchup_stats (
                    matchup_id, formation_a, formation_b, sample_size, p_a_win, p_draw, p_b_win,
                    avg_goals_a, avg_goals_b, scoreline_json, source_id, notes
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(matchup_id) DO UPDATE SET
                    sample_size = excluded.sample_size,
                    p_a_win = excluded.p_a_win,
                    p_draw = excluded.p_draw,
                    p_b_win = excluded.p_b_win,
                    avg_goals_a = excluded.avg_goals_a,
                    avg_goals_b = excluded.avg_goals_b,
                    scoreline_json = excluded.scoreline_json,
                    source_id = excluded.source_id,
                    notes = excluded.notes
                """,
                (
                    matchup_id,
                    formation_a,
                    formation_b,
                    sample_size,
                    a_wins / sample_size,
                    draws / sample_size,
                    b_wins / sample_size,
                    avg_goals_a,
                    avg_goals_b,
                    json.dumps(scorelines, ensure_ascii=True),
                    "manual_enhancement_feed",
                    "Aggregated from completed fixtures and latest available team_style_profiles.",
                ),
            )
            written += 1
        conn.commit()
        return {"formation_matchups": written, "eligible_buckets": len(buckets)}
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, help="SQLite database path.")
    parser.add_argument("--since", help="Inclusive start date, YYYY-MM-DD.")
    parser.add_argument("--until", help="Inclusive end date, YYYY-MM-DD.")
    parser.add_argument("--min-sample", type=int, default=1, help="Minimum matches per formation pair.")
    args = parser.parse_args()

    result = analyze(Path(args.db), args.since, args.until, args.min_sample)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
