#!/usr/bin/env python3
"""Backtest score predictions against completed fixtures or paired team results."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from common import MODEL_VERSION, connect, now_utc, slugify
from predict_match import predict


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


def completed_fixtures(conn, test_start: str | None, test_end: str | None) -> list[dict]:
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
    return [dict(row) for row in rows]


def backtest(db_path: Path, test_start: str | None, test_end: str | None, stage: str) -> dict:
    conn = connect(db_path)
    run_at = now_utc()
    run_id = f"backtest-{slugify(run_at)}"
    rows = completed_fixtures(conn, test_start, test_end)
    metrics = {
        "brier_score": 0.0,
        "log_loss": 0.0,
        "mae_goals": 0.0,
        "exact_score_accuracy": 0.0,
        "top8_score_hit_rate": 0.0,
    }
    stored = []
    try:
        for row in rows:
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
            top_scores = result["top_scorelines"]
            actual_score = f"{row['score_a']}-{row['score_b']}"
            metrics["brier_score"] += brier(probabilities, actual)
            metrics["log_loss"] += safe_log_loss(probabilities[actual])
            metrics["mae_goals"] += (abs(float(result["lambda_a"]) - row["score_a"]) + abs(float(result["lambda_b"]) - row["score_b"])) / 2
            metrics["exact_score_accuracy"] += 1.0 if top_scores and top_scores[0]["score"] == actual_score else 0.0
            metrics["top8_score_hit_rate"] += 1.0 if any(item["score"] == actual_score for item in top_scores) else 0.0
            stored.append((row, result, actual, actual_score))

        sample_size = len(stored)
        if sample_size:
            for key in metrics:
                metrics[key] = metrics[key] / sample_size

        conn.execute(
            """
            INSERT INTO backtest_runs (
                run_id, run_at, model_version, test_start, test_end, sample_size,
                brier_score, log_loss, mae_goals, exact_score_accuracy, top8_score_hit_rate, notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                "Backtest uses current available snapshots/enhancements; for strict historical testing use archived pre-match data.",
            ),
        )
        for row, result, actual, _actual_score in stored:
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
                    json.dumps(result["top_scorelines"], ensure_ascii=True),
                    row["score_a"],
                    row["score_b"],
                    actual,
                    result["formation_matchup"]["formation_a"],
                    result["formation_matchup"]["formation_b"],
                    "Generated by backtest_model.py.",
                ),
            )
        conn.commit()
        return {"run_id": run_id, "sample_size": sample_size, **metrics}
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, help="SQLite database path.")
    parser.add_argument("--test-start", help="Inclusive date filter, YYYY-MM-DD.")
    parser.add_argument("--test-end", help="Inclusive date filter, YYYY-MM-DD.")
    parser.add_argument("--stage", default="Group Stage", help="Fallback stage when fixture stage is empty.")
    args = parser.parse_args()

    result = backtest(Path(args.db), args.test_start, args.test_end, args.stage)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
