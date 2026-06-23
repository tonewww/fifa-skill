#!/usr/bin/env python3
"""Grid-search model parameters against completed fixtures."""

from __future__ import annotations

import argparse
import itertools
import json
import math
from pathlib import Path

from common import MODEL_VERSION, connect, now_utc, slugify, table_exists
from predict_match import (
    DEFAULT_PARAMETERS,
    calibrate_score_distribution,
    ensure_model_parameter_columns,
    predict,
    raw_score_grid,
)


GRIDS = {
    "smoke": {
        "base_goals": [1.18, 1.28],
        "attack_weight": [0.85],
        "overall_weight": [0.55],
        "formation_weight": [0.25],
        "recent_goal_openness_weight": [0.32],
        "formation_openness_weight": [0.45],
        "wdl_prior_weight": [0.70],
        "formation_wdl_prior_max_weight": [0.25],
        "wdl_score_calibration_weight": [0.80],
        "favorite_score_tilt": [0.08],
        "high_total_score_boost": [0.14, 0.24],
        "very_high_total_score_boost": [0.08, 0.16],
        "nil_nil_dampener": [0.12, 0.20],
        "one_goal_win_dampener": [0.04, 0.08],
        "stage_data_weight": [0.45],
        "stage_open_match_resistance": [0.30],
    },
    "coarse": {
        "base_goals": [1.12, 1.22, 1.32],
        "attack_weight": [0.70, 0.90],
        "overall_weight": [0.40, 0.60],
        "formation_weight": [0.10, 0.30],
        "recent_goal_openness_weight": [0.20, 0.34],
        "formation_openness_weight": [0.25, 0.45],
        "wdl_prior_weight": [0.45, 0.65, 0.80],
        "formation_wdl_prior_max_weight": [0.20, 0.35],
        "wdl_score_calibration_weight": [0.45, 0.70, 0.90],
        "favorite_score_tilt": [0.06, 0.14],
        "draw_score_tilt": [0.04, 0.10],
        "high_total_score_boost": [0.12, 0.22, 0.30],
        "very_high_total_score_boost": [0.06, 0.14, 0.22],
        "nil_nil_dampener": [0.10, 0.18, 0.26],
        "one_goal_win_dampener": [0.02, 0.06, 0.10],
        "stage_data_weight": [0.35, 0.65, 0.90],
        "stage_sample_half_life": [18.0, 36.0, 72.0],
        "stage_open_match_resistance": [0.20, 0.45, 0.70],
    },
    "full": {
        "base_goals": [1.10, 1.18, 1.26, 1.34],
        "attack_weight": [0.70, 0.90, 1.10],
        "overall_weight": [0.40, 0.60, 0.80],
        "formation_weight": [0.10, 0.30],
        "recent_goal_openness_weight": [0.18, 0.28, 0.38],
        "formation_openness_weight": [0.20, 0.38, 0.55],
        "wdl_prior_weight": [0.45, 0.65, 0.85],
        "formation_wdl_prior_max_weight": [0.20, 0.35],
        "wdl_score_calibration_weight": [0.45, 0.70, 0.90],
        "favorite_score_tilt": [0.06, 0.14],
        "draw_score_tilt": [0.04, 0.10],
        "high_total_score_boost": [0.10, 0.20, 0.30],
        "very_high_total_score_boost": [0.04, 0.12, 0.22],
        "nil_nil_dampener": [0.08, 0.18, 0.28],
        "one_goal_win_dampener": [0.02, 0.06, 0.12],
        "stage_data_weight": [0.30, 0.55, 0.80],
        "stage_sample_half_life": [18.0, 36.0, 72.0],
        "stage_open_match_resistance": [0.20, 0.45, 0.70],
    },
}


def outcome(score_a: int, score_b: int) -> str:
    if score_a > score_b:
        return "team_a_win"
    if score_a < score_b:
        return "team_b_win"
    return "draw"


def completed_fixtures(
    conn,
    test_start: str | None,
    test_end: str | None,
    max_matches: int | None = None,
    use_training_cache: bool = False,
) -> list[dict]:
    if use_training_cache and table_exists(conn, "training_matches"):
        filters = [
            "domain = 'international'",
            "team_a_id IS NOT NULL",
            "team_b_id IS NOT NULL",
            "score_a IS NOT NULL",
            "score_b IS NOT NULL",
        ]
        params: list[str] = []
        if test_start:
            filters.append("date(match_date) >= date(?)")
            params.append(test_start)
        if test_end:
            filters.append("date(match_date) <= date(?)")
            params.append(test_end)
        rows = conn.execute(
            f"""
            SELECT
                training_match_id AS match_id,
                match_date,
                stage,
                team_a_id,
                team_b_id,
                score_a,
                score_b,
                sample_weight
            FROM training_matches
            WHERE {' AND '.join(filters)}
            ORDER BY match_date, training_match_id
            """,
            params,
        ).fetchall()
        matches = [dict(row) for row in rows]
        if max_matches and max_matches > 0:
            matches = matches[-max_matches:]
        return matches
    filters = [
        "team_a_id IS NOT NULL",
        "team_b_id IS NOT NULL",
        "score_a IS NOT NULL",
        "score_b IS NOT NULL",
        "lower(coalesce(status, 'final')) = 'final'",
    ]
    params: list[str] = []
    if test_start:
        filters.append("date(match_date) >= date(?)")
        params.append(test_start)
    if test_end:
        filters.append("date(match_date) <= date(?)")
        params.append(test_end)
    rows = conn.execute(
        f"""
        SELECT *
        FROM fixtures
        WHERE {' AND '.join(filters)}
        ORDER BY match_date, match_id
        """,
        params,
    ).fetchall()
    matches = [{**dict(row), "sample_weight": 1.0} for row in rows]
    if max_matches and max_matches > 0:
        matches = matches[-max_matches:]
    return matches


def calibrated_score_rows(result: dict, params: dict, max_goals: int = 10) -> list[dict]:
    rows = raw_score_grid(float(result["lambda_a"]), float(result["lambda_b"]), max_goals)
    target_wdl = (result.get("score_calibration") or {}).get("target_wdl")
    if target_wdl:
        rows = calibrate_score_distribution(rows, target_wdl, params)
    return rows


def score_params(db_path: Path, matches: list[dict], params: dict) -> dict:
    log_loss = 0.0
    mae = 0.0
    score_log_loss = 0.0
    exact_score_accuracy = 0.0
    top8_score_hit_rate = 0.0
    avg_actual_score_probability = 0.0
    total_weight = 0.0
    for match in matches:
        weight = max(float(match.get("sample_weight") or 1.0), 0.0)
        if weight <= 0:
            continue
        result = predict(
            db_path,
            match["team_a_id"],
            match["team_b_id"],
            match["stage"] or "Group Stage",
            True,
            False,
            params,
        )
        actual = outcome(int(match["score_a"]), int(match["score_b"]))
        prob = max(min(float(result["probabilities"][actual]), 1.0 - 1e-12), 1e-12)
        log_loss += -math.log(prob) * weight
        mae += (
            (abs(float(result["lambda_a"]) - match["score_a"]) + abs(float(result["lambda_b"]) - match["score_b"])) / 2
        ) * weight
        actual_score = f"{int(match['score_a'])}-{int(match['score_b'])}"
        rows = calibrated_score_rows(result, params)
        by_score = {row["score"]: float(row["probability"]) for row in rows}
        actual_score_probability = max(min(by_score.get(actual_score, 0.0), 1.0 - 1e-12), 1e-12)
        ranked_scores = sorted(rows, key=lambda row: row["probability"], reverse=True)
        score_log_loss += -math.log(actual_score_probability) * weight
        avg_actual_score_probability += actual_score_probability * weight
        exact_score_accuracy += (1.0 if ranked_scores and ranked_scores[0]["score"] == actual_score else 0.0) * weight
        top8_score_hit_rate += (1.0 if any(row["score"] == actual_score for row in ranked_scores[:8]) else 0.0) * weight
        total_weight += weight

    sample_size = max(total_weight, 1.0)
    return {
        "log_loss": log_loss / sample_size,
        "mae_goals": mae / sample_size,
        "score_log_loss": score_log_loss / sample_size,
        "exact_score_accuracy": exact_score_accuracy / sample_size,
        "top8_score_hit_rate": top8_score_hit_rate / sample_size,
        "avg_actual_score_probability": avg_actual_score_probability / sample_size,
    }


def optimize(
    db_path: Path,
    test_start: str | None,
    test_end: str | None,
    write_best: bool,
    grid_name: str,
    max_matches: int | None,
    use_training_cache: bool,
) -> dict:
    conn = connect(db_path)
    try:
        matches = completed_fixtures(conn, test_start, test_end, max_matches, use_training_cache)
    finally:
        conn.close()
    if not matches:
        return {"sample_size": 0, "message": "No completed fixtures available for optimization."}

    best = None
    grid = GRIDS[grid_name]
    keys = list(grid)
    grid_size = math.prod(len(grid[key]) for key in keys)
    for values in itertools.product(*(grid[key] for key in keys)):
        params = DEFAULT_PARAMETERS.copy()
        params.update(dict(zip(keys, values)))
        metrics = score_params(db_path, matches, params)
        objective = (
            metrics["log_loss"]
            + 0.12 * metrics["mae_goals"]
            + 0.08 * metrics["score_log_loss"]
            - 0.10 * metrics["top8_score_hit_rate"]
            - 0.05 * metrics["avg_actual_score_probability"]
        )
        candidate = {"objective": objective, "params": params, "metrics": metrics}
        if best is None or objective < best["objective"]:
            best = candidate

    assert best is not None
    if write_best:
        conn = connect(db_path)
        ensure_model_parameter_columns(conn)
        parameter_id = f"params-{MODEL_VERSION}-{slugify(now_utc())}"
        params = best["params"]
        try:
            columns = ["parameter_id", "model_version", "as_of_date", *DEFAULT_PARAMETERS.keys(), "notes"]
            placeholders = ", ".join("?" for _ in columns)
            notes = (
                "Selected by optimize_model_parameters.py grid search with WDL-to-score calibration "
                "and stage goal multipliers. "
                f"log_loss={best['metrics']['log_loss']:.4f}, "
                f"score_log_loss={best['metrics']['score_log_loss']:.4f}, "
                f"top8_score_hit_rate={best['metrics']['top8_score_hit_rate']:.4f}, "
                f"mae_goals={best['metrics']['mae_goals']:.4f}, "
                f"use_training_cache={use_training_cache}."
            )
            conn.execute(
                f"INSERT INTO model_parameters ({', '.join(columns)}) VALUES ({placeholders})",
                [
                    parameter_id,
                    MODEL_VERSION,
                    now_utc(),
                    *[params[key] for key in DEFAULT_PARAMETERS],
                    notes,
                ],
            )
            conn.commit()
            best["parameter_id"] = parameter_id
        finally:
            conn.close()

    return {
        "sample_size": len(matches),
        "total_sample_weight": round(sum(float(match.get("sample_weight") or 1.0) for match in matches), 3),
        "grid": grid_name,
        "grid_size": grid_size,
        **best,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, help="SQLite database path.")
    parser.add_argument("--test-start", help="Inclusive date filter, YYYY-MM-DD.")
    parser.add_argument("--test-end", help="Inclusive date filter, YYYY-MM-DD.")
    parser.add_argument("--grid", choices=sorted(GRIDS), default="coarse", help="Parameter grid size preset.")
    parser.add_argument(
        "--max-matches",
        type=int,
        help="Use only the most recent N completed fixtures in the selected date window.",
    )
    parser.add_argument("--write-best", action="store_true", help="Persist best parameter row.")
    parser.add_argument("--use-training-cache", action="store_true", help="Read weighted international rows from training_matches.")
    args = parser.parse_args()

    result = optimize(
        Path(args.db),
        args.test_start,
        args.test_end,
        args.write_best,
        args.grid,
        args.max_matches,
        args.use_training_cache,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
