#!/usr/bin/env python3
"""Grid-search model parameters against completed fixtures."""

from __future__ import annotations

import argparse
import itertools
import json
import math
from pathlib import Path

from common import MODEL_VERSION, connect, now_utc, slugify
from predict_match import DEFAULT_PARAMETERS, predict


GRIDS = {
    "smoke": {
        "base_goals": [1.18, 1.28],
        "attack_weight": [0.75, 0.95],
        "overall_weight": [0.45, 0.65],
        "formation_weight": [0.15, 0.35],
    },
    "coarse": {
        "base_goals": [1.12, 1.22, 1.32],
        "attack_weight": [0.70, 0.90],
        "overall_weight": [0.40, 0.60],
        "formation_weight": [0.10, 0.30],
    },
    "full": {
        "base_goals": [1.10, 1.18, 1.26, 1.34],
        "attack_weight": [0.65, 0.80, 0.95, 1.10],
        "overall_weight": [0.35, 0.50, 0.65, 0.80],
        "formation_weight": [0.05, 0.20, 0.35, 0.50],
    },
}

GRID = {
    "base_goals": [1.12, 1.22, 1.32],
    "attack_weight": [0.70, 0.85, 1.00],
    "overall_weight": [0.40, 0.55, 0.70],
    "formation_weight": [0.10, 0.25, 0.40],
}


def outcome(score_a: int, score_b: int) -> str:
    if score_a > score_b:
        return "team_a_win"
    if score_a < score_b:
        return "team_b_win"
    return "draw"


def completed_fixtures(conn, test_start: str | None, test_end: str | None, max_matches: int | None = None) -> list[dict]:
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
    matches = [dict(row) for row in rows]
    if max_matches and max_matches > 0:
        matches = matches[-max_matches:]
    return matches


def score_params(db_path: Path, matches: list[dict], params: dict) -> dict:
    log_loss = 0.0
    mae = 0.0
    for match in matches:
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
        log_loss += -math.log(prob)
        mae += (abs(float(result["lambda_a"]) - match["score_a"]) + abs(float(result["lambda_b"]) - match["score_b"])) / 2

    sample_size = max(len(matches), 1)
    return {"log_loss": log_loss / sample_size, "mae_goals": mae / sample_size}


def optimize(
    db_path: Path,
    test_start: str | None,
    test_end: str | None,
    write_best: bool,
    grid_name: str,
    max_matches: int | None,
) -> dict:
    conn = connect(db_path)
    try:
        matches = completed_fixtures(conn, test_start, test_end, max_matches)
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
        objective = metrics["log_loss"] + 0.15 * metrics["mae_goals"]
        candidate = {"objective": objective, "params": params, "metrics": metrics}
        if best is None or objective < best["objective"]:
            best = candidate

    assert best is not None
    if write_best:
        conn = connect(db_path)
        parameter_id = f"params-{MODEL_VERSION}-{slugify(now_utc())}"
        params = best["params"]
        try:
            conn.execute(
                """
                INSERT INTO model_parameters (
                    parameter_id, model_version, as_of_date, base_goals, home_edge, knockout_drag,
                    attack_weight, overall_weight, keeper_weight, set_piece_weight, fitness_weight,
                    style_weight, formation_weight, notes
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    parameter_id,
                    MODEL_VERSION,
                    now_utc(),
                    params["base_goals"],
                    params["home_edge"],
                    params["knockout_drag"],
                    params["attack_weight"],
                    params["overall_weight"],
                    params["keeper_weight"],
                    params["set_piece_weight"],
                    params["fitness_weight"],
                    params["style_weight"],
                    params["formation_weight"],
                    (
                        "Selected by optimize_model_parameters.py grid search. "
                        f"log_loss={best['metrics']['log_loss']:.4f}, mae_goals={best['metrics']['mae_goals']:.4f}."
                    ),
                ),
            )
            conn.commit()
            best["parameter_id"] = parameter_id
        finally:
            conn.close()

    return {"sample_size": len(matches), "grid": grid_name, "grid_size": grid_size, **best}


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
    args = parser.parse_args()

    result = optimize(Path(args.db), args.test_start, args.test_end, args.write_best, args.grid, args.max_matches)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
