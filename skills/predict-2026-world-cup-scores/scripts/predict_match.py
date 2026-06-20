#!/usr/bin/env python3
"""Predict win/draw/loss probabilities and likely scorelines for two teams."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from common import MODEL_VERSION, clamp, connect, find_team_id, now_utc, table_exists, today_utc


DEFAULT_PARAMETERS = {
    "base_goals": 1.22,
    "home_edge": 0.12,
    "knockout_drag": -0.07,
    "attack_weight": 0.85,
    "overall_weight": 0.55,
    "keeper_weight": 0.25,
    "set_piece_weight": 0.18,
    "fitness_weight": 0.12,
    "style_weight": 1.0,
    "formation_weight": 0.25,
}


def latest_strength(conn, team_id: str) -> dict:
    row = conn.execute(
        """
        SELECT *
        FROM team_strength_snapshots
        WHERE team_id = ?
        ORDER BY rating_date DESC
        LIMIT 1
        """,
        (team_id,),
    ).fetchone()
    if row is None:
        raise SystemExit(f"No strength snapshot for {team_id}. Run build_strength_table.py first.")
    return dict(row)


def latest_style(conn, team_id: str) -> dict:
    row = conn.execute(
        """
        SELECT *
        FROM team_style_profiles
        WHERE team_id = ?
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
    return {} if row is None else dict(row)


def latest_model_parameters(conn) -> dict:
    params = DEFAULT_PARAMETERS.copy()
    params["parameter_id"] = "defaults"
    if not table_exists(conn, "model_parameters"):
        return params
    row = conn.execute(
        """
        SELECT *
        FROM model_parameters
        ORDER BY as_of_date DESC, parameter_id DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return params
    for key in DEFAULT_PARAMETERS:
        if row[key] is not None:
            params[key] = float(row[key])
    params["parameter_id"] = row["parameter_id"]
    return params


def latest_fifa_ranking(conn, team_id: str) -> dict:
    if table_exists(conn, "fifa_rankings"):
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
        if row is not None:
            return dict(row)
    team = conn.execute("SELECT fifa_rank, fifa_points FROM teams WHERE team_id = ?", (team_id,)).fetchone()
    if team and team["fifa_rank"] is not None:
        return {
            "ranking_date": None,
            "ranking_type": "teams",
            "rank": team["fifa_rank"],
            "points": team["fifa_points"],
        }
    return {}


def latest_lineup(conn, team_id: str, opponent_team_id: str | None = None) -> dict:
    if not table_exists(conn, "lineups"):
        return {}
    params: list[str] = [team_id]
    opponent_clause = ""
    if opponent_team_id:
        opponent_clause = "AND (opponent_team_id = ? OR opponent_team_id IS NULL)"
        params.append(opponent_team_id)
    row = conn.execute(
        f"""
        SELECT *
        FROM lineups
        WHERE team_id = ?
          {opponent_clause}
        ORDER BY
            CASE WHEN opponent_team_id = ? THEN 0 ELSE 1 END,
            CASE lower(coalesce(lineup_type, 'expected'))
                WHEN 'official' THEN 0
                WHEN 'confirmed' THEN 1
                WHEN 'expected' THEN 2
                ELSE 3
            END,
            as_of DESC
        LIMIT 1
        """,
        params + [opponent_team_id or ""],
    ).fetchone()
    if row is None:
        return {}
    lineup = dict(row)
    if table_exists(conn, "lineup_players"):
        player_rows = conn.execute(
            """
            SELECT lp.*, p.name, p.position AS base_position, p.rating_overall
            FROM lineup_players lp
            JOIN players p ON p.player_id = lp.player_id
            WHERE lp.lineup_id = ?
            ORDER BY lp.is_starter DESC, lp.minutes_expected DESC, p.name
            """,
            (lineup["lineup_id"],),
        ).fetchall()
        lineup["players"] = [dict(player) for player in player_rows]
    else:
        lineup["players"] = []
    return lineup


def injury_summary(conn, team_id: str) -> dict:
    summary = {"count": 0, "major": [], "availability_known": 0}
    if not table_exists(conn, "injuries"):
        return summary
    rows = conn.execute(
        """
        SELECT i.*, p.name
        FROM injuries i
        LEFT JOIN players p ON p.player_id = i.player_id
        JOIN (
            SELECT player_id, MAX(verified_at) AS verified_at
            FROM injuries
            WHERE team_id = ?
               OR player_id IN (SELECT player_id FROM players WHERE team_id = ?)
            GROUP BY player_id
        ) latest
          ON latest.player_id = i.player_id
         AND latest.verified_at = i.verified_at
        ORDER BY COALESCE(i.impact_rating, 0) DESC, p.name
        """,
        (team_id, team_id),
    ).fetchall()
    summary["count"] = len(rows)
    summary["availability_known"] = len([row for row in rows if row["availability_pct"] is not None])
    for row in rows[:5]:
        status = row["status"] or "unknown"
        impact = row["impact_rating"]
        if str(status).lower() in {"out", "suspended", "withdrawn", "doubtful", "limited"} or (
            impact is not None and float(impact) >= 60
        ):
            summary["major"].append(
                {
                    "player": row["name"] or row["player_id"],
                    "status": status,
                    "availability_pct": row["availability_pct"],
                    "impact_rating": impact,
                }
            )
    return summary


def latest_tactical_plan(conn, team_id: str, opponent_team_id: str) -> dict:
    if not table_exists(conn, "tactical_plans"):
        return {}
    row = conn.execute(
        """
        SELECT *
        FROM tactical_plans
        WHERE team_id = ?
          AND (opponent_team_id = ? OR opponent_team_id IS NULL)
        ORDER BY
            CASE WHEN opponent_team_id = ? THEN 0 ELSE 1 END,
            as_of_date DESC
        LIMIT 1
        """,
        (team_id, opponent_team_id, opponent_team_id),
    ).fetchone()
    return {} if row is None else dict(row)


def formation_from_context(style: dict, lineup: dict, plan: dict) -> str | None:
    return (
        plan.get("formation")
        or lineup.get("formation")
        or style.get("formation_primary")
        or None
    )


def formation_matchup(conn, formation_a: str | None, formation_b: str | None) -> dict:
    if not formation_a or not formation_b or not table_exists(conn, "formation_matchup_stats"):
        return {}
    row = conn.execute(
        """
        SELECT *, 0 AS reversed
        FROM formation_matchup_stats
        WHERE lower(formation_a) = lower(?)
          AND lower(formation_b) = lower(?)
        UNION ALL
        SELECT *, 1 AS reversed
        FROM formation_matchup_stats
        WHERE lower(formation_a) = lower(?)
          AND lower(formation_b) = lower(?)
        LIMIT 1
        """,
        (formation_a, formation_b, formation_b, formation_a),
    ).fetchone()
    if row is None:
        return {}
    data = dict(row)
    if data.pop("reversed"):
        data = {
            **data,
            "formation_a": formation_a,
            "formation_b": formation_b,
            "p_a_win": row["p_b_win"],
            "p_draw": row["p_draw"],
            "p_b_win": row["p_a_win"],
            "avg_goals_a": row["avg_goals_b"],
            "avg_goals_b": row["avg_goals_a"],
            "notes": f"Reversed from stored {row['formation_a']} vs {row['formation_b']}. {row['notes'] or ''}".strip(),
        }
    return data


def formation_goal_delta(stats: dict, weight: float) -> tuple[float, float, str | None]:
    if not stats:
        return 0.0, 0.0, None
    sample_size = int(stats.get("sample_size") or 0)
    confidence = min(sample_size / 80.0, 1.0)
    if sample_size < 5:
        confidence *= 0.25
    avg_edge = float(stats.get("avg_goals_a") or 0.0) - float(stats.get("avg_goals_b") or 0.0)
    prob_edge = float(stats.get("p_a_win") or 0.0) - float(stats.get("p_b_win") or 0.0)
    edge = clamp((0.08 * avg_edge + 0.16 * prob_edge) * weight * confidence, -0.16, 0.16)
    note = (
        f"{stats.get('formation_a')} vs {stats.get('formation_b')}: sample {sample_size}, "
        f"W/D/L {float(stats.get('p_a_win') or 0):.2f}/"
        f"{float(stats.get('p_draw') or 0):.2f}/"
        f"{float(stats.get('p_b_win') or 0):.2f}"
    )
    return edge, -edge, note


def tactical_goal_delta(plan: dict) -> float:
    if not plan:
        return 0.0
    risk = plan.get("risk_level")
    if risk is None:
        return 0.0
    return clamp((float(risk) - 50.0) / 100.0 * 0.08, -0.06, 0.08)


def data_readiness(conn, team_id: str) -> dict:
    checks = {
        "fifa_ranking": bool(latest_fifa_ranking(conn, team_id)),
        "recent_results": conn.execute("SELECT COUNT(*) AS c FROM team_results WHERE team_id = ?", (team_id,)).fetchone()["c"],
        "xg_results": conn.execute(
            "SELECT COUNT(*) AS c FROM team_results WHERE team_id = ? AND xg_for IS NOT NULL AND xg_against IS NOT NULL",
            (team_id,),
        ).fetchone()["c"],
        "player_ratings": 0,
        "injuries": 0,
        "lineup": False,
        "tactical_plan": False,
    }
    if table_exists(conn, "player_ratings"):
        checks["player_ratings"] = conn.execute(
            """
            SELECT COUNT(DISTINCT player_id) AS c
            FROM player_ratings
            WHERE team_id = ?
               OR player_id IN (SELECT player_id FROM players WHERE team_id = ?)
            """,
            (team_id, team_id),
        ).fetchone()["c"]
    if table_exists(conn, "injuries"):
        checks["injuries"] = conn.execute(
            """
            SELECT COUNT(DISTINCT player_id) AS c
            FROM injuries
            WHERE team_id = ?
               OR player_id IN (SELECT player_id FROM players WHERE team_id = ?)
            """,
            (team_id, team_id),
        ).fetchone()["c"]
    if table_exists(conn, "lineups"):
        checks["lineup"] = bool(conn.execute("SELECT 1 FROM lineups WHERE team_id = ? LIMIT 1", (team_id,)).fetchone())
    if table_exists(conn, "tactical_plans"):
        checks["tactical_plan"] = bool(
            conn.execute("SELECT 1 FROM tactical_plans WHERE team_id = ? LIMIT 1", (team_id,)).fetchone()
        )
    return checks


def manual_adjustments(conn, team_a_id: str, team_b_id: str) -> tuple[float, float, list[str]]:
    rows = conn.execute(
        """
        SELECT *
        FROM matchup_adjustments
        WHERE ((team_a_id = ? AND team_b_id = ?) OR (team_a_id = ? AND team_b_id = ?))
          AND affected_team_id IN (?, ?)
        ORDER BY as_of_date DESC
        """,
        (team_a_id, team_b_id, team_b_id, team_a_id, team_a_id, team_b_id),
    ).fetchall()
    delta_a = 0.0
    delta_b = 0.0
    notes = []
    for row in rows:
        effect = float(row["goal_delta"] or 0.0) * float(row["confidence"] or 0.5)
        if row["affected_team_id"] == team_a_id:
            delta_a += effect
        elif row["affected_team_id"] == team_b_id:
            delta_b += effect
        if row["rationale"]:
            notes.append(f"{row['category']}: {row['rationale']}")
    return delta_a, delta_b, notes[:6]


def style_value(style: dict, key: str, default: float = 50.0) -> float:
    value = style.get(key)
    try:
        return float(default if value is None else value)
    except (TypeError, ValueError):
        return default


def matchup_style_delta(attacker_style: dict, defender_style: dict) -> tuple[float, list[str]]:
    """Return a goal delta from style counters, roughly bounded to +/- 0.25."""
    notes: list[str] = []
    delta = 0.0

    transition_edge = (
        style_value(attacker_style, "transition_attack")
        - 0.5 * style_value(defender_style, "transition_defense")
        - 0.5 * (100.0 - style_value(defender_style, "defensive_line"))
    )
    if transition_edge > 18:
        delta += 0.08
        notes.append("transition pace can punish the opponent's defensive spacing")
    elif transition_edge < -18:
        delta -= 0.05
        notes.append("transition routes are likely to be contained")

    press_edge = style_value(attacker_style, "press_intensity") - style_value(defender_style, "buildup_quality")
    if press_edge > 20:
        delta += 0.06
        notes.append("pressing may disrupt buildup")
    elif press_edge < -20:
        delta -= 0.04
        notes.append("opponent buildup quality can bypass pressure")

    set_piece_edge = (
        0.6 * style_value(attacker_style, "set_piece_attack")
        + 0.4 * style_value(attacker_style, "aerial_strength")
        - 0.7 * style_value(defender_style, "set_piece_defense")
        - 0.3 * style_value(defender_style, "aerial_strength")
    )
    if set_piece_edge > 15:
        delta += 0.06
        notes.append("set-piece and aerial profile creates extra threat")
    elif set_piece_edge < -15:
        delta -= 0.04
        notes.append("set-piece threat is muted by defensive profile")

    block_edge = style_value(attacker_style, "low_block_attack") - style_value(defender_style, "low_block_defense")
    if block_edge > 18:
        delta += 0.05
        notes.append("chance creation against a settled block is a plus")
    elif block_edge < -18:
        delta -= 0.05
        notes.append("settled defense may suppress chance quality")

    wing_edge = style_value(attacker_style, "wing_play") - style_value(defender_style, "aerial_strength")
    if wing_edge > 22:
        delta += 0.03
        notes.append("wide attacks can create volume, but finishing quality still matters")

    return clamp(delta, -0.25, 0.25), notes[:4]


def poisson(k: int, lam: float) -> float:
    return math.exp(-lam) * (lam**k) / math.factorial(k)


def score_distribution(lambda_a: float, lambda_b: float, max_goals: int = 7) -> tuple[float, float, float, list[dict]]:
    scores = []
    p_a = p_d = p_b = 0.0
    for goals_a in range(max_goals + 1):
        for goals_b in range(max_goals + 1):
            prob = poisson(goals_a, lambda_a) * poisson(goals_b, lambda_b)
            if goals_a > goals_b:
                p_a += prob
            elif goals_a == goals_b:
                p_d += prob
            else:
                p_b += prob
            scores.append({"score": f"{goals_a}-{goals_b}", "probability": prob})
    total = p_a + p_d + p_b
    if total > 0:
        p_a, p_d, p_b = p_a / total, p_d / total, p_b / total
        for score in scores:
            score["probability"] = score["probability"] / total
    top = sorted(scores, key=lambda item: item["probability"], reverse=True)[:8]
    for item in top:
        item["probability"] = round(item["probability"], 4)
    return p_a, p_d, p_b, top


def predict(
    db_path: Path,
    team_a_query: str,
    team_b_query: str,
    stage: str,
    neutral_site: bool,
    save: bool,
    parameter_overrides: dict | None = None,
) -> dict:
    conn = connect(db_path)
    try:
        team_a_id = find_team_id(conn, team_a_query)
        team_b_id = find_team_id(conn, team_b_query)
        if not team_a_id:
            raise SystemExit(f"Unknown team: {team_a_query}")
        if not team_b_id:
            raise SystemExit(f"Unknown team: {team_b_query}")
        if team_a_id == team_b_id:
            raise SystemExit("Choose two different teams.")

        team_a = conn.execute("SELECT * FROM teams WHERE team_id = ?", (team_a_id,)).fetchone()
        team_b = conn.execute("SELECT * FROM teams WHERE team_id = ?", (team_b_id,)).fetchone()
        strength_a = latest_strength(conn, team_a_id)
        strength_b = latest_strength(conn, team_b_id)
        style_a = latest_style(conn, team_a_id)
        style_b = latest_style(conn, team_b_id)
        params = latest_model_parameters(conn)
        if parameter_overrides:
            for key in DEFAULT_PARAMETERS:
                if key in parameter_overrides and parameter_overrides[key] is not None:
                    params[key] = float(parameter_overrides[key])
            params["parameter_id"] = parameter_overrides.get("parameter_id", "override")
        ranking_a = latest_fifa_ranking(conn, team_a_id)
        ranking_b = latest_fifa_ranking(conn, team_b_id)
        lineup_a = latest_lineup(conn, team_a_id, team_b_id)
        lineup_b = latest_lineup(conn, team_b_id, team_a_id)
        injuries_a = injury_summary(conn, team_a_id)
        injuries_b = injury_summary(conn, team_b_id)
        plan_a = latest_tactical_plan(conn, team_a_id, team_b_id)
        plan_b = latest_tactical_plan(conn, team_b_id, team_a_id)
        formation_a = formation_from_context(style_a, lineup_a, plan_a)
        formation_b = formation_from_context(style_b, lineup_b, plan_b)
        formation_stats = formation_matchup(conn, formation_a, formation_b)
        formation_a_delta, formation_b_delta, formation_note = formation_goal_delta(
            formation_stats, params["formation_weight"]
        )

        base_goals = params["base_goals"]
        attack_edge_a = (strength_a["attack_rating"] - strength_b["defense_rating"]) / 100.0
        attack_edge_b = (strength_b["attack_rating"] - strength_a["defense_rating"]) / 100.0
        overall_edge_a = (strength_a["overall_rating"] - strength_b["overall_rating"]) / 100.0
        overall_edge_b = -overall_edge_a
        keeper_edge_a = (strength_a["goalkeeper_rating"] - strength_b["goalkeeper_rating"]) / 100.0
        keeper_edge_b = -keeper_edge_a
        set_piece_edge_a = (strength_a["set_piece_rating"] - strength_b["set_piece_rating"]) / 100.0
        set_piece_edge_b = -set_piece_edge_a
        fitness_edge_a = (strength_a["fitness_rating"] - strength_b["fitness_rating"]) / 100.0
        fitness_edge_b = -fitness_edge_a

        style_delta_a, style_notes_a = matchup_style_delta(style_a, style_b)
        style_delta_b, style_notes_b = matchup_style_delta(style_b, style_a)
        manual_a, manual_b, manual_notes = manual_adjustments(conn, team_a_id, team_b_id)
        tactic_a = tactical_goal_delta(plan_a)
        tactic_b = tactical_goal_delta(plan_b)

        home_a = 0.0
        home_b = 0.0
        if not neutral_site:
            if team_a["is_host"]:
                home_a += params["home_edge"]
            if team_b["is_host"]:
                home_b += params["home_edge"]

        knockout_drag = params["knockout_drag"] if stage and "group" not in stage.lower() else 0.0
        lambda_a = (
            base_goals
            + params["attack_weight"] * attack_edge_a
            + params["overall_weight"] * overall_edge_a
            - params["keeper_weight"] * keeper_edge_b
            + params["set_piece_weight"] * set_piece_edge_a
            + params["fitness_weight"] * fitness_edge_a
            + params["style_weight"] * style_delta_a
            + manual_a
            + tactic_a
            + formation_a_delta
            + home_a
            + knockout_drag
        )
        lambda_b = (
            base_goals
            + params["attack_weight"] * attack_edge_b
            + params["overall_weight"] * overall_edge_b
            - params["keeper_weight"] * keeper_edge_a
            + params["set_piece_weight"] * set_piece_edge_b
            + params["fitness_weight"] * fitness_edge_b
            + params["style_weight"] * style_delta_b
            + manual_b
            + tactic_b
            + formation_b_delta
            + home_b
            + knockout_drag
        )
        lambda_a = clamp(lambda_a, 0.2, 3.8)
        lambda_b = clamp(lambda_b, 0.2, 3.8)
        p_a, p_draw, p_b, top_scores = score_distribution(lambda_a, lambda_b)

        prediction_id = f"pred-{team_a_id.lower()}-{team_b_id.lower()}-{now_utc().replace(':', '').replace('+', '')}"
        result = {
            "prediction_id": prediction_id,
            "predicted_at": now_utc(),
            "team_a": {"team_id": team_a_id, "name": team_a["name"]},
            "team_b": {"team_id": team_b_id, "name": team_b["name"]},
            "stage": stage,
            "neutral_site": neutral_site,
            "lambda_a": round(lambda_a, 3),
            "lambda_b": round(lambda_b, 3),
            "probabilities": {
                "team_a_win": round(p_a, 4),
                "draw": round(p_draw, 4),
                "team_b_win": round(p_b, 4),
            },
            "top_scorelines": top_scores,
            "strength": {
                team_a_id: {
                    "overall": strength_a["overall_rating"],
                    "attack": strength_a["attack_rating"],
                    "defense": strength_a["defense_rating"],
                    "form": strength_a["form_rating"],
                    "fitness": strength_a["fitness_rating"],
                    "uncertainty": strength_a["uncertainty"],
                },
                team_b_id: {
                    "overall": strength_b["overall_rating"],
                    "attack": strength_b["attack_rating"],
                    "defense": strength_b["defense_rating"],
                    "form": strength_b["form_rating"],
                    "fitness": strength_b["fitness_rating"],
                    "uncertainty": strength_b["uncertainty"],
                },
            },
            "rankings": {
                team_a_id: ranking_a,
                team_b_id: ranking_b,
            },
            "lineups": {
                team_a_id: {
                    "lineup_id": lineup_a.get("lineup_id"),
                    "lineup_type": lineup_a.get("lineup_type"),
                    "as_of": lineup_a.get("as_of"),
                    "formation": lineup_a.get("formation"),
                    "player_count": len(lineup_a.get("players", [])),
                    "starter_count": len([p for p in lineup_a.get("players", []) if p.get("is_starter")]),
                },
                team_b_id: {
                    "lineup_id": lineup_b.get("lineup_id"),
                    "lineup_type": lineup_b.get("lineup_type"),
                    "as_of": lineup_b.get("as_of"),
                    "formation": lineup_b.get("formation"),
                    "player_count": len(lineup_b.get("players", [])),
                    "starter_count": len([p for p in lineup_b.get("players", []) if p.get("is_starter")]),
                },
            },
            "injuries": {
                team_a_id: injuries_a,
                team_b_id: injuries_b,
            },
            "tactics": {
                team_a_id: {
                    "formation": formation_a,
                    "defensive_shape": plan_a.get("defensive_shape"),
                    "pressing_trigger": plan_a.get("pressing_trigger"),
                    "buildup_pattern": plan_a.get("buildup_pattern"),
                    "chance_creation": plan_a.get("chance_creation"),
                    "transition_plan": plan_a.get("transition_plan"),
                    "set_piece_plan": plan_a.get("set_piece_plan"),
                    "risk_level": plan_a.get("risk_level"),
                    "goal_delta": round(tactic_a, 3),
                },
                team_b_id: {
                    "formation": formation_b,
                    "defensive_shape": plan_b.get("defensive_shape"),
                    "pressing_trigger": plan_b.get("pressing_trigger"),
                    "buildup_pattern": plan_b.get("buildup_pattern"),
                    "chance_creation": plan_b.get("chance_creation"),
                    "transition_plan": plan_b.get("transition_plan"),
                    "set_piece_plan": plan_b.get("set_piece_plan"),
                    "risk_level": plan_b.get("risk_level"),
                    "goal_delta": round(tactic_b, 3),
                },
            },
            "formation_matchup": {
                "formation_a": formation_a,
                "formation_b": formation_b,
                "goal_delta_a": round(formation_a_delta, 3),
                "goal_delta_b": round(formation_b_delta, 3),
                "stats": formation_stats,
                "note": formation_note,
            },
            "matchup_notes": {
                team_a_id: style_notes_a,
                team_b_id: style_notes_b,
                "manual": manual_notes + ([formation_note] if formation_note else []),
            },
            "data_readiness": {
                team_a_id: data_readiness(conn, team_a_id),
                team_b_id: data_readiness(conn, team_b_id),
            },
            "model_parameters": params,
            "model_version": MODEL_VERSION,
            "data_cutoff": today_utc(),
        }

        if save:
            conn.execute(
                """
                INSERT INTO predictions (
                    prediction_id, predicted_at, team_a_id, team_b_id, stage, neutral_site,
                    lambda_a, lambda_b, p_team_a_win, p_draw, p_team_b_win,
                    top_scorelines_json, model_version, data_cutoff, notes
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    prediction_id,
                    result["predicted_at"],
                    team_a_id,
                    team_b_id,
                    stage,
                    1 if neutral_site else 0,
                    lambda_a,
                    lambda_b,
                    p_a,
                    p_draw,
                    p_b,
                    json.dumps(top_scores, ensure_ascii=True),
                    MODEL_VERSION,
                    result["data_cutoff"],
                    "Generated by predict_match.py; probabilities are model estimates, not certainty.",
                ),
            )
            conn.commit()

        return result
    finally:
        conn.close()


def pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def recommended_score(result: dict) -> str:
    top = result["top_scorelines"][0]["score"]
    goals_a, goals_b = [int(part) for part in top.split("-")]
    probs = result["probabilities"]
    if probs["team_a_win"] > probs["draw"] and probs["team_a_win"] > probs["team_b_win"] and goals_a <= goals_b:
        goals_a = goals_b + 1
    elif probs["team_b_win"] > probs["draw"] and probs["team_b_win"] > probs["team_a_win"] and goals_b <= goals_a:
        goals_b = goals_a + 1
    elif probs["draw"] >= probs["team_a_win"] and probs["draw"] >= probs["team_b_win"] and goals_a != goals_b:
        m = min(goals_a, goals_b)
        goals_a = goals_b = m
    return f"{goals_a}-{goals_b}"


def confidence_label(result: dict) -> str:
    strengths = list(result["strength"].values())
    uncertainty = sum(float(item["uncertainty"] or 25.0) for item in strengths) / max(len(strengths), 1)
    probs = sorted(result["probabilities"].values(), reverse=True)
    edge = probs[0] - probs[1] if len(probs) >= 2 else 0.0
    if uncertainty <= 12 and edge >= 0.16:
        return "high"
    if uncertainty <= 18 and edge >= 0.09:
        return "medium"
    if uncertainty <= 26:
        return "medium-low"
    return "low"


def ranking_text(ranking: dict) -> str:
    if not ranking:
        return "ranking unavailable"
    rank = ranking.get("rank")
    points = ranking.get("points")
    date = ranking.get("ranking_date") or "date unknown"
    ranking_type = ranking.get("ranking_type") or "unknown"
    points_text = f", {float(points):.2f} pts" if points is not None else ""
    return f"rank {rank}{points_text} ({ranking_type}, {date})"


def readiness_text(readiness: dict) -> str:
    bits = []
    bits.append("FIFA ranking" if readiness.get("fifa_ranking") else "no FIFA ranking")
    result_count = int(readiness.get("recent_results") or 0)
    xg_count = int(readiness.get("xg_results") or 0)
    bits.append(f"{result_count} recent results")
    bits.append(f"{xg_count} xG rows")
    ratings = int(readiness.get("player_ratings") or 0)
    bits.append(f"{ratings} provider player ratings")
    injuries = int(readiness.get("injuries") or 0)
    bits.append(f"{injuries} injury rows")
    bits.append("lineup available" if readiness.get("lineup") else "no lineup")
    bits.append("tactical plan" if readiness.get("tactical_plan") else "no tactical plan")
    return "; ".join(bits)


def lineup_text(lineup: dict) -> str:
    if not lineup or not lineup.get("lineup_id"):
        return "no lineup stored"
    formation = lineup.get("formation") or "formation unknown"
    lineup_type = lineup.get("lineup_type") or "unknown"
    as_of = lineup.get("as_of") or "time unknown"
    return (
        f"{lineup_type}, {formation}, {lineup.get('starter_count', 0)} starters/"
        f"{lineup.get('player_count', 0)} players, as of {as_of}"
    )


def injury_text(injuries: dict) -> str:
    if not injuries or not injuries.get("count"):
        return "no current injury rows stored"
    major = injuries.get("major") or []
    if not major:
        return f"{injuries['count']} injury/availability rows, no major absence flagged"
    details = []
    for item in major[:3]:
        availability = item.get("availability_pct")
        availability_text = f", {availability:.0f}% available" if availability is not None else ""
        details.append(f"{item['player']} {item['status']}{availability_text}")
    return f"{injuries['count']} injury/availability rows; " + "; ".join(details)


def tactic_text(tactic: dict) -> str:
    formation = tactic.get("formation") or "formation unknown"
    parts = [formation]
    if tactic.get("defensive_shape"):
        parts.append(f"defensive shape: {tactic['defensive_shape']}")
    if tactic.get("pressing_trigger"):
        parts.append(f"pressing: {tactic['pressing_trigger']}")
    if tactic.get("chance_creation"):
        parts.append(f"chance creation: {tactic['chance_creation']}")
    if tactic.get("risk_level") is not None:
        parts.append(f"risk {float(tactic['risk_level']):.0f}/100")
    return "; ".join(parts)


def format_report(result: dict) -> str:
    team_a = result["team_a"]["name"]
    team_b = result["team_b"]["name"]
    team_a_id = result["team_a"]["team_id"]
    team_b_id = result["team_b"]["team_id"]
    probs = result["probabilities"]
    lines = [
        f"Match: {team_a} vs {team_b}",
        f"Data cutoff: {result['data_cutoff']} | Model: {result['model_version']} | Stage: {result['stage']}",
        (
            "Data readiness: "
            f"{team_a}: {readiness_text(result['data_readiness'][team_a_id])} | "
            f"{team_b}: {readiness_text(result['data_readiness'][team_b_id])}"
        ),
        "",
        "Win/draw/loss analysis:",
        f"- {team_a} win: {pct(probs['team_a_win'])}",
        f"- Draw: {pct(probs['draw'])}",
        f"- {team_b} win: {pct(probs['team_b_win'])}",
        "",
        "Expected goals:",
        f"- {team_a}: {result['lambda_a']:.2f}",
        f"- {team_b}: {result['lambda_b']:.2f}",
        "",
        "Top 8 score probabilities:",
    ]
    for index, item in enumerate(result["top_scorelines"], start=1):
        lines.append(f"{index}. {team_a} {item['score']} {team_b}: {pct(item['probability'])}")

    strength_a = result["strength"][team_a_id]
    strength_b = result["strength"][team_b_id]
    lines.extend(
        [
            "",
            "Strength snapshot:",
            (
                f"- {team_a}: overall {strength_a['overall']:.1f}, attack {strength_a['attack']:.1f}, "
                f"defense {strength_a['defense']:.1f}, form {strength_a['form']:.1f}, "
                f"fitness {strength_a['fitness']:.1f}, uncertainty {strength_a['uncertainty']:.1f}; "
                f"{ranking_text(result['rankings'][team_a_id])}"
            ),
            (
                f"- {team_b}: overall {strength_b['overall']:.1f}, attack {strength_b['attack']:.1f}, "
                f"defense {strength_b['defense']:.1f}, form {strength_b['form']:.1f}, "
                f"fitness {strength_b['fitness']:.1f}, uncertainty {strength_b['uncertainty']:.1f}; "
                f"{ranking_text(result['rankings'][team_b_id])}"
            ),
            "",
            "Lineups and availability:",
            f"- {team_a}: {lineup_text(result['lineups'][team_a_id])}; {injury_text(result['injuries'][team_a_id])}",
            f"- {team_b}: {lineup_text(result['lineups'][team_b_id])}; {injury_text(result['injuries'][team_b_id])}",
            "",
            "Tactical arrangement:",
            f"- {team_a}: {tactic_text(result['tactics'][team_a_id])}",
            f"- {team_b}: {tactic_text(result['tactics'][team_b_id])}",
            "",
            "Formation matchup:",
            (
                f"- {result['formation_matchup']['formation_a'] or 'unknown'} vs "
                f"{result['formation_matchup']['formation_b'] or 'unknown'}; "
                f"goal delta {team_a} {result['formation_matchup']['goal_delta_a']:+.2f}, "
                f"{team_b} {result['formation_matchup']['goal_delta_b']:+.2f}"
            ),
            "",
            "Matchup notes:",
        ]
    )
    notes = []
    for label, values in result["matchup_notes"].items():
        if not values:
            continue
        label_name = team_a if label == team_a_id else team_b if label == team_b_id else "manual"
        notes.extend([f"- {label_name}: {value}" for value in values])
    if notes:
        lines.extend(notes)
    else:
        lines.append("- No specific matchup adjustment is currently stored; result is driven by baseline squad/profile ratings.")

    lines.extend(
        [
            "",
            f"Predicted score lean: {team_a} {recommended_score(result)} {team_b}",
            f"Confidence: {confidence_label(result)}",
            "Note: single scorelines are low-probability events; use the full distribution above.",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, help="SQLite database path.")
    parser.add_argument("--team-a", required=True, help="Team A name, team_id, or FIFA code.")
    parser.add_argument("--team-b", required=True, help="Team B name, team_id, or FIFA code.")
    parser.add_argument("--stage", default="Group Stage", help="Competition stage.")
    parser.add_argument("--non-neutral", action="store_true", help="Apply host home edge when relevant.")
    parser.add_argument("--save", action="store_true", help="Persist prediction to predictions table.")
    parser.add_argument("--format", choices=["json", "report"], default="report", help="Output format.")
    args = parser.parse_args()

    result = predict(Path(args.db), args.team_a, args.team_b, args.stage, not args.non_neutral, args.save)
    if args.format == "json":
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(format_report(result))


if __name__ == "__main__":
    main()
