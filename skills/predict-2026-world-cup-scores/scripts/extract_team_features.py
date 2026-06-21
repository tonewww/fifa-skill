#!/usr/bin/env python3
"""Derive team tactical/style features from squad and recent-result data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from common import average, clamp, connect, today_utc, weighted_average


SOURCE_ID = "derived_team_features"


def competition_weight(competition: str | None) -> float:
    text = (competition or "").lower()
    if "world cup" in text and "qualification" not in text:
        return 1.35
    if "euro" in text or "copa am" in text or "african cup" in text or "gold cup" in text:
        return 1.20
    if "qualification" in text or "nations league" in text:
        return 1.10
    if "friendly" in text:
        return 0.75
    return 1.0


def position_bucket(position: str | None) -> str:
    pos = (position or "").upper()
    if "GK" in pos:
        return "GK"
    if "DF" in pos or pos in {"CB", "LB", "RB", "WB"}:
        return "DF"
    if "MF" in pos or pos in {"CM", "DM", "AM"}:
        return "MF"
    if "FW" in pos or pos in {"ST", "CF", "LW", "RW"}:
        return "FW"
    return "MF"


def player_national_position(player: dict) -> str | None:
    return player.get("national_team_position") or player.get("position")


def player_weight(row: dict) -> float:
    status = str(row.get("status") or "available").lower()
    if status in {"out", "suspended", "withdrawn"}:
        return 0.0
    minutes = row.get("minutes_expected")
    if minutes is None:
        minutes = 25.0
    return max(float(minutes), 0.0)


def rating(row: dict, key: str, default: float = 50.0) -> float:
    value = row.get(key)
    return default if value is None else float(value)


def weighted_position_average(players: list[dict], bucket: str, key: str, default: float = 50.0) -> float:
    values = [
        (rating(player, key, default), player_weight(player))
        for player in players
        if position_bucket(player_national_position(player)) == bucket
    ]
    return weighted_average(values, default)


def top_average(players: list[dict], key: str, limit: int, default: float = 50.0) -> float:
    values = sorted([rating(player, key, default) for player in players if player.get(key) is not None], reverse=True)
    return average(values[:limit], default)


def weighted_player_feature(players: list[dict], key: str, default: float = 50.0) -> float:
    values = []
    for player in players:
        value = player.get(key)
        if value is None:
            continue
        source_weight = player.get("feature_source_weight")
        if source_weight is None:
            source_weight = 0.18
        values.append((float(value), player_weight(player) * max(float(source_weight), 0.0)))
    return weighted_average(values, default)


def metric(row: dict, key: str, fallback_key: str | None = None) -> float | None:
    value = row.get(key)
    if value is None and fallback_key:
        value = row.get(fallback_key)
    return None if value is None else float(value)


def weighted_metric(rows: list[dict], key: str, default: float, fallback_key: str | None = None) -> float:
    values = []
    for index, row in enumerate(rows):
        value = metric(row, key, fallback_key)
        if value is None:
            continue
        recency_weight = 1.0 / (1.0 + index * 0.08)
        values.append((value, recency_weight * competition_weight(row.get("competition"))))
    return weighted_average(values, default)


def recent_result_features(conn, team_id: str, limit: int) -> dict:
    rows = conn.execute(
        """
        SELECT *
        FROM team_results
        WHERE team_id = ?
        ORDER BY match_date DESC
        LIMIT ?
        """,
        (team_id, limit),
    ).fetchall()
    rows = [dict(row) for row in rows]
    if not rows:
        return {
            "sample": 0,
            "goals_for": 1.25,
            "goals_against": 1.25,
            "xg_for": 1.25,
            "xg_against": 1.25,
            "goal_diff": 0.0,
            "xg_diff": 0.0,
            "shots": 11.0,
            "shots_on_target": 4.0,
            "possession": 50.0,
            "clean_sheet_rate": 0.25,
            "failed_score_rate": 0.25,
            "competitive_weight": 1.0,
        }
    goals_for = weighted_metric(rows, "goals_for", 1.25)
    goals_against = weighted_metric(rows, "goals_against", 1.25)
    xg_for = weighted_metric(rows, "xg_for", goals_for, "goals_for")
    xg_against = weighted_metric(rows, "xg_against", goals_against, "goals_against")
    shots = weighted_metric(rows, "shots", 11.0)
    shots_on_target = weighted_metric(rows, "shots_on_target", 4.0)
    possession = weighted_metric(rows, "possession", 50.0)
    total_weight = sum(competition_weight(row.get("competition")) for row in rows) or len(rows)
    return {
        "sample": len(rows),
        "goals_for": goals_for,
        "goals_against": goals_against,
        "xg_for": xg_for,
        "xg_against": xg_against,
        "goal_diff": goals_for - goals_against,
        "xg_diff": xg_for - xg_against,
        "shots": shots,
        "shots_on_target": shots_on_target,
        "possession": possession,
        "clean_sheet_rate": sum(1 for row in rows if int(row["goals_against"] or 0) == 0) / len(rows),
        "failed_score_rate": sum(1 for row in rows if int(row["goals_for"] or 0) == 0) / len(rows),
        "competitive_weight": total_weight / len(rows),
    }


def infer_formation(players: list[dict]) -> str:
    outfield = [player for player in players if position_bucket(player_national_position(player)) != "GK"]
    total_weight = sum(player_weight(player) for player in outfield) or 1.0
    shares = {
        bucket: sum(player_weight(player) for player in outfield if position_bucket(player_national_position(player)) == bucket)
        / total_weight
        for bucket in ("DF", "MF", "FW")
    }
    if shares["DF"] >= 0.45 and shares["FW"] >= 0.20:
        return "5-3-2"
    if shares["DF"] >= 0.43:
        return "3-4-2-1"
    if shares["FW"] >= 0.30 and shares["MF"] >= 0.35:
        return "4-3-3"
    if shares["FW"] >= 0.27:
        return "4-2-3-1"
    if shares["MF"] >= 0.48:
        return "4-3-2-1"
    return "4-2-3-1"


def tactical_text(features: dict) -> dict:
    press = features["press_intensity"]
    buildup = features["buildup_quality"]
    transition = features["transition_attack"]
    wing = features["wing_play"]
    central = features["central_progression"]
    set_piece = features["set_piece_attack"]
    defensive_line = features["defensive_line"]
    low_block_defense = features["low_block_defense"]

    defensive_shape = "high press" if press >= 66 else "mid-block"
    if low_block_defense >= 66 and defensive_line < 55:
        defensive_shape = "compact low-to-mid block"
    pressing_trigger = "press loose first touches and backward passes" if press >= 62 else "press selectively after wide passes"
    buildup_pattern = "short buildup through midfield and goalkeeper support" if buildup >= 64 else "mixed buildup with earlier direct outlets"
    chance_creation = "wide overloads and cutbacks" if wing >= central else "central combinations and half-space entries"
    transition_plan = "fast direct counters" if transition >= 66 else "controlled transition with rest-defense priority"
    set_piece_plan = "actively target aerial/set-piece mismatches" if set_piece >= 64 else "standard set-piece routines"
    return {
        "defensive_shape": defensive_shape,
        "pressing_trigger": pressing_trigger,
        "buildup_pattern": buildup_pattern,
        "chance_creation": chance_creation,
        "transition_plan": transition_plan,
        "set_piece_plan": set_piece_plan,
    }


def derive_features(conn, team: dict, profile_date: str, recent_limit: int) -> dict:
    players = [
        dict(row)
        for row in conn.execute(
            """
            SELECT *
            FROM players
            WHERE team_id = ?
            """,
            (team["team_id"],),
        ).fetchall()
    ]
    recent = recent_result_features(conn, team["team_id"], recent_limit)
    if not players:
        players = []

    formation = infer_formation(players)
    avg_age = weighted_average([(player.get("age"), player_weight(player)) for player in players], 27.0)
    avg_caps = weighted_average([(player.get("caps"), player_weight(player)) for player in players], 25.0)
    avg_height = weighted_average([(player.get("height_cm"), player_weight(player)) for player in players], 181.0)
    top_attack = top_average(players, "rating_attack", 6, 50.0)
    top_transition = top_average(players, "rating_transition", 8, 50.0)
    top_possession = top_average(players, "rating_possession", 8, 50.0)
    top_defense = top_average(players, "rating_defense", 8, 50.0)
    feature_pressing = weighted_player_feature(players, "feature_pressing", top_transition)
    feature_progression = weighted_player_feature(players, "feature_progression", top_possession)
    feature_box_presence = weighted_player_feature(players, "feature_box_presence", top_attack)
    feature_shot_quality = weighted_player_feature(players, "feature_shot_quality", top_attack)
    feature_key_passing = weighted_player_feature(players, "feature_key_passing", top_possession)
    feature_duel_activity = weighted_player_feature(players, "feature_duel_activity", top_defense)
    feature_defensive_activity = weighted_player_feature(players, "feature_defensive_activity", top_defense)

    fw_attack = weighted_position_average(players, "FW", "rating_attack", top_attack)
    mf_possession = weighted_position_average(players, "MF", "rating_possession", top_possession)
    mf_transition = weighted_position_average(players, "MF", "rating_transition", top_transition)
    df_defense = weighted_position_average(players, "DF", "rating_defense", top_defense)
    df_set_piece = weighted_position_average(players, "DF", "rating_set_piece", 50.0)
    gk_goalkeeping = weighted_position_average(players, "GK", "rating_goalkeeping", 50.0)
    gk_transition = weighted_position_average(players, "GK", "rating_transition", 50.0)

    chance_diff = 0.65 * recent["xg_diff"] + 0.35 * recent["goal_diff"]
    goal_diff_signal = clamp(50.0 + chance_diff * 8.0, 35.0, 70.0)
    chance_volume_signal = clamp(50.0 + (recent["shots"] - 11.0) * 1.4 + (recent["shots_on_target"] - 4.0) * 2.0, 35.0, 72.0)
    possession_signal = clamp(50.0 + (recent["possession"] - 50.0) * 0.55, 35.0, 72.0)
    competitive_signal = clamp(50.0 + (recent["competitive_weight"] - 1.0) * 10.0, 42.0, 58.0)
    clean_sheet_signal = 50.0 + recent["clean_sheet_rate"] * 18.0
    failed_score_penalty = recent["failed_score_rate"] * 10.0

    features = {
        "formation_primary": formation,
        "tempo": clamp(
            0.33 * top_transition
            + 0.18 * mf_possession
            + 0.15 * goal_diff_signal
            + 0.14 * chance_volume_signal
            + 0.20 * ((feature_pressing + feature_progression) / 2.0)
        ),
        "press_intensity": clamp(
            0.38 * mf_transition
            + 0.18 * fw_attack
            + 0.14 * top_defense
            + 0.10 * competitive_signal
            + 0.20 * feature_pressing
        ),
        "defensive_line": clamp(47.0 + (top_defense - 50.0) * 0.20 + (gk_transition - 50.0) * 0.18 + (top_transition - 50.0) * 0.12),
        "buildup_quality": clamp(
            0.38 * mf_possession
            + 0.18 * gk_transition
            + 0.14 * top_possession
            + 0.12 * possession_signal
            + 0.18 * feature_progression
        ),
        "transition_attack": clamp(
            0.32 * top_transition
            + 0.26 * fw_attack
            + 0.15 * goal_diff_signal
            + 0.09 * chance_volume_signal
            + 0.18 * feature_progression
        ),
        "transition_defense": clamp(0.45 * df_defense + 0.20 * mf_transition + 0.17 * clean_sheet_signal + 0.18 * feature_defensive_activity),
        "wing_play": clamp(
            0.37 * top_transition
            + 0.21 * fw_attack
            + 0.14 * mf_possession
            + 0.10 * chance_volume_signal
            + 0.18 * feature_box_presence
        ),
        "central_progression": clamp(
            0.39 * mf_possession
            + 0.17 * top_attack
            + 0.12 * top_transition
            + 0.11 * possession_signal
            + 0.13 * feature_progression
            + 0.08 * feature_key_passing
        ),
        "set_piece_attack": clamp(0.50 * df_set_piece + 0.25 * (avg_height - 170.0) + 0.25 * fw_attack),
        "set_piece_defense": clamp(0.48 * df_defense + 0.25 * (avg_height - 170.0) + 0.27 * clean_sheet_signal),
        "aerial_strength": clamp(42.0 + (avg_height - 178.0) * 1.8 + (df_set_piece - 50.0) * 0.35),
        "low_block_attack": clamp(
            0.32 * top_attack
            + 0.24 * mf_possession
            + 0.14 * goal_diff_signal
            + 0.10 * chance_volume_signal
            + 0.11 * feature_box_presence
            + 0.09 * feature_shot_quality
            - failed_score_penalty
        ),
        "low_block_defense": clamp(0.48 * df_defense + 0.18 * gk_goalkeeping + 0.16 * clean_sheet_signal + 0.11 * feature_defensive_activity + 0.07 * feature_duel_activity),
        "keeper_sweeper": clamp(0.65 * gk_transition + 0.35 * gk_goalkeeping),
        "keeper_shot_stopping": clamp(gk_goalkeeping),
        "injury_load": 0.0,
        "cohesion": clamp(45.0 + min(avg_caps, 80.0) * 0.35 - abs(avg_age - 28.0) * 0.45 + min(recent["sample"], 12) * 0.6),
        "travel_fatigue": 0.0,
        "risk_level": clamp(50.0 + (top_attack - df_defense) * 0.22 + (recent["xg_for"] + recent["xg_against"] - 2.5) * 4.0 + (recent["shots"] - 11.0) * 0.35),
        "recent_sample": recent["sample"],
        "recent_xg_diff": recent["xg_diff"],
        "recent_chance_volume": chance_volume_signal,
        "club_feature_pressing": feature_pressing,
        "club_feature_progression": feature_progression,
        "club_feature_box_presence": feature_box_presence,
        "club_feature_shot_quality": feature_shot_quality,
        "club_feature_key_passing": feature_key_passing,
        "club_feature_duel_activity": feature_duel_activity,
        "club_feature_defensive_activity": feature_defensive_activity,
        "avg_age": avg_age,
        "avg_caps": avg_caps,
        "avg_height": avg_height,
    }
    return features


def upsert_source(conn) -> None:
    conn.execute(
        """
        INSERT INTO sources (
            source_id, name, category, publisher, retrieved_at,
            freshness_days, reliability, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_id) DO UPDATE SET
            retrieved_at = excluded.retrieved_at,
            notes = excluded.notes
        """,
        (
            SOURCE_ID,
            "Derived team feature extraction",
            "derived_features",
            "Local model",
            today_utc(),
            7,
            "model-derived",
            "Generated from squad ratings, player demographics, and imported recent team_results.",
        ),
    )


def write_profile(conn, team_id: str, profile_date: str, features: dict) -> None:
    profile_id = f"features-{team_id.lower()}-{profile_date}"
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
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(profile_id) DO UPDATE SET
            formation_primary = excluded.formation_primary,
            tempo = excluded.tempo,
            press_intensity = excluded.press_intensity,
            defensive_line = excluded.defensive_line,
            buildup_quality = excluded.buildup_quality,
            transition_attack = excluded.transition_attack,
            transition_defense = excluded.transition_defense,
            wing_play = excluded.wing_play,
            central_progression = excluded.central_progression,
            set_piece_attack = excluded.set_piece_attack,
            set_piece_defense = excluded.set_piece_defense,
            aerial_strength = excluded.aerial_strength,
            low_block_attack = excluded.low_block_attack,
            low_block_defense = excluded.low_block_defense,
            keeper_sweeper = excluded.keeper_sweeper,
            keeper_shot_stopping = excluded.keeper_shot_stopping,
            injury_load = excluded.injury_load,
            cohesion = excluded.cohesion,
            travel_fatigue = excluded.travel_fatigue,
            source_id = excluded.source_id,
            notes = excluded.notes
        """,
        (
            profile_id,
            team_id,
            profile_date,
            features["formation_primary"],
            round(features["tempo"], 2),
            round(features["press_intensity"], 2),
            round(features["defensive_line"], 2),
            round(features["buildup_quality"], 2),
            round(features["transition_attack"], 2),
            round(features["transition_defense"], 2),
            round(features["wing_play"], 2),
            round(features["central_progression"], 2),
            round(features["set_piece_attack"], 2),
            round(features["set_piece_defense"], 2),
            round(features["aerial_strength"], 2),
            round(features["low_block_attack"], 2),
            round(features["low_block_defense"], 2),
            round(features["keeper_sweeper"], 2),
            round(features["keeper_shot_stopping"], 2),
            round(features["injury_load"], 2),
            round(features["cohesion"], 2),
            round(features["travel_fatigue"], 2),
            SOURCE_ID,
            (
                "Derived from player ratings/demographics and recent team_results; "
                f"recent_sample={features['recent_sample']}, avg_age={features['avg_age']:.1f}, "
                f"avg_caps={features['avg_caps']:.1f}, avg_height={features['avg_height']:.1f}, "
                f"xg_diff={features['recent_xg_diff']:.2f}, chance_volume={features['recent_chance_volume']:.1f}, "
                f"club_role_features=pressing:{features['club_feature_pressing']:.1f}/"
                f"progression:{features['club_feature_progression']:.1f}/"
                f"box:{features['club_feature_box_presence']:.1f}/"
                f"shot_quality:{features['club_feature_shot_quality']:.1f}/"
                f"key_passing:{features['club_feature_key_passing']:.1f}/"
                f"duels:{features['club_feature_duel_activity']:.1f}/"
                f"def_actions:{features['club_feature_defensive_activity']:.1f}."
            ),
        ),
    )


def write_plan(conn, team_id: str, profile_date: str, features: dict) -> None:
    text = tactical_text(features)
    plan_id = f"derived-plan-{team_id.lower()}-{profile_date}"
    conn.execute(
        """
        INSERT INTO tactical_plans (
            plan_id, team_id, opponent_team_id, as_of_date, formation,
            defensive_shape, pressing_trigger, buildup_pattern, chance_creation,
            transition_plan, set_piece_plan, risk_level, source_id, notes
        )
        VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(plan_id) DO UPDATE SET
            formation = excluded.formation,
            defensive_shape = excluded.defensive_shape,
            pressing_trigger = excluded.pressing_trigger,
            buildup_pattern = excluded.buildup_pattern,
            chance_creation = excluded.chance_creation,
            transition_plan = excluded.transition_plan,
            set_piece_plan = excluded.set_piece_plan,
            risk_level = excluded.risk_level,
            source_id = excluded.source_id,
            notes = excluded.notes
        """,
        (
            plan_id,
            team_id,
            profile_date,
            features["formation_primary"],
            text["defensive_shape"],
            text["pressing_trigger"],
            text["buildup_pattern"],
            text["chance_creation"],
            text["transition_plan"],
            text["set_piece_plan"],
            round(features["risk_level"], 2),
            SOURCE_ID,
            "Generic derived tactical plan; replace with opponent-specific or official lineup data when available.",
        ),
    )


def extract(db_path: Path, profile_date: str, recent_limit: int, write_plans: bool) -> dict:
    conn = connect(db_path)
    summaries = []
    try:
        upsert_source(conn)
        teams = [dict(row) for row in conn.execute("SELECT * FROM teams ORDER BY team_id").fetchall()]
        for team in teams:
            features = derive_features(conn, team, profile_date, recent_limit)
            write_profile(conn, team["team_id"], profile_date, features)
            if write_plans:
                write_plan(conn, team["team_id"], profile_date, features)
            summaries.append(
                {
                    "team_id": team["team_id"],
                    "formation": features["formation_primary"],
                    "tempo": round(features["tempo"], 1),
                    "press": round(features["press_intensity"], 1),
                    "buildup": round(features["buildup_quality"], 1),
                    "transition_attack": round(features["transition_attack"], 1),
                    "risk": round(features["risk_level"], 1),
                    "recent_sample": features["recent_sample"],
                }
            )
        conn.commit()
    finally:
        conn.close()
    return {
        "profile_date": profile_date,
        "teams_updated": len(summaries),
        "plans_updated": len(summaries) if write_plans else 0,
        "sample": summaries[:12],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, help="SQLite database path.")
    parser.add_argument("--profile-date", default=today_utc(), help="Profile date, YYYY-MM-DD.")
    parser.add_argument("--recent-limit", type=int, default=12, help="Recent team_results rows used per team.")
    parser.add_argument("--no-plans", action="store_true", help="Do not write generic tactical_plans.")
    args = parser.parse_args()

    result = extract(Path(args.db), args.profile_date, args.recent_limit, not args.no_plans)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
