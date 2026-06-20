#!/usr/bin/env python3
"""Apply latest enhancement rows to baseline team/player fields."""

from __future__ import annotations

import argparse
from pathlib import Path

from common import clamp, connect, now_utc


INACTIVE_STATUSES = {"out", "suspended", "withdrawn"}
LIMITED_STATUSES = {"doubtful", "limited", "questionable"}


def latest_player_ratings(conn) -> int:
    rows = conn.execute(
        """
        SELECT pr.*
        FROM player_ratings pr
        JOIN (
            SELECT player_id, provider, MAX(rating_date) AS rating_date
            FROM player_ratings
            WHERE player_id IS NOT NULL
            GROUP BY player_id, provider
        ) latest
          ON latest.player_id = pr.player_id
         AND latest.provider = pr.provider
         AND latest.rating_date = pr.rating_date
        ORDER BY pr.player_id, pr.rating_date DESC
        """
    ).fetchall()
    applied = 0
    seen: set[str] = set()
    for row in rows:
        player_id = row["player_id"]
        if not player_id or player_id in seen:
            continue
        seen.add(player_id)
        conn.execute(
            """
            UPDATE players
            SET rating_overall = COALESCE(?, rating_overall),
                rating_attack = COALESCE(?, rating_attack),
                rating_defense = COALESCE(?, rating_defense),
                rating_possession = COALESCE(?, rating_possession),
                rating_transition = COALESCE(?, rating_transition),
                rating_set_piece = COALESCE(?, rating_set_piece),
                rating_goalkeeping = COALESCE(?, rating_goalkeeping),
                rating_fitness = COALESCE(?, rating_fitness),
                market_value_eur = COALESCE(?, market_value_eur),
                last_verified_at = ?
            WHERE player_id = ?
            """,
            (
                row["overall"],
                row["attack"],
                row["defense"],
                row["possession"],
                row["transition"],
                row["set_piece"],
                row["goalkeeping"],
                row["fitness"],
                row["market_value_eur"],
                now_utc(),
                player_id,
            ),
        )
        applied += 1
    return applied


def latest_injuries(conn) -> int:
    rows = conn.execute(
        """
        SELECT i.*
        FROM injuries i
        JOIN (
            SELECT player_id, MAX(verified_at) AS verified_at
            FROM injuries
            WHERE player_id IS NOT NULL
            GROUP BY player_id
        ) latest
          ON latest.player_id = i.player_id
         AND latest.verified_at = i.verified_at
        """
    ).fetchall()
    applied = 0
    for row in rows:
        status = (row["status"] or "available").strip().lower()
        availability = row["availability_pct"]
        if availability is None:
            if status in INACTIVE_STATUSES:
                availability = 0.0
            elif status in LIMITED_STATUSES:
                availability = 55.0
            else:
                availability = 100.0
        current = conn.execute(
            "SELECT rating_fitness, minutes_expected FROM players WHERE player_id = ?",
            (row["player_id"],),
        ).fetchone()
        if current is None:
            continue
        current_fitness = 50.0 if current["rating_fitness"] is None else float(current["rating_fitness"])
        current_minutes = 25.0 if current["minutes_expected"] is None else float(current["minutes_expected"])
        fitness = clamp(min(current_fitness, float(availability)))
        minutes = 0.0 if status in INACTIVE_STATUSES else current_minutes * clamp(float(availability), 0, 100) / 100.0
        conn.execute(
            """
            UPDATE players
            SET status = ?,
                rating_fitness = ?,
                minutes_expected = ?,
                last_verified_at = ?
            WHERE player_id = ?
            """,
            (status, round(fitness, 2), round(minutes, 2), row["verified_at"] or now_utc(), row["player_id"]),
        )
        applied += 1
    return applied


def latest_lineups(conn, match_id: str | None, opponent_team_id: str | None) -> int:
    filters = []
    params: list[str] = []
    if match_id:
        filters.append("match_id = ?")
        params.append(match_id)
    if opponent_team_id:
        filters.append("opponent_team_id = ?")
        params.append(opponent_team_id)
    where = " AND ".join(filters)
    if where:
        where = "WHERE " + where
    rows = conn.execute(
        f"""
        SELECT *
        FROM lineups
        {where}
        ORDER BY
            CASE lower(coalesce(lineup_type, 'expected'))
                WHEN 'official' THEN 0
                WHEN 'confirmed' THEN 1
                WHEN 'expected' THEN 2
                ELSE 3
            END,
            as_of DESC
        """,
        params,
    ).fetchall()
    latest_by_team: dict[str, str] = {}
    for row in rows:
        latest_by_team.setdefault(row["team_id"], row["lineup_id"])

    applied = 0
    for lineup_id in latest_by_team.values():
        players = conn.execute(
            """
            SELECT lp.*
            FROM lineup_players lp
            WHERE lp.lineup_id = ?
            """,
            (lineup_id,),
        ).fetchall()
        for row in players:
            availability = 100.0 if row["availability_pct"] is None else float(row["availability_pct"])
            minutes = row["minutes_expected"]
            if minutes is None:
                minutes = 75.0 if row["is_starter"] else 20.0
            conn.execute(
                """
                UPDATE players
                SET minutes_expected = ?,
                    status = CASE
                        WHEN ? <= 0 THEN 'out'
                        WHEN ? < 60 THEN 'limited'
                        ELSE status
                    END,
                    last_verified_at = ?
                WHERE player_id = ?
                """,
                (
                    round(float(minutes) * clamp(availability, 0, 100) / 100.0, 2),
                    availability,
                    availability,
                    now_utc(),
                    row["player_id"],
                ),
            )
            applied += 1
    return applied


def latest_tactical_plans(conn) -> int:
    rows = conn.execute(
        """
        SELECT tp.*
        FROM tactical_plans tp
        JOIN (
            SELECT team_id, COALESCE(opponent_team_id, '') AS opponent_key, MAX(as_of_date) AS as_of_date
            FROM tactical_plans
            GROUP BY team_id, COALESCE(opponent_team_id, '')
        ) latest
          ON latest.team_id = tp.team_id
         AND latest.opponent_key = COALESCE(tp.opponent_team_id, '')
         AND latest.as_of_date = tp.as_of_date
        WHERE tp.formation IS NOT NULL
        """
    ).fetchall()
    applied = 0
    for row in rows:
        style = conn.execute(
            """
            SELECT profile_id
            FROM team_style_profiles
            WHERE team_id = ?
            ORDER BY profile_date DESC
            LIMIT 1
            """,
            (row["team_id"],),
        ).fetchone()
        if style is None:
            profile_id = f"style-{row['team_id'].lower()}-{row['as_of_date']}"
            conn.execute(
                """
                INSERT INTO team_style_profiles (
                    profile_id, team_id, profile_date, formation_primary, tempo,
                    press_intensity, defensive_line, buildup_quality, transition_attack,
                    transition_defense, wing_play, central_progression, set_piece_attack,
                    set_piece_defense, aerial_strength, low_block_attack, low_block_defense,
                    keeper_sweeper, keeper_shot_stopping, injury_load, cohesion,
                    travel_fatigue, source_id, notes
                )
                VALUES (?, ?, ?, ?, 50, 50, 50, 50, 50, 50, 50, 50, 50, 50, 50, 50, 50, 50, 50, 0, 50, 0, ?, ?)
                """,
                (
                    profile_id,
                    row["team_id"],
                    row["as_of_date"],
                    row["formation"],
                    row["source_id"],
                    "Created from tactical_plans by apply_enhancements.py; numeric style values are neutral defaults.",
                ),
            )
        else:
            conn.execute(
                """
                UPDATE team_style_profiles
                SET formation_primary = COALESCE(?, formation_primary),
                    source_id = COALESCE(source_id, ?)
                WHERE profile_id = ?
                """,
                (row["formation"], row["source_id"], style["profile_id"]),
            )
        applied += 1
    return applied


def apply(db_path: Path, match_id: str | None, opponent_team_id: str | None) -> dict[str, int]:
    conn = connect(db_path)
    try:
        counts = {
            "player_ratings": latest_player_ratings(conn),
            "injuries": latest_injuries(conn),
            "lineup_players": latest_lineups(conn, match_id, opponent_team_id),
            "tactical_plans": latest_tactical_plans(conn),
        }
        conn.commit()
        return counts
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, help="SQLite database path.")
    parser.add_argument("--match-id", help="Only apply latest lineups for a specific fixture.")
    parser.add_argument("--opponent-team-id", help="Only apply latest lineups for a specific opponent.")
    args = parser.parse_args()

    counts = apply(Path(args.db), args.match_id, args.opponent_team_id)
    for table, count in counts.items():
        print(f"Applied {count} rows from {table}")


if __name__ == "__main__":
    main()
