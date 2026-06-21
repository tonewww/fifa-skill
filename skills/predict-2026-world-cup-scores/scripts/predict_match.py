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
    "openness_baseline_total": 2.55,
    "recent_goal_openness_weight": 0.28,
    "formation_openness_weight": 0.38,
    "style_openness_weight": 0.20,
    "tactical_openness_weight": 0.14,
    "openness_max_delta": 0.95,
    "stage_group_goal_multiplier": 1.00,
    "stage_round32_goal_multiplier": 1.00,
    "stage_round16_goal_multiplier": 1.00,
    "stage_quarter_goal_multiplier": 1.00,
    "stage_semi_goal_multiplier": 1.00,
    "stage_final_goal_multiplier": 1.00,
    "stage_data_weight": 0.70,
    "stage_sample_half_life": 36.0,
    "stage_open_match_resistance": 0.45,
    "wdl_prior_weight": 0.65,
    "formation_wdl_prior_max_weight": 0.35,
    "wdl_score_calibration_weight": 0.70,
    "favorite_score_tilt": 0.10,
    "draw_score_tilt": 0.08,
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


def latest_model_parameters(conn) -> dict:
    params = DEFAULT_PARAMETERS.copy()
    params["parameter_id"] = "defaults"
    if not table_exists(conn, "model_parameters"):
        return params
    ensure_model_parameter_columns(conn)
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
        if key in row.keys() and row[key] is not None:
            params[key] = float(row[key])
    params["parameter_id"] = row["parameter_id"]
    return params


def ensure_model_parameter_columns(conn) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(model_parameters)").fetchall()}
    additions = {
        "openness_baseline_total": "REAL",
        "recent_goal_openness_weight": "REAL",
        "formation_openness_weight": "REAL",
        "style_openness_weight": "REAL",
        "tactical_openness_weight": "REAL",
        "openness_max_delta": "REAL",
        "stage_group_goal_multiplier": "REAL",
        "stage_round32_goal_multiplier": "REAL",
        "stage_round16_goal_multiplier": "REAL",
        "stage_quarter_goal_multiplier": "REAL",
        "stage_semi_goal_multiplier": "REAL",
        "stage_final_goal_multiplier": "REAL",
        "stage_data_weight": "REAL",
        "stage_sample_half_life": "REAL",
        "stage_open_match_resistance": "REAL",
        "wdl_prior_weight": "REAL",
        "formation_wdl_prior_max_weight": "REAL",
        "wdl_score_calibration_weight": "REAL",
        "favorite_score_tilt": "REAL",
        "draw_score_tilt": "REAL",
    }
    changed = False
    for name, ddl_type in additions.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE model_parameters ADD COLUMN {name} {ddl_type}")
            changed = True
    if changed:
        conn.commit()


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
            SELECT lp.*, p.name, COALESCE(p.national_team_position, p.position) AS base_position, p.rating_overall
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


def formation_total_goals(stats: dict) -> float | None:
    if not stats:
        return None
    goals_a = stats.get("avg_goals_a")
    goals_b = stats.get("avg_goals_b")
    if goals_a is None or goals_b is None:
        return None
    return float(goals_a) + float(goals_b)


def tactical_goal_delta(plan: dict) -> float:
    if not plan:
        return 0.0
    risk = plan.get("risk_level")
    if risk is None:
        return 0.0
    return clamp((float(risk) - 50.0) / 100.0 * 0.08, -0.06, 0.08)


def tactical_risk(plan: dict) -> float:
    risk = plan.get("risk_level") if plan else None
    if risk is None:
        return 50.0
    try:
        return float(risk)
    except (TypeError, ValueError):
        return 50.0


def recent_goal_profile(conn, team_id: str, limit: int = 8) -> dict:
    rows = conn.execute(
        """
        SELECT goals_for, goals_against, competition
        FROM team_results
        WHERE team_id = ?
          AND goals_for IS NOT NULL
          AND goals_against IS NOT NULL
        ORDER BY date(match_date) DESC
        LIMIT ?
        """,
        (team_id, limit),
    ).fetchall()
    if not rows:
        return {
            "sample": 0,
            "goals_for": 1.25,
            "goals_against": 1.25,
            "total_goals": 2.5,
            "competitive_weight": 1.0,
        }
    total_weight = 0.0
    goals_for = goals_against = total_goals = competitive_weight = 0.0
    for index, row in enumerate(rows):
        competition = (row["competition"] or "").lower()
        comp_weight = 1.0
        if "world cup" in competition and "qualification" not in competition:
            comp_weight = 1.35
        elif "qualification" in competition or "nations league" in competition:
            comp_weight = 1.10
        elif "friendly" in competition:
            comp_weight = 0.75
        recency_weight = 1.0 / (1.0 + index * 0.12)
        weight = comp_weight * recency_weight
        gf = float(row["goals_for"] or 0.0)
        ga = float(row["goals_against"] or 0.0)
        goals_for += gf * weight
        goals_against += ga * weight
        total_goals += (gf + ga) * weight
        competitive_weight += comp_weight * recency_weight
        total_weight += weight
    return {
        "sample": len(rows),
        "goals_for": goals_for / total_weight,
        "goals_against": goals_against / total_weight,
        "total_goals": total_goals / total_weight,
        "competitive_weight": competitive_weight / total_weight,
    }


def style_openness(style_a: dict, style_b: dict) -> tuple[float, list[str]]:
    tempo = (style_value(style_a, "tempo") + style_value(style_b, "tempo")) / 2.0
    press = (style_value(style_a, "press_intensity") + style_value(style_b, "press_intensity")) / 2.0
    transition = (style_value(style_a, "transition_attack") + style_value(style_b, "transition_attack")) / 2.0
    defensive_security = (style_value(style_a, "transition_defense") + style_value(style_b, "transition_defense")) / 2.0
    line_height = (style_value(style_a, "defensive_line") + style_value(style_b, "defensive_line")) / 2.0
    chance_routes = (
        style_value(style_a, "wing_play")
        + style_value(style_b, "wing_play")
        + style_value(style_a, "central_progression")
        + style_value(style_b, "central_progression")
    ) / 4.0
    raw = (
        0.24 * (tempo - 55.0)
        + 0.20 * (press - 58.0)
        + 0.26 * (transition - defensive_security)
        + 0.14 * (line_height - 50.0)
        + 0.16 * (chance_routes - 58.0)
    )
    delta = clamp(raw / 28.0, -0.22, 0.28)
    notes = []
    if tempo >= 59:
        notes.append("tempo profile points to a higher-event match")
    if transition - defensive_security >= 4:
        notes.append("transition attack exceeds transition defense")
    if press >= 61:
        notes.append("pressing can create turnovers and short-field chances")
    if chance_routes >= 62:
        notes.append("wide/central chance routes support shot volume")
    return delta, notes[:3]


def openness_adjustment(
    conn,
    team_a_id: str,
    team_b_id: str,
    style_a: dict,
    style_b: dict,
    plan_a: dict,
    plan_b: dict,
    formation_stats: dict,
    params: dict,
) -> dict:
    baseline = float(params["openness_baseline_total"])
    recent_a = recent_goal_profile(conn, team_a_id)
    recent_b = recent_goal_profile(conn, team_b_id)
    recent_total = (recent_a["total_goals"] + recent_b["total_goals"]) / 2.0
    recent_confidence = min((recent_a["sample"] + recent_b["sample"]) / 16.0, 1.0)
    recent_delta = (
        (recent_total - baseline)
        * float(params["recent_goal_openness_weight"])
        * recent_confidence
    )

    formation_total = formation_total_goals(formation_stats)
    formation_sample = int(formation_stats.get("sample_size") or 0) if formation_stats else 0
    formation_confidence = min(formation_sample / 20.0, 1.0)
    if formation_sample < 5:
        formation_confidence *= 0.35
    formation_delta = 0.0
    if formation_total is not None:
        formation_delta = (
            (formation_total - baseline)
            * float(params["formation_openness_weight"])
            * formation_confidence
        )

    style_delta, style_notes = style_openness(style_a, style_b)
    style_delta *= float(params["style_openness_weight"])

    risk_average = (tactical_risk(plan_a) + tactical_risk(plan_b)) / 2.0
    tactical_delta = ((risk_average - 50.0) / 50.0) * float(params["tactical_openness_weight"])

    raw_delta = recent_delta + formation_delta + style_delta + tactical_delta
    total_delta = clamp(raw_delta, -float(params["openness_max_delta"]), float(params["openness_max_delta"]))
    notes = []
    if recent_delta >= 0.12:
        notes.append(f"recent goal profile is open ({recent_total:.2f} total goals avg)")
    elif recent_delta <= -0.12:
        notes.append(f"recent goal profile is closed ({recent_total:.2f} total goals avg)")
    if formation_delta >= 0.12 and formation_total is not None:
        notes.append(f"formation pair has high historical total goals ({formation_total:.2f})")
    elif formation_delta <= -0.12 and formation_total is not None:
        notes.append(f"formation pair has low historical total goals ({formation_total:.2f})")
    notes.extend(style_notes)
    if tactical_delta >= 0.04:
        notes.append(f"tactical risk is above neutral ({risk_average:.0f}/100)")
    elif tactical_delta <= -0.04:
        notes.append(f"tactical risk is below neutral ({risk_average:.0f}/100)")
    return {
        "total_delta": total_delta,
        "recent_delta": recent_delta,
        "formation_delta": formation_delta,
        "style_delta": style_delta,
        "tactical_delta": tactical_delta,
        "recent_a": recent_a,
        "recent_b": recent_b,
        "recent_total_goals": recent_total,
        "formation_total_goals": formation_total,
        "formation_sample": formation_sample,
        "tactical_risk_average": risk_average,
        "notes": notes[:6],
    }


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

    central_edge = style_value(attacker_style, "central_progression") - style_value(defender_style, "defensive_compactness")
    if central_edge > 20:
        delta += 0.04
        notes.append("central progression can exploit lack of compactness")
    elif central_edge < -20:
        delta -= 0.03
        notes.append("central attacks will be stifled by compact defense")

    return clamp(delta, -0.25, 0.25), notes[:4]


def poisson(k: int, lam: float) -> float:
    return math.exp(-lam) * (lam**k) / math.factorial(k)


def score_group_from_goals(goals_a: int, goals_b: int) -> str:
    if goals_a > goals_b:
        return "team_a_win"
    if goals_a < goals_b:
        return "team_b_win"
    return "draw"


def stage_family(stage: str | None) -> str:
    text = (stage or "").lower().replace("-", " ")
    if "group" in text:
        return "group"
    if "round of 32" in text or "last 32" in text or "round32" in text:
        return "round32"
    if "round of 16" in text or "last 16" in text or "round16" in text:
        return "round16"
    if "quarter" in text:
        return "quarter"
    if "semi" in text:
        return "semi"
    if "final" in text or "third place" in text or "bronze" in text:
        return "final"
    if "knockout" in text or "playoff" in text:
        return "round16"
    return "group"


def stage_completed_goal_profile(conn, family: str) -> dict:
    if not table_exists(conn, "fixtures"):
        return {"sample_size": 0, "avg_total_goals": None}
    rows = conn.execute(
        """
        SELECT stage, score_a, score_b
        FROM fixtures
        WHERE score_a IS NOT NULL
          AND score_b IS NOT NULL
          AND lower(coalesce(status, 'final')) = 'final'
        """
    ).fetchall()
    totals = []
    for row in rows:
        if stage_family(row["stage"]) == family:
            totals.append(float(row["score_a"] or 0) + float(row["score_b"] or 0))
    if not totals:
        return {"sample_size": 0, "avg_total_goals": None}
    return {"sample_size": len(totals), "avg_total_goals": sum(totals) / len(totals)}


def stage_goal_context(
    conn,
    stage: str | None,
    params: dict,
    openness_total_delta: float,
) -> dict:
    family = stage_family(stage)
    key = f"stage_{family}_goal_multiplier"
    base_multiplier = float(params.get(key, 1.0) or 1.0)
    profile = stage_completed_goal_profile(conn, family)
    baseline_total = float(params.get("openness_baseline_total", 2.55) or 2.55)
    sample_size = int(profile["sample_size"] or 0)
    avg_total_goals = profile["avg_total_goals"]
    sample_half_life = max(float(params.get("stage_sample_half_life", 36.0) or 36.0), 1.0)
    confidence = sample_size / (sample_size + sample_half_life) if sample_size else 0.0
    data_multiplier = 1.0
    if avg_total_goals is not None and baseline_total > 0:
        data_multiplier = clamp(avg_total_goals / baseline_total, 0.76, 1.18)

    data_weight = clamp(float(params.get("stage_data_weight", 0.70) or 0.70), 0.0, 1.0)
    adaptive_weight = data_weight * confidence
    multiplier = (1.0 - adaptive_weight) * 1.0 + adaptive_weight * data_multiplier

    if sample_size == 0:
        multiplier = 1.0
    elif confidence < 0.25:
        # With thin round-specific evidence, keep only a small portion of the configured prior.
        multiplier = 0.82 * multiplier + 0.18 * base_multiplier
    else:
        multiplier = 0.55 * multiplier + 0.45 * base_multiplier

    open_resistance = clamp(float(params.get("stage_open_match_resistance", 0.45) or 0.45), 0.0, 1.0)
    open_match_offset = max(0.0, openness_total_delta) * open_resistance * (1.0 - confidence * 0.55)
    open_multiplier_offset = open_match_offset / max(baseline_total, 0.1)
    if multiplier < 1.0:
        multiplier = min(1.0, multiplier + open_multiplier_offset)
    multiplier = clamp(multiplier, 0.78, 1.16)
    notes = []
    if avg_total_goals is None:
        notes.append("No completed same-stage sample; stage multiplier stays neutral.")
    else:
        notes.append(
            f"Same-stage completed sample {sample_size}, avg total goals {avg_total_goals:.2f}, confidence {confidence:.2f}."
        )
    if open_match_offset > 0.03 and multiplier < 1.0:
        notes.append("Current matchup openness offsets part of the conservative stage prior.")
    return {
        "family": family,
        "base_multiplier": base_multiplier,
        "data_multiplier": data_multiplier,
        "multiplier": multiplier,
        "sample_size": sample_size,
        "avg_total_goals": avg_total_goals,
        "confidence": confidence,
        "open_match_offset": open_match_offset,
        "notes": notes,
    }


def softmax_three(edge_a: float, draw_score: float, edge_b: float) -> dict[str, float]:
    values = {
        "team_a_win": edge_a,
        "draw": draw_score,
        "team_b_win": edge_b,
    }
    max_value = max(values.values())
    exp_values = {key: math.exp(value - max_value) for key, value in values.items()}
    total = sum(exp_values.values())
    return {key: value / total for key, value in exp_values.items()}


def strength_wdl_prior(
    strength_a: dict,
    strength_b: dict,
    attack_edge_a: float,
    attack_edge_b: float,
    style_delta_a: float,
    style_delta_b: float,
    tactic_a: float,
    tactic_b: float,
    formation_a_delta: float,
    formation_b_delta: float,
    manual_a: float,
    manual_b: float,
    home_a: float,
    home_b: float,
    openness_total_delta: float,
    stage_kind: str,
    stage_context: dict,
) -> dict[str, float]:
    """First-stage WDL prior from team features, before exact-score calibration."""
    overall_edge = (float(strength_a["overall_rating"]) - float(strength_b["overall_rating"])) / 100.0
    form_edge = (float(strength_a["form_rating"]) - float(strength_b["form_rating"])) / 100.0
    defense_edge_a = (float(strength_a["defense_rating"]) - float(strength_b["attack_rating"])) / 100.0
    defense_edge_b = (float(strength_b["defense_rating"]) - float(strength_a["attack_rating"])) / 100.0
    keeper_edge = (float(strength_a["goalkeeper_rating"]) - float(strength_b["goalkeeper_rating"])) / 100.0
    set_piece_edge = (float(strength_a["set_piece_rating"]) - float(strength_b["set_piece_rating"])) / 100.0
    feature_edge = (
        2.00 * overall_edge
        + 1.25 * (attack_edge_a - attack_edge_b)
        + 0.72 * (defense_edge_a - defense_edge_b)
        + 0.48 * form_edge
        + 0.34 * keeper_edge
        + 0.24 * set_piece_edge
        + 1.80
        * (
            style_delta_a
            - style_delta_b
            + tactic_a
            - tactic_b
            + formation_a_delta
            - formation_b_delta
            + manual_a
            - manual_b
            + home_a
            - home_b
        )
    )
    uncertainty = (
        float(strength_a.get("uncertainty") or 25.0)
        + float(strength_b.get("uncertainty") or 25.0)
    ) / 2.0
    edge_shrink = clamp(1.0 - max(0.0, uncertainty - 12.0) / 55.0, 0.58, 1.0)
    feature_edge *= edge_shrink
    openness_draw_penalty = clamp(openness_total_delta * 0.18, -0.08, 0.10)
    stage_multiplier = float(stage_context.get("multiplier") or 1.0)
    stage_confidence = float(stage_context.get("confidence") or 0.0)
    knockout_draw_bonus = clamp((1.0 - stage_multiplier) * (0.45 + 0.55 * stage_confidence), 0.0, 0.10)
    if stage_kind == "group":
        knockout_draw_bonus = 0.0
    draw_score = -0.18 - 0.55 * abs(feature_edge) - openness_draw_penalty + knockout_draw_bonus
    return softmax_three(feature_edge, draw_score, -feature_edge)


def raw_score_grid(lambda_a: float, lambda_b: float, max_goals: int = 7) -> list[dict]:
    rows = []
    p_a = p_d = p_b = 0.0
    for goals_a in range(max_goals + 1):
        for goals_b in range(max_goals + 1):
            prob = poisson(goals_a, lambda_a) * poisson(goals_b, lambda_b)
            group = score_group_from_goals(goals_a, goals_b)
            if group == "team_a_win":
                p_a += prob
            elif group == "draw":
                p_d += prob
            else:
                p_b += prob
            rows.append({"score": f"{goals_a}-{goals_b}", "group": group, "probability": prob})
    total = p_a + p_d + p_b
    if total > 0:
        for row in rows:
            row["probability"] = row["probability"] / total
    return rows


def aggregate_wdl(rows: list[dict]) -> dict[str, float]:
    totals = {"team_a_win": 0.0, "draw": 0.0, "team_b_win": 0.0}
    for row in rows:
        totals[row["group"]] += float(row["probability"])
    return totals


def normalize_scores(rows: list[dict]) -> list[dict]:
    total = sum(float(row["probability"]) for row in rows)
    if total <= 0:
        return rows
    for row in rows:
        row["probability"] = float(row["probability"]) / total
    return rows


def calibrate_score_distribution(rows: list[dict], target_wdl: dict[str, float], params: dict) -> list[dict]:
    current_wdl = aggregate_wdl(rows)
    weight = clamp(float(params.get("wdl_score_calibration_weight", 0.0) or 0.0), 0.0, 1.0)
    favorite_group = max(target_wdl.items(), key=lambda item: item[1])[0]
    favorite_probability = float(target_wdl[favorite_group])
    draw_probability = float(target_wdl["draw"])
    favorite_tilt = float(params.get("favorite_score_tilt", 0.0) or 0.0)
    draw_tilt = float(params.get("draw_score_tilt", 0.0) or 0.0)
    calibrated = []
    for row in rows:
        group = row["group"]
        current = max(current_wdl.get(group, 0.0), 1e-9)
        target = max(float(target_wdl.get(group, current)), 1e-9)
        multiplier = (1.0 - weight) + weight * (target / current)
        goals_a, goals_b = [int(part) for part in row["score"].split("-", 1)]
        margin = abs(goals_a - goals_b)
        total_goals = goals_a + goals_b
        if group == favorite_group and favorite_probability >= 0.42:
            multiplier *= 1.0 + favorite_tilt * min(margin, 3) / 3.0
        if group == "draw" and draw_probability >= 0.30:
            multiplier *= 1.0 + draw_tilt / (1.0 + abs(total_goals - 2))
            
        # Adjust for Poisson's low-score bias (Dixon-Coles inspired heuristic)
        if total_goals == 0:
            multiplier *= 0.85  # Reduce 0-0 probability
        elif total_goals >= 3:
            multiplier *= 1.15  # Boost matches with 3+ goals
        elif group == "draw" and total_goals == 2:
            multiplier *= 1.10  # Boost 1-1 draw slightly to reflect realistic distribution
            
        calibrated.append({**row, "probability": float(row["probability"]) * multiplier})
    return normalize_scores(calibrated)


def score_distribution(
    lambda_a: float,
    lambda_b: float,
    target_wdl: dict[str, float] | None = None,
    params: dict | None = None,
    max_goals: int = 7,
) -> tuple[float, float, float, list[dict], dict]:
    rows = raw_score_grid(lambda_a, lambda_b, max_goals)
    raw_wdl = aggregate_wdl(rows)
    if target_wdl and params:
        rows = calibrate_score_distribution(rows, target_wdl, params)
    calibrated_wdl = aggregate_wdl(rows)
    top = sorted(rows, key=lambda item: item["probability"], reverse=True)[:8]
    for item in top:
        item["probability"] = round(item["probability"], 4)
    return (
        calibrated_wdl["team_a_win"],
        calibrated_wdl["draw"],
        calibrated_wdl["team_b_win"],
        top,
        {"raw_wdl": raw_wdl, "calibrated_wdl": calibrated_wdl},
    )


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
        openness = openness_adjustment(
            conn,
            team_a_id,
            team_b_id,
            style_a,
            style_b,
            plan_a,
            plan_b,
            formation_stats,
            params,
        )

        home_a = 0.0
        home_b = 0.0
        if not neutral_site:
            if team_a["is_host"]:
                home_a += params["home_edge"]
            if team_b["is_host"]:
                home_b += params["home_edge"]

        stage_context = stage_goal_context(conn, stage, params, openness["total_delta"])
        stage_multiplier = stage_context["multiplier"]
        stage_kind = stage_context["family"]
        lambda_a_base = (
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
        )
        lambda_b_base = (
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
        )
        recent_a = openness["recent_a"]
        recent_b = openness["recent_b"]
        attack_claim_a = max(0.20, lambda_a_base + 0.28 * attack_edge_a + 0.12 * recent_a["goals_for"])
        attack_claim_b = max(
            0.20,
            lambda_b_base + 0.28 * attack_edge_b + 0.12 * recent_b["goals_for"],
        )
        defensive_leak_a = max(0.0, recent_a["goals_against"] - 1.15)
        defensive_leak_b = max(0.0, recent_b["goals_against"] - 1.15)
        attack_claim_a += 0.10 * defensive_leak_b
        attack_claim_b += 0.10 * defensive_leak_a
        share_a = clamp(attack_claim_a / (attack_claim_a + attack_claim_b), 0.38, 0.68)
        openness_delta_a = openness["total_delta"] * share_a
        openness_delta_b = openness["total_delta"] * (1.0 - share_a)
        lambda_a = lambda_a_base + openness_delta_a
        lambda_b = lambda_b_base + openness_delta_b
        lambda_a_before_stage = lambda_a
        lambda_b_before_stage = lambda_b
        lambda_a *= stage_multiplier
        lambda_b *= stage_multiplier
        lambda_a = clamp(lambda_a, 0.2, 3.8)
        lambda_b = clamp(lambda_b, 0.2, 3.8)
        base_rows = raw_score_grid(lambda_a, lambda_b)
        raw_wdl = aggregate_wdl(base_rows)
        prior_wdl = strength_wdl_prior(
            strength_a,
            strength_b,
            attack_edge_a,
            attack_edge_b,
            style_delta_a,
            style_delta_b,
            tactic_a,
            tactic_b,
            formation_a_delta,
            formation_b_delta,
            manual_a,
            manual_b,
            home_a,
            home_b,
            openness["total_delta"],
            stage_kind,
            stage_context,
        )
        prior_weight = clamp(float(params.get("wdl_prior_weight", 0.65) or 0.65), 0.0, 1.0)
        target_wdl = {
            key: prior_weight * prior_wdl[key] + (1.0 - prior_weight) * raw_wdl[key]
            for key in ("team_a_win", "draw", "team_b_win")
        }
        formation_confidence = 0.0
        if formation_stats:
            max_formation_weight = clamp(
                float(params.get("formation_wdl_prior_max_weight", 0.35) or 0.35),
                0.0,
                0.65,
            )
            formation_confidence = min(float(formation_stats.get("sample_size") or 0) / 100.0, max_formation_weight)
            target_wdl = {
                "team_a_win": (1.0 - formation_confidence) * target_wdl["team_a_win"]
                + formation_confidence * float(formation_stats.get("p_a_win") or target_wdl["team_a_win"]),
                "draw": (1.0 - formation_confidence) * target_wdl["draw"]
                + formation_confidence * float(formation_stats.get("p_draw") or target_wdl["draw"]),
                "team_b_win": (1.0 - formation_confidence) * target_wdl["team_b_win"]
                + formation_confidence * float(formation_stats.get("p_b_win") or target_wdl["team_b_win"]),
            }
            total_target = sum(target_wdl.values())
            if total_target > 0:
                target_wdl = {key: value / total_target for key, value in target_wdl.items()}
        p_a, p_draw, p_b, top_scores, score_calibration = score_distribution(lambda_a, lambda_b, target_wdl, params)

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
            "lambda_components": {
                team_a_id: {
                    "base_before_openness": round(lambda_a_base, 3),
                    "openness_delta": round(openness_delta_a, 3),
                    "before_stage_multiplier": round(lambda_a_before_stage, 3),
                },
                team_b_id: {
                    "base_before_openness": round(lambda_b_base, 3),
                    "openness_delta": round(openness_delta_b, 3),
                    "before_stage_multiplier": round(lambda_b_before_stage, 3),
                },
                "stage_multiplier": round(stage_multiplier, 3),
                "stage_family": stage_kind,
                "stage_context": {
                    "base_multiplier": round(stage_context["base_multiplier"], 3),
                    "data_multiplier": round(stage_context["data_multiplier"], 3),
                    "sample_size": stage_context["sample_size"],
                    "avg_total_goals": (
                        round(stage_context["avg_total_goals"], 3)
                        if stage_context["avg_total_goals"] is not None
                        else None
                    ),
                    "confidence": round(stage_context["confidence"], 3),
                    "open_match_offset": round(stage_context["open_match_offset"], 3),
                    "notes": stage_context["notes"],
                },
            },
            "probabilities": {
                "team_a_win": round(p_a, 4),
                "draw": round(p_draw, 4),
                "team_b_win": round(p_b, 4),
            },
            "top_scorelines": top_scores,
            "score_calibration": {
                "prior_wdl": {key: round(value, 4) for key, value in prior_wdl.items()},
                "target_wdl": {key: round(value, 4) for key, value in target_wdl.items()},
                "raw_wdl": {key: round(value, 4) for key, value in score_calibration["raw_wdl"].items()},
                "calibrated_wdl": {
                    key: round(value, 4) for key, value in score_calibration["calibrated_wdl"].items()
                },
                "prior_weight": prior_weight,
                "formation_prior_weight": round(formation_confidence, 4),
                "weight": params["wdl_score_calibration_weight"],
                "favorite_tilt": params["favorite_score_tilt"],
                "draw_tilt": params["draw_score_tilt"],
            },
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
            "openness": {
                "total_delta": round(openness["total_delta"], 3),
                "recent_delta": round(openness["recent_delta"], 3),
                "formation_delta": round(openness["formation_delta"], 3),
                "style_delta": round(openness["style_delta"], 3),
                "tactical_delta": round(openness["tactical_delta"], 3),
                "recent_total_goals": round(openness["recent_total_goals"], 3),
                "formation_total_goals": (
                    round(openness["formation_total_goals"], 3)
                    if openness["formation_total_goals"] is not None
                    else None
                ),
                "formation_sample": openness["formation_sample"],
                "tactical_risk_average": round(openness["tactical_risk_average"], 1),
                "notes": openness["notes"],
            },
            "matchup_notes": {
                team_a_id: style_notes_a,
                team_b_id: style_notes_b,
                "openness": openness["notes"],
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
    top_score = result["top_scorelines"][0]["score"]
    goals_a, goals_b = [int(part) for part in top_score.split("-")]
    top_group = "team_a_win" if goals_a > goals_b else "team_b_win" if goals_b > goals_a else "draw"
    
    probs = result["probabilities"]
    best_group = max(probs, key=probs.get)
    best_prob = probs[best_group]
    score_group_prob = probs[top_group]
    
    # In football, Win/Loss total probabilities are sums of many scorelines, so a Draw (e.g. 1-1) 
    # can realistically be the single most likely exact scoreline even if one team has a 45% win prob.
    # We only override the mathematical top score if it heavily contradicts the WDL breakdown.
    if best_prob - score_group_prob > 0.12 and top_group != best_group:
        # Find the highest probability score that matches the dominant outcome group
        for item in result["top_scorelines"]:
            ga, gb = [int(part) for part in item["score"].split("-")]
            item_group = "team_a_win" if ga > gb else "team_b_win" if gb > ga else "draw"
            if item_group == best_group:
                return item["score"]
                
        # Fallback if no matching score is in the top_scorelines (rare)
        if best_group == "team_a_win" and goals_a <= goals_b:
            return f"{goals_b + 1}-{goals_b}"
        elif best_group == "team_b_win" and goals_b <= goals_a:
            return f"{goals_a}-{goals_a + 1}"
        elif best_group == "draw" and goals_a != goals_b:
            m = min(goals_a, goals_b)
            return f"{m}-{m}"
            
    return top_score


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
        (
            f"- stage balance: {result['lambda_components']['stage_family']} "
            f"adaptive multiplier {result['lambda_components']['stage_multiplier']:.2f} "
            f"(base {result['lambda_components']['stage_context']['base_multiplier']:.2f}, "
            f"data {result['lambda_components']['stage_context']['data_multiplier']:.2f}, "
            f"sample {result['lambda_components']['stage_context']['sample_size']})"
        ),
        (
            "Score/WDL calibration: "
            f"prior {pct(result['score_calibration']['prior_wdl']['team_a_win'])}/"
            f"{pct(result['score_calibration']['prior_wdl']['draw'])}/"
            f"{pct(result['score_calibration']['prior_wdl']['team_b_win'])}, "
            f"raw {pct(result['score_calibration']['raw_wdl']['team_a_win'])}/"
            f"{pct(result['score_calibration']['raw_wdl']['draw'])}/"
            f"{pct(result['score_calibration']['raw_wdl']['team_b_win'])}, "
            f"calibrated {pct(result['score_calibration']['calibrated_wdl']['team_a_win'])}/"
            f"{pct(result['score_calibration']['calibrated_wdl']['draw'])}/"
            f"{pct(result['score_calibration']['calibrated_wdl']['team_b_win'])}"
        ),
        "",
        "Match openness:",
        (
            f"- total lambda adjustment {result['openness']['total_delta']:+.2f} "
            f"(recent {result['openness']['recent_delta']:+.2f}, "
            f"formation {result['openness']['formation_delta']:+.2f}, "
            f"style {result['openness']['style_delta']:+.2f}, "
            f"tactical {result['openness']['tactical_delta']:+.2f})"
        ),
        (
            f"- recent total-goal signal {result['openness']['recent_total_goals']:.2f}; "
            f"formation total-goal prior "
            f"{result['openness']['formation_total_goals'] if result['openness']['formation_total_goals'] is not None else 'n/a'}"
        ),
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
    openness_notes = result.get("openness", {}).get("notes") or []
    if openness_notes:
        lines.extend([f"- openness: {value}" for value in openness_notes])

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
