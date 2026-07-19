#!/usr/bin/env python3
"""Backtest score predictions against completed fixtures or paired team results."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from common import MODEL_VERSION, connect, now_utc, slugify, table_exists
from predict_match import calibrate_score_distribution, predict, raw_score_grid


def ensure_backtest_columns(conn) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(backtest_runs)").fetchall()}
    additions = {
        "favorite_accuracy": "REAL",
        "avg_actual_outcome_probability": "REAL",
        "calibration_json": "TEXT",
    }
    for name, ddl_type in additions.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE backtest_runs ADD COLUMN {name} {ddl_type}")


def outcome(score_a: int, score_b: int) -> str:
    if score_a > score_b:
        return "team_a_win"
    if score_a < score_b:
        return "team_b_win"
    return "draw"


def brier(probabilities: dict, actual: str) -> float:
    return sum((float(probabilities[key]) - (1.0 if key == actual else 0.0)) ** 2 for key in probabilities)


def safe_log_loss(probability: float) -> float:
    return -math.log(max(min(probability, 1.0 - 1e-12), 1e-12))


def calibration_bucket(probability: float) -> str:
    lower = max(min(int(probability * 10) / 10, 0.9), 0.0)
    upper = lower + 0.1
    return f"{lower:.1f}-{upper:.1f}"


def published_scorelines(result: dict, max_goals: int = 10) -> list[dict]:
    """Mirror the calibrated score distribution used by the odds report."""
    rows = raw_score_grid(float(result["lambda_a"]), float(result["lambda_b"]), max_goals)
    target_wdl = (result.get("score_calibration") or {}).get("target_wdl")
    params = result.get("model_parameters") or {}
    if target_wdl and params:
        rows = calibrate_score_distribution(rows, target_wdl, params)
    return sorted(rows, key=lambda row: float(row["probability"]), reverse=True)


def completed_fixtures(conn, test_start: str | None, test_end: str | None, use_training_cache: bool = False) -> list[dict]:
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
        return [dict(row) for row in rows]
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
    return [{**dict(row), "sample_weight": 1.0} for row in rows]


def backtest(db_path: Path, test_start: str | None, test_end: str | None, stage: str, use_training_cache: bool) -> dict:
    conn = connect(db_path)
    run_at = now_utc()
    run_id = f"backtest-{slugify(run_at)}"
    rows = completed_fixtures(conn, test_start, test_end, use_training_cache)
    ensure_backtest_columns(conn)
    metrics = {
        "brier_score": 0.0,
        "log_loss": 0.0,
        "mae_goals": 0.0,
        "exact_score_accuracy": 0.0,
        "top8_score_hit_rate": 0.0,
        "favorite_accuracy": 0.0,
        "avg_actual_outcome_probability": 0.0,
    }
    calibration: dict[str, dict[str, float]] = {}
    stored = []
    total_weight = 0.0
    try:
        for row in rows:
            weight = max(float(row.get("sample_weight") or 1.0), 0.0)
            if weight <= 0:
                continue
            result = predict(
                db_path,
                row["team_a_id"],
                row["team_b_id"],
                row["stage"] or stage,
                True,
                False,
            )
            actual = outcome(int(row["score_a"]), int(row["score_b"]))
            probabilities = result["probabilities"]
            favorite_outcome, favorite_probability = max(probabilities.items(), key=lambda item: item[1])
            actual_probability = float(probabilities[actual])
            bucket = calibration_bucket(float(favorite_probability))
            calibration.setdefault(
                bucket,
                {"sample_size": 0.0, "avg_confidence": 0.0, "hit_rate": 0.0, "avg_actual_probability": 0.0},
            )
            calibration[bucket]["sample_size"] += weight
            calibration[bucket]["avg_confidence"] += float(favorite_probability) * weight
            calibration[bucket]["hit_rate"] += (1.0 if favorite_outcome == actual else 0.0) * weight
            calibration[bucket]["avg_actual_probability"] += actual_probability * weight
            top_scores = published_scorelines(result)
            actual_score = f"{row['score_a']}-{row['score_b']}"
            metrics["brier_score"] += brier(probabilities, actual) * weight
            metrics["log_loss"] += safe_log_loss(actual_probability) * weight
            metrics["mae_goals"] += (
                (abs(float(result["lambda_a"]) - row["score_a"]) + abs(float(result["lambda_b"]) - row["score_b"])) / 2
            ) * weight
            metrics["exact_score_accuracy"] += (1.0 if top_scores and top_scores[0]["score"] == actual_score else 0.0) * weight
            metrics["top8_score_hit_rate"] += (
                1.0 if any(item["score"] == actual_score for item in top_scores[:8]) else 0.0
            ) * weight
            metrics["favorite_accuracy"] += (1.0 if favorite_outcome == actual else 0.0) * weight
            metrics["avg_actual_outcome_probability"] += actual_probability * weight
            total_weight += weight
            stored.append((row, result, actual, actual_score, top_scores))

        sample_size = len(stored)
        if total_weight:
            for key in metrics:
                metrics[key] = metrics[key] / total_weight
        calibration_summary = {}
        for bucket, stats in sorted(calibration.items()):
            bucket_weight = float(stats["sample_size"])
            if bucket_weight:
                calibration_summary[bucket] = {
                    "sample_weight": round(bucket_weight, 3),
                    "avg_confidence": round(stats["avg_confidence"] / bucket_weight, 4),
                    "hit_rate": round(stats["hit_rate"] / bucket_weight, 4),
                    "avg_actual_outcome_probability": round(stats["avg_actual_probability"] / bucket_weight, 4),
                }

        conn.execute(
            """
            INSERT INTO backtest_runs (
                run_id, run_at, model_version, test_start, test_end, sample_size,
                brier_score, log_loss, mae_goals, exact_score_accuracy, top8_score_hit_rate,
                favorite_accuracy, avg_actual_outcome_probability, calibration_json, notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                run_at,
                MODEL_VERSION,
                test_start,
                test_end,
                sample_size,
                metrics["brier_score"],
                metrics["log_loss"],
                metrics["mae_goals"],
                metrics["exact_score_accuracy"],
                metrics["top8_score_hit_rate"],
                metrics["favorite_accuracy"],
                metrics["avg_actual_outcome_probability"],
                json.dumps(calibration_summary, ensure_ascii=True),
                (
                    "Backtest uses current available snapshots/enhancements; for strict historical testing use archived pre-match data. "
                    f"use_training_cache={use_training_cache}, total_sample_weight={total_weight:.3f}."
                ),
            ),
        )
        for row, result, actual, _actual_score, top_scores in stored:
            conn.execute(
                """
                INSERT INTO backtest_predictions (
                    backtest_prediction_id, run_id, match_id, predicted_at, team_a_id, team_b_id,
                    lambda_a, lambda_b, p_team_a_win, p_draw, p_team_b_win, top_scorelines_json,
                    actual_score_a, actual_score_b, actual_outcome, formation_a, formation_b, notes
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"btpred-{slugify(run_id)}-{slugify(row['match_id'])}",
                    run_id,
                    row["match_id"],
                    run_at,
                    row["team_a_id"],
                    row["team_b_id"],
                    result["lambda_a"],
                    result["lambda_b"],
                    result["probabilities"]["team_a_win"],
                    result["probabilities"]["draw"],
                    result["probabilities"]["team_b_win"],
                    json.dumps(top_scores[:8], ensure_ascii=True),
                    row["score_a"],
                    row["score_b"],
                    actual,
                    result["formation_matchup"]["formation_a"],
                    result["formation_matchup"]["formation_b"],
                    "Generated by backtest_model.py using the report-aligned calibrated score distribution.",
                ),
            )
        conn.commit()
        return {
            "run_id": run_id,
            "sample_size": sample_size,
            "total_sample_weight": round(total_weight, 3),
            **metrics,
            "calibration": calibration_summary,
        }
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, help="SQLite database path.")
    parser.add_argument("--test-start", help="Inclusive date filter, YYYY-MM-DD.")
    parser.add_argument("--test-end", help="Inclusive date filter, YYYY-MM-DD.")
    parser.add_argument("--stage", default="Group Stage", help="Fallback stage when fixture stage is empty.")
    parser.add_argument("--use-training-cache", action="store_true", help="Read weighted international rows from training_matches.")
    args = parser.parse_args()

    result = backtest(Path(args.db), args.test_start, args.test_end, args.stage, args.use_training_cache)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
