#!/usr/bin/env python3
"""Build dated team strength snapshots from team, player, form, and style data."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

from common import MODEL_VERSION, average, clamp, connect, table_exists, today_utc, weighted_average


DEFAULTS = {
    "fifa_component": 50.0,
    "squad_quality": 50.0,
    "attack_rating": 50.0,
    "defense_rating": 50.0,
    "possession_rating": 50.0,
    "transition_rating": 50.0,
    "set_piece_rating": 50.0,
    "goalkeeper_rating": 50.0,
    "depth_rating": 45.0,
    "form_rating": 50.0,
    "experience_rating": 50.0,
    "fitness_rating": 50.0,
    "coaching_rating": 50.0,
}


def fifa_component(rank: int | None, points: float | None) -> float:
    if points is not None and points > 0:
        return clamp(35.0 + min(points, 2100.0) / 2100.0 * 65.0)
    if rank is not None and rank > 0:
        return clamp(100.0 - (rank - 1) * 0.65, 25.0, 100.0)
    return DEFAULTS["fifa_component"]


def latest_fifa_ranking(conn, team_id: str) -> dict:
    if not table_exists(conn, "fifa_rankings"):
        return {}
    row = conn.execute(
        """
        SELECT *
        FROM fifa_rankings
        WHERE team_id = ?
          AND rank IS NOT NULL
        ORDER BY
            CASE lower(coalesce(ranking_type, 'official'))
                WHEN 'official' THEN 0
                WHEN 'fifa-official' THEN 0
                WHEN 'live' THEN 1
                ELSE 2
            END,
            ranking_date DESC
        LIMIT 1
        """,
        (team_id,),
    ).fetchone()
    return {} if row is None else dict(row)


def latest_player_rating_map(conn, team_id: str) -> dict[str, dict]:
    if not table_exists(conn, "player_ratings"):
        return {}
    rows = conn.execute(
        """
        SELECT pr.*
        FROM player_ratings pr
        JOIN (
            SELECT player_id, MAX(rating_date) AS rating_date
            FROM player_ratings
            WHERE team_id = ?
               OR player_id IN (SELECT player_id FROM players WHERE team_id = ?)
            GROUP BY player_id
        ) latest
          ON latest.player_id = pr.player_id
         AND latest.rating_date = pr.rating_date
        """,
        (team_id, team_id),
    ).fetchall()
    return {row["player_id"]: dict(row) for row in rows if row["player_id"]}


def latest_injury_map(conn, team_id: str) -> dict[str, dict]:
    if not table_exists(conn, "injuries"):
        return {}
    rows = conn.execute(
        """
        SELECT i.*
        FROM injuries i
        JOIN (
            SELECT player_id, MAX(verified_at) AS verified_at
            FROM injuries
            WHERE team_id = ?
               OR player_id IN (SELECT player_id FROM players WHERE team_id = ?)
            GROUP BY player_id
        ) latest
          ON latest.player_id = i.player_id
         AND latest.verified_at = i.verified_at
        """,
        (team_id, team_id),
    ).fetchall()
    return {row["player_id"]: dict(row) for row in rows if row["player_id"]}


def latest_lineup_minutes(conn, team_id: str) -> tuple[dict[str, dict], str | None]:
    if not table_exists(conn, "lineups") or not table_exists(conn, "lineup_players"):
        return {}, None
    lineup = conn.execute(
        """
        SELECT *
        FROM lineups
        WHERE team_id = ?
        ORDER BY
            CASE lower(coalesce(lineup_type, 'expected'))
                WHEN 'official' THEN 0
                WHEN 'confirmed' THEN 1
                WHEN 'expected' THEN 2
                ELSE 3
            END,
            as_of DESC
        LIMIT 1
        """,
        (team_id,),
    ).fetchone()
    if lineup is None:
        return {}, None
    rows = conn.execute(
        "SELECT * FROM lineup_players WHERE lineup_id = ?",
        (lineup["lineup_id"],),
    ).fetchall()
    return {row["player_id"]: dict(row) for row in rows if row["player_id"]}, lineup["lineup_type"]


def player_feature_coverage(conn, team_id: str) -> dict:
    if not table_exists(conn, "player_feature_snapshots"):
        return {"known_player_features": 0, "feature_sample_minutes": 0.0}
    row = conn.execute(
        """
        SELECT
            COUNT(DISTINCT pfs.player_id) AS players,
            COALESCE(SUM(pfs.sample_minutes), 0) AS minutes
        FROM player_feature_snapshots pfs
        WHERE pfs.team_id = ?
          AND pfs.snapshot_date = (
              SELECT MAX(snapshot_date)
              FROM player_feature_snapshots
              WHERE team_id = ?
          )
        """,
        (team_id, team_id),
    ).fetchone()
    if row is None:
        return {"known_player_features": 0, "feature_sample_minutes": 0.0}
    return {
        "known_player_features": int(row["players"] or 0),
        "feature_sample_minutes": float(row["minutes"] or 0.0),
    }


def row_rating(player: dict, provider: dict | None, player_key: str, provider_key: str) -> float | None:
    if provider and provider.get(provider_key) is not None:
        return provider[provider_key]
    return player.get(player_key)


def player_weight(player: dict, lineup_row: dict | None, injury_row: dict | None) -> float:
    if str(player.get("status") or "").lower() in {"out", "suspended", "withdrawn"} and injury_row is None:
        return 0.0
    minutes = player.get("minutes_expected")
    if lineup_row:
        minutes = lineup_row.get("minutes_expected") if lineup_row.get("minutes_expected") is not None else minutes
        if minutes is None:
            minutes = 75.0 if lineup_row.get("is_starter") else 20.0
    if minutes is None:
        minutes = 25.0
    availability = 100.0
    if lineup_row and lineup_row.get("availability_pct") is not None:
        availability = min(availability, float(lineup_row["availability_pct"]))
    if injury_row and injury_row.get("availability_pct") is not None:
        availability = min(availability, float(injury_row["availability_pct"]))
    elif injury_row and str(injury_row.get("status") or "").lower() in {"out", "suspended", "withdrawn"}:
        availability = 0.0
    return max(float(minutes), 0.0) * clamp(availability, 0, 100) / 100.0


def player_components(conn, team_id: str) -> dict[str, float]:
    rows = conn.execute(
        """
        SELECT *
        FROM players
        WHERE team_id = ?
        """,
        (team_id,),
    ).fetchall()
    rating_map = latest_player_rating_map(conn, team_id)
    injury_map = latest_injury_map(conn, team_id)
    lineup_map, lineup_type = latest_lineup_minutes(conn, team_id)
    feature_coverage = player_feature_coverage(conn, team_id)
    rows = [dict(row) for row in rows]
    if not rows:
        return {
            "squad_quality": DEFAULTS["squad_quality"],
            "attack_rating": DEFAULTS["attack_rating"],
            "defense_rating": DEFAULTS["defense_rating"],
            "possession_rating": DEFAULTS["possession_rating"],
            "transition_rating": DEFAULTS["transition_rating"],
            "set_piece_rating": DEFAULTS["set_piece_rating"],
            "goalkeeper_rating": DEFAULTS["goalkeeper_rating"],
            "depth_rating": DEFAULTS["depth_rating"],
            "experience_rating": DEFAULTS["experience_rating"],
            "fitness_rating": DEFAULTS["fitness_rating"],
            "known_player_ratings": 0,
            "known_provider_ratings": 0,
            "known_injuries": 0,
            "known_lineup_players": 0,
            "known_player_features": 0,
            "feature_sample_minutes": 0.0,
            "lineup_type": None,
        }

    weighted_overall = weighted_average(
        [
            (
                row_rating(row, rating_map.get(row["player_id"]), "rating_overall", "overall"),
                player_weight(row, lineup_map.get(row["player_id"]), injury_map.get(row["player_id"])),
            )
            for row in rows
        ],
        DEFAULTS["squad_quality"],
    )
    top_overalls = sorted(
        [
            float(value)
            for row in rows
            if (value := row_rating(row, rating_map.get(row["player_id"]), "rating_overall", "overall")) is not None
        ],
        reverse=True,
    )
    top_11 = average(top_overalls[:11], weighted_overall)
    next_10 = average(top_overalls[11:21], weighted_overall - 5.0)
    depth = clamp(0.68 * top_11 + 0.32 * next_10)

    return {
        "squad_quality": clamp(0.70 * weighted_overall + 0.30 * top_11),
        "attack_rating": clamp(
            weighted_average(
                [
                    (
                        row_rating(row, rating_map.get(row["player_id"]), "rating_attack", "attack"),
                        player_weight(row, lineup_map.get(row["player_id"]), injury_map.get(row["player_id"])),
                    )
                    for row in rows
                ],
                50.0,
            )
        ),
        "defense_rating": clamp(
            weighted_average(
                [
                    (
                        row_rating(row, rating_map.get(row["player_id"]), "rating_defense", "defense"),
                        player_weight(row, lineup_map.get(row["player_id"]), injury_map.get(row["player_id"])),
                    )
                    for row in rows
                ],
                50.0,
            )
        ),
        "possession_rating": clamp(
            weighted_average(
                [
                    (
                        row_rating(row, rating_map.get(row["player_id"]), "rating_possession", "possession"),
                        player_weight(row, lineup_map.get(row["player_id"]), injury_map.get(row["player_id"])),
                    )
                    for row in rows
                ],
                50.0,
            )
        ),
        "transition_rating": clamp(
            weighted_average(
                [
                    (
                        row_rating(row, rating_map.get(row["player_id"]), "rating_transition", "transition"),
                        player_weight(row, lineup_map.get(row["player_id"]), injury_map.get(row["player_id"])),
                    )
                    for row in rows
                ],
                50.0,
            )
        ),
        "set_piece_rating": clamp(
            weighted_average(
                [
                    (
                        row_rating(row, rating_map.get(row["player_id"]), "rating_set_piece", "set_piece"),
                        player_weight(row, lineup_map.get(row["player_id"]), injury_map.get(row["player_id"])),
                    )
                    for row in rows
                ],
                50.0,
            )
        ),
        "goalkeeper_rating": clamp(
            max(
                [
                    row_rating(row, rating_map.get(row["player_id"]), "rating_goalkeeping", "goalkeeping")
                    for row in rows
                    if row_rating(row, rating_map.get(row["player_id"]), "rating_goalkeeping", "goalkeeping")
                    is not None
                ]
                or [50.0]
            )
        ),
        "depth_rating": depth,
        "experience_rating": clamp(average([row["caps"] for row in rows if row["caps"] is not None], 35.0) / 80.0 * 100.0),
        "fitness_rating": clamp(
            weighted_average(
                [
                    (
                        row_rating(row, rating_map.get(row["player_id"]), "rating_fitness", "fitness"),
                        player_weight(row, lineup_map.get(row["player_id"]), injury_map.get(row["player_id"])),
                    )
                    for row in rows
                ],
                50.0,
            )
        ),
        "known_player_ratings": len(top_overalls),
        "known_provider_ratings": len(rating_map),
        "known_injuries": len(injury_map),
        "known_lineup_players": len(lineup_map),
        "known_player_features": feature_coverage["known_player_features"],
        "feature_sample_minutes": feature_coverage["feature_sample_minutes"],
        "lineup_type": lineup_type,
    }


def form_component(conn, team_id: str) -> float:
    rows = conn.execute(
        """
        SELECT goals_for, goals_against, xg_for, xg_against, elo_before, elo_after
        FROM team_results
        WHERE team_id = ?
        ORDER BY match_date DESC
        LIMIT 12
        """,
        (team_id,),
    ).fetchall()
    if not rows:
        return DEFAULTS["form_rating"]

    scored = average([row["xg_for"] if row["xg_for"] is not None else row["goals_for"] for row in rows], 1.25)
    conceded = average(
        [row["xg_against"] if row["xg_against"] is not None else row["goals_against"] for row in rows],
        1.25,
    )
    gd_component = 50.0 + (scored - conceded) * 18.0
    elo_deltas = [
        row["elo_after"] - row["elo_before"]
        for row in rows
        if row["elo_before"] is not None and row["elo_after"] is not None
    ]
    elo_component = 50.0 + average(elo_deltas, 0.0) * 1.5
    return clamp(0.75 * gd_component + 0.25 * elo_component)


def latest_style(conn, team_id: str) -> dict:
    row = conn.execute(
        """
        SELECT *
        FROM team_style_profiles
        WHERE team_id = ?
        ORDER BY
            profile_date DESC,
            CASE source_id
                WHEN 'club_feature_merge' THEN 0
                WHEN 'manual_enhancement_feed' THEN 1
                WHEN 'derived_team_features' THEN 2
                ELSE 3
            END
        LIMIT 1
        """,
        (team_id,),
    ).fetchone()
    return {} if row is None else dict(row)


def source_count(conn, team_id: str) -> int:
    tables = [
        ("teams", "team_id"),
        ("players", "team_id"),
        ("team_results", "team_id"),
        ("fifa_rankings", "team_id"),
        ("player_ratings", "team_id"),
        ("injuries", "team_id"),
        ("lineups", "team_id"),
        ("tactical_plans", "team_id"),
        ("team_style_profiles", "team_id"),
        ("player_feature_snapshots", "team_id"),
    ]
    source_ids = set()
    for table, team_column in tables:
        if not table_exists(conn, table):
            continue
        rows = conn.execute(
            f"SELECT DISTINCT source_id FROM {table} WHERE {team_column} = ? AND source_id IS NOT NULL",
            (team_id,),
        ).fetchall()
        source_ids.update(row["source_id"] for row in rows if row["source_id"])
    return len(source_ids)


def build(db_path: Path, rating_date: str) -> int:
    conn = connect(db_path)
    inserted = 0
    try:
        teams = conn.execute("SELECT * FROM teams ORDER BY name").fetchall()
        for team in teams:
            team_id = team["team_id"]
            player = player_components(conn, team_id)
            style = latest_style(conn, team_id)
            ranking = latest_fifa_ranking(conn, team_id)

            fifa_rank = ranking.get("rank", team["fifa_rank"])
            fifa_points = ranking.get("points", team["fifa_points"])
            fifa = fifa_component(fifa_rank, fifa_points)
            form = form_component(conn, team_id)
            injury_load = float(style.get("injury_load") or 0.0)
            if player["known_injuries"]:
                unavailable = conn.execute(
                    """
                    SELECT COUNT(*) AS c
                    FROM injuries i
                    JOIN (
                        SELECT player_id, MAX(verified_at) AS verified_at
                        FROM injuries
                        WHERE team_id = ?
                        GROUP BY player_id
                    ) latest
                      ON latest.player_id = i.player_id
                     AND latest.verified_at = i.verified_at
                    WHERE lower(coalesce(i.status, 'available')) IN ('out', 'suspended', 'withdrawn')
                    """,
                    (team_id,),
                ).fetchone()["c"]
                injury_load = max(injury_load, min(float(unavailable) * 8.0, 40.0))
            cohesion = float(style.get("cohesion") or 50.0)
            travel_fatigue = float(style.get("travel_fatigue") or 0.0)
            fitness = clamp(player["fitness_rating"] - injury_load * 0.30 - travel_fatigue * 0.20)
            coaching = clamp(0.55 * cohesion + 0.45 * 50.0)

            attack = clamp(0.78 * player["attack_rating"] + 0.22 * float(style.get("low_block_attack") or 50.0))
            defense = clamp(0.78 * player["defense_rating"] + 0.22 * float(style.get("low_block_defense") or 50.0))
            possession = clamp(0.72 * player["possession_rating"] + 0.28 * float(style.get("buildup_quality") or 50.0))
            transition = clamp(
                0.50 * player["transition_rating"]
                + 0.25 * float(style.get("transition_attack") or 50.0)
                + 0.25 * float(style.get("transition_defense") or 50.0)
            )
            set_piece = clamp(
                0.55 * player["set_piece_rating"]
                + 0.25 * float(style.get("set_piece_attack") or 50.0)
                + 0.20 * float(style.get("set_piece_defense") or 50.0)
            )
            keeper = clamp(
                0.70 * player["goalkeeper_rating"]
                + 0.20 * float(style.get("keeper_shot_stopping") or 50.0)
                + 0.10 * float(style.get("keeper_sweeper") or 50.0)
            )

            overall = clamp(
                0.12 * fifa
                + 0.18 * player["squad_quality"]
                + 0.16 * attack
                + 0.16 * defense
                + 0.08 * possession
                + 0.08 * transition
                + 0.06 * set_piece
                + 0.06 * keeper
                + 0.04 * player["depth_rating"]
                + 0.04 * form
                + 0.02 * fitness
            )
            source_total = source_count(conn, team_id)
            uncertainty = clamp(
                28.0
                - min(player["known_player_ratings"], 26) * 0.45
                - min(player["known_provider_ratings"], 18) * 0.18
                - min(player["known_player_features"], 11) * 0.12
                - min(player["known_lineup_players"], 11) * 0.35
                - (2.5 if ranking else 0.0)
                - (1.5 if player["known_injuries"] else 0.0)
                - min(source_total, 8) * 1.0
                + max(0.0, injury_load) * 0.08,
                5.0,
                35.0,
            )
            snapshot_id = f"strength-{team_id.lower()}-{rating_date}"
            conn.execute(
                """
                INSERT INTO team_strength_snapshots (
                    snapshot_id, team_id, rating_date, fifa_component, squad_quality,
                    attack_rating, defense_rating, possession_rating, transition_rating,
                    set_piece_rating, goalkeeper_rating, depth_rating, form_rating,
                    experience_rating, fitness_rating, coaching_rating, overall_rating,
                    uncertainty, model_version, source_count, notes
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(snapshot_id) DO UPDATE SET
                    fifa_component = excluded.fifa_component,
                    squad_quality = excluded.squad_quality,
                    attack_rating = excluded.attack_rating,
                    defense_rating = excluded.defense_rating,
                    possession_rating = excluded.possession_rating,
                    transition_rating = excluded.transition_rating,
                    set_piece_rating = excluded.set_piece_rating,
                    goalkeeper_rating = excluded.goalkeeper_rating,
                    depth_rating = excluded.depth_rating,
                    form_rating = excluded.form_rating,
                    experience_rating = excluded.experience_rating,
                    fitness_rating = excluded.fitness_rating,
                    coaching_rating = excluded.coaching_rating,
                    overall_rating = excluded.overall_rating,
                    uncertainty = excluded.uncertainty,
                    model_version = excluded.model_version,
                    source_count = excluded.source_count,
                    notes = excluded.notes
                """,
                (
                    snapshot_id,
                    team_id,
                    rating_date,
                    round(fifa, 2),
                    round(player["squad_quality"], 2),
                    round(attack, 2),
                    round(defense, 2),
                    round(possession, 2),
                    round(transition, 2),
                    round(set_piece, 2),
                    round(keeper, 2),
                    round(player["depth_rating"], 2),
                    round(form, 2),
                    round(player["experience_rating"], 2),
                    round(fitness, 2),
                    round(coaching, 2),
                    round(overall, 2),
                    round(uncertainty, 2),
                    MODEL_VERSION,
                    source_total,
                    (
                        "Generated by build_strength_table.py v0.2; "
                        f"ranking={'yes' if ranking else 'no'}, "
                        f"provider_player_ratings={player['known_provider_ratings']}, "
                        f"club_role_features={player['known_player_features']}, "
                        f"club_feature_minutes={player['feature_sample_minutes']:.0f}, "
                        f"injury_rows={player['known_injuries']}, "
                        f"lineup_players={player['known_lineup_players']}, "
                        f"lineup_type={player['lineup_type'] or 'none'}."
                    ),
                ),
            )
            inserted += 1
        conn.commit()
        return inserted
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, help="SQLite database path.")
    parser.add_argument("--rating-date", default=today_utc(), help="Snapshot date, YYYY-MM-DD.")
    args = parser.parse_args()

    count = build(Path(args.db), args.rating_date)
    print(f"Built {count} team strength snapshots for {args.rating_date}")


if __name__ == "__main__":
    main()
