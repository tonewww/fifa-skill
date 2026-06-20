#!/usr/bin/env python3
"""Shared helpers for the 2026 World Cup score prediction skill."""

from __future__ import annotations

import datetime as dt
import re
import sqlite3
from pathlib import Path
from typing import Any


MODEL_VERSION = "wc2026-skill-v0.3"


TABLE_COLUMNS: dict[str, list[str]] = {
    "sources": [
        "source_id",
        "name",
        "url",
        "category",
        "publisher",
        "retrieved_at",
        "published_at",
        "freshness_days",
        "license_note",
        "reliability",
        "notes",
    ],
    "teams": [
        "team_id",
        "fifa_code",
        "name",
        "short_name",
        "confederation",
        "group_name",
        "is_host",
        "qualification_method",
        "seed_pot",
        "coach",
        "fifa_rank",
        "fifa_points",
        "squad_status",
        "last_verified_at",
        "source_id",
        "notes",
    ],
    "players": [
        "player_id",
        "team_id",
        "fifa_code",
        "name",
        "display_name",
        "shirt_number",
        "position",
        "club",
        "league",
        "birth_date",
        "age",
        "caps",
        "goals",
        "dominant_foot",
        "height_cm",
        "market_value_eur",
        "rating_overall",
        "rating_attack",
        "rating_defense",
        "rating_possession",
        "rating_transition",
        "rating_set_piece",
        "rating_goalkeeping",
        "rating_fitness",
        "status",
        "minutes_expected",
        "last_verified_at",
        "source_id",
        "notes",
    ],
    "fixtures": [
        "match_id",
        "competition",
        "stage",
        "group_name",
        "match_date",
        "venue",
        "city",
        "country",
        "team_a_id",
        "team_b_id",
        "score_a",
        "score_b",
        "status",
        "source_id",
        "notes",
    ],
    "team_results": [
        "result_id",
        "match_date",
        "team_id",
        "opponent_team_id",
        "venue_type",
        "competition",
        "is_neutral",
        "goals_for",
        "goals_against",
        "xg_for",
        "xg_against",
        "shots",
        "shots_on_target",
        "possession",
        "elo_before",
        "elo_after",
        "source_id",
        "notes",
    ],
    "fifa_rankings": [
        "ranking_id",
        "ranking_date",
        "ranking_type",
        "team_id",
        "fifa_code",
        "rank",
        "points",
        "previous_rank",
        "previous_points",
        "rank_change",
        "confederation",
        "source_id",
        "notes",
    ],
    "player_ratings": [
        "rating_id",
        "player_id",
        "team_id",
        "provider",
        "rating_date",
        "overall",
        "attack",
        "defense",
        "possession",
        "transition",
        "set_piece",
        "goalkeeping",
        "fitness",
        "market_value_eur",
        "minutes_recent",
        "source_id",
        "notes",
    ],
    "injuries": [
        "injury_id",
        "player_id",
        "team_id",
        "status",
        "severity",
        "injury_type",
        "expected_return",
        "availability_pct",
        "impact_rating",
        "verified_at",
        "source_id",
        "notes",
    ],
    "lineups": [
        "lineup_id",
        "match_id",
        "team_id",
        "opponent_team_id",
        "lineup_type",
        "as_of",
        "formation",
        "source_id",
        "notes",
    ],
    "lineup_players": [
        "lineup_player_id",
        "lineup_id",
        "player_id",
        "role",
        "position",
        "is_starter",
        "minutes_expected",
        "availability_pct",
        "source_id",
        "notes",
    ],
    "tactical_plans": [
        "plan_id",
        "team_id",
        "opponent_team_id",
        "as_of_date",
        "formation",
        "defensive_shape",
        "pressing_trigger",
        "buildup_pattern",
        "chance_creation",
        "transition_plan",
        "set_piece_plan",
        "risk_level",
        "source_id",
        "notes",
    ],
    "formation_matchup_stats": [
        "matchup_id",
        "formation_a",
        "formation_b",
        "sample_size",
        "p_a_win",
        "p_draw",
        "p_b_win",
        "avg_goals_a",
        "avg_goals_b",
        "scoreline_json",
        "source_id",
        "notes",
    ],
    "model_parameters": [
        "parameter_id",
        "model_version",
        "as_of_date",
        "base_goals",
        "home_edge",
        "knockout_drag",
        "attack_weight",
        "overall_weight",
        "keeper_weight",
        "set_piece_weight",
        "fitness_weight",
        "style_weight",
        "formation_weight",
        "notes",
    ],
    "backtest_runs": [
        "run_id",
        "run_at",
        "model_version",
        "train_start",
        "train_end",
        "test_start",
        "test_end",
        "sample_size",
        "brier_score",
        "log_loss",
        "mae_goals",
        "exact_score_accuracy",
        "top8_score_hit_rate",
        "notes",
    ],
    "backtest_predictions": [
        "backtest_prediction_id",
        "run_id",
        "match_id",
        "predicted_at",
        "team_a_id",
        "team_b_id",
        "lambda_a",
        "lambda_b",
        "p_team_a_win",
        "p_draw",
        "p_team_b_win",
        "top_scorelines_json",
        "actual_score_a",
        "actual_score_b",
        "actual_outcome",
        "formation_a",
        "formation_b",
        "notes",
    ],
    "team_style_profiles": [
        "profile_id",
        "team_id",
        "profile_date",
        "formation_primary",
        "tempo",
        "press_intensity",
        "defensive_line",
        "buildup_quality",
        "transition_attack",
        "transition_defense",
        "wing_play",
        "central_progression",
        "set_piece_attack",
        "set_piece_defense",
        "aerial_strength",
        "low_block_attack",
        "low_block_defense",
        "keeper_sweeper",
        "keeper_shot_stopping",
        "injury_load",
        "cohesion",
        "travel_fatigue",
        "source_id",
        "notes",
    ],
    "team_strength_snapshots": [
        "snapshot_id",
        "team_id",
        "rating_date",
        "fifa_component",
        "squad_quality",
        "attack_rating",
        "defense_rating",
        "possession_rating",
        "transition_rating",
        "set_piece_rating",
        "goalkeeper_rating",
        "depth_rating",
        "form_rating",
        "experience_rating",
        "fitness_rating",
        "coaching_rating",
        "overall_rating",
        "uncertainty",
        "model_version",
        "source_count",
        "notes",
    ],
    "matchup_adjustments": [
        "adjustment_id",
        "as_of_date",
        "team_a_id",
        "team_b_id",
        "affected_team_id",
        "category",
        "goal_delta",
        "win_prob_delta",
        "confidence",
        "rationale",
        "source_id",
        "notes",
    ],
    "predictions": [
        "prediction_id",
        "predicted_at",
        "team_a_id",
        "team_b_id",
        "stage",
        "neutral_site",
        "lambda_a",
        "lambda_b",
        "p_team_a_win",
        "p_draw",
        "p_team_b_win",
        "top_scorelines_json",
        "model_version",
        "data_cutoff",
        "notes",
    ],
}


INTEGER_COLUMNS = {
    "is_host",
    "seed_pot",
    "fifa_rank",
    "shirt_number",
    "age",
    "caps",
    "goals",
    "height_cm",
    "is_neutral",
    "score_a",
    "score_b",
    "goals_for",
    "goals_against",
    "shots",
    "shots_on_target",
    "rank",
    "previous_rank",
    "rank_change",
    "is_starter",
    "sample_size",
    "actual_score_a",
    "actual_score_b",
    "source_count",
    "freshness_days",
}


REAL_COLUMNS = {
    "fifa_points",
    "market_value_eur",
    "rating_overall",
    "rating_attack",
    "rating_defense",
    "rating_possession",
    "rating_transition",
    "rating_set_piece",
    "rating_goalkeeping",
    "rating_fitness",
    "minutes_expected",
    "xg_for",
    "xg_against",
    "possession",
    "elo_before",
    "elo_after",
    "points",
    "previous_points",
    "overall",
    "attack",
    "defense",
    "transition",
    "set_piece",
    "goalkeeping",
    "fitness",
    "minutes_recent",
    "availability_pct",
    "impact_rating",
    "risk_level",
    "p_a_win",
    "p_b_win",
    "avg_goals_a",
    "avg_goals_b",
    "base_goals",
    "home_edge",
    "knockout_drag",
    "attack_weight",
    "overall_weight",
    "keeper_weight",
    "set_piece_weight",
    "fitness_weight",
    "style_weight",
    "formation_weight",
    "brier_score",
    "log_loss",
    "mae_goals",
    "exact_score_accuracy",
    "top8_score_hit_rate",
    "tempo",
    "press_intensity",
    "defensive_line",
    "buildup_quality",
    "transition_attack",
    "transition_defense",
    "wing_play",
    "central_progression",
    "set_piece_attack",
    "set_piece_defense",
    "aerial_strength",
    "low_block_attack",
    "low_block_defense",
    "keeper_sweeper",
    "keeper_shot_stopping",
    "injury_load",
    "cohesion",
    "travel_fatigue",
    "fifa_component",
    "squad_quality",
    "attack_rating",
    "defense_rating",
    "possession_rating",
    "transition_rating",
    "set_piece_rating",
    "goalkeeper_rating",
    "depth_rating",
    "form_rating",
    "experience_rating",
    "fitness_rating",
    "coaching_rating",
    "overall_rating",
    "uncertainty",
    "goal_delta",
    "win_prob_delta",
    "confidence",
    "lambda_a",
    "lambda_b",
    "p_team_a_win",
    "p_draw",
    "p_team_b_win",
}


def connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(Path(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def ensure_parent(path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def now_utc() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()


def today_utc() -> str:
    return dt.date.today().isoformat()


def slugify(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-") or "unknown"


def coerce_value(column: str, value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        if value == "":
            return None
    if column in INTEGER_COLUMNS:
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None
    if column in REAL_COLUMNS:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    return value


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def average(values: list[float], default: float = 50.0) -> float:
    clean = [float(v) for v in values if v is not None]
    if not clean:
        return default
    return sum(clean) / len(clean)


def weighted_average(values: list[tuple[float | None, float | None]], default: float = 50.0) -> float:
    total = 0.0
    weight = 0.0
    for value, raw_weight in values:
        if value is None:
            continue
        w = 1.0 if raw_weight is None else max(float(raw_weight), 0.0)
        total += float(value) * w
        weight += w
    if weight <= 0:
        return default
    return total / weight


def find_team_id(conn: sqlite3.Connection, query: str) -> str | None:
    key = query.strip()
    row = conn.execute(
        """
        SELECT team_id
        FROM teams
        WHERE upper(team_id) = upper(?)
           OR upper(fifa_code) = upper(?)
           OR lower(name) = lower(?)
           OR lower(short_name) = lower(?)
        LIMIT 1
        """,
        (key, key, key, key),
    ).fetchone()
    return None if row is None else str(row["team_id"])


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None
