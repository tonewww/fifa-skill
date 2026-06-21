#!/usr/bin/env python3
"""Analyze exact-score odds and build four-leg parlay candidates."""

from __future__ import annotations

import argparse
import itertools
import json
import math
import sys
from pathlib import Path

from predict_match import calibrate_score_distribution, predict, raw_score_grid


DEFAULT_TEAM_MAP = {
    "荷兰": "NED",
    "瑞典": "SWE",
    "德国": "GER",
    "科特迪瓦": "CIV",
    "厄瓜多尔": "ECU",
    "库拉索": "CUW",
    "突尼斯": "TUN",
    "日本": "JPN",
}


def score_distribution(result: dict, max_goals: int = 10) -> dict[str, float]:
    params = result.get("model_parameters") or {}
    target_wdl = (result.get("score_calibration") or {}).get("target_wdl")
    lambda_a = float(result["lambda_a"])
    lambda_b = float(result["lambda_b"])
    if target_wdl and params:
        rows = raw_score_grid(lambda_a, lambda_b, max_goals)
        rows = calibrate_score_distribution(rows, target_wdl, params)
        return {row["score"].replace("-", ":"): row["probability"] for row in rows}
    rows = raw_score_grid(lambda_a, lambda_b, max_goals)
    return {row["score"].replace("-", ":"): row["probability"] for row in rows}


def flatten_odds(odds: dict) -> tuple[list[dict], dict[str, float]]:
    rows = []
    group_raw = {"home_win": 0.0, "draw": 0.0, "away_win": 0.0}
    raw_sum = 0.0
    for group, scores in odds.items():
        for score, decimal_odds in scores.items():
            raw = 1.0 / float(decimal_odds)
            raw_sum += raw
            group_raw[group] = group_raw.get(group, 0.0) + raw
            rows.append({"group": group, "score": score, "odds": float(decimal_odds), "raw": raw})
    market_wdl = {group: value / raw_sum for group, value in group_raw.items() if raw_sum > 0}
    for row in rows:
        row["market_probability"] = row["raw"] / raw_sum if raw_sum > 0 else 0.0
    return rows, market_wdl


def expected_value_fields(probability: float, odds: float, stake: float) -> dict:
    ev_multiplier = probability * odds
    return {
        "break_even_probability": 1.0 / odds if odds > 0 else 0.0,
        "edge_probability": probability - (1.0 / odds if odds > 0 else 0.0),
        "ev_multiplier": ev_multiplier,
        "profit_if_hit": stake * (odds - 1.0),
        "return_if_hit": stake * odds,
        "expected_return": stake * ev_multiplier,
        "expected_profit": stake * (ev_multiplier - 1.0),
        "roi": ev_multiplier - 1.0,
    }


def score_group(score: str) -> str | None:
    try:
        home_goals, away_goals = [int(part) for part in score.split(":", 1)]
    except ValueError:
        return None
    if home_goals > away_goals:
        return "home_win"
    if home_goals < away_goals:
        return "away_win"
    return "draw"


def recommendation_reason(favorite_group: str, raw_top_group: str | None, recommended_group: str | None) -> str:
    if raw_top_group == favorite_group:
        return "Raw top exact score already matches the blended WDL favorite."
    if recommended_group == favorite_group:
        return "Selected the highest-probability exact score inside the blended WDL favorite outcome group."
    return "No exact score inside the blended WDL favorite outcome group was available; fell back to raw top exact score."


def score_total_goals(score: str) -> int | None:
    if "其它" in score:
        return None
    try:
        home_goals, away_goals = [int(part) for part in score.split(":", 1)]
    except ValueError:
        return None
    return home_goals + away_goals


def score_parts(score: str) -> tuple[int, int] | None:
    if "其它" in score:
        return None
    try:
        home_goals, away_goals = [int(part) for part in score.split(":", 1)]
    except ValueError:
        return None
    return home_goals, away_goals


def winner_goals(score: str, group: str) -> int | None:
    parts = score_parts(score)
    if parts is None:
        return None
    home_goals, away_goals = parts
    if group == "home_win":
        return home_goals
    if group == "away_win":
        return away_goals
    if group == "draw":
        return home_goals
    return None


def openness_target_total(match: dict) -> float | None:
    openness = (match.get("prediction") or {}).get("openness") or {}
    total_delta = float(openness.get("total_delta") or 0.0)
    recent_total = openness.get("recent_total_goals")
    formation_total = openness.get("formation_total_goals")
    if total_delta < 0.35:
        return None
    signals = [float(value) for value in (recent_total, formation_total) if value is not None]
    if not signals:
        return None
    return max(3.0, min(4.4, sum(signals) / len(signals)))


def score_shape_context(match: dict, favorite_group: str) -> dict | None:
    if favorite_group == "draw":
        return None
    prediction = match.get("prediction") or {}
    openness = prediction.get("openness") or {}
    blended_wdl = match.get("blended_wdl") or {}
    market_wdl = match.get("market_wdl") or {}
    favorite_probability = float(blended_wdl.get(favorite_group) or 0.0)
    runner_up = max((float(value) for key, value in blended_wdl.items() if key != favorite_group), default=0.0)
    favorite_edge = favorite_probability - runner_up
    market_favorite_probability = float(market_wdl.get(favorite_group) or 0.0)
    total_delta = float(openness.get("total_delta") or 0.0)
    recent_total = openness.get("recent_total_goals")
    formation_total = openness.get("formation_total_goals")
    lambda_total = float(prediction.get("lambda_a") or 0.0) + float(prediction.get("lambda_b") or 0.0)
    signals = [float(value) for value in (recent_total, formation_total, lambda_total) if value is not None]

    if total_delta >= 0.18 or (recent_total is not None and float(recent_total) >= 3.0 and total_delta >= 0.08):
        target_total = max(3.0, min(4.4, sum(signals) / len(signals)))
        return {
            "kind": "open",
            "target_total": target_total,
            "minimum_total": 3,
            "minimum_winner_goals": 2,
            "minimum_probability_ratio": 0.70,
            "short_note": f"开放度偏高，按{outcome_label(favorite_group)}方向上调至更高总进球。",
        }

    if favorite_probability >= 0.54 or favorite_edge >= 0.22 or market_favorite_probability >= 0.75:
        return {
            "kind": "favorite_margin",
            "target_total": max(2.2, min(3.2, lambda_total)),
            "minimum_total": 2,
            "minimum_winner_goals": 2,
            "minimum_probability_ratio": 0.72,
            "short_note": f"{outcome_label(favorite_group)}优势较清晰，优先考虑2球起步的胜比分。",
        }

    if lambda_total >= 2.40 and favorite_probability >= 0.42:
        return {
            "kind": "balanced_goals",
            "target_total": max(2.7, min(3.4, lambda_total)),
            "minimum_total": 3,
            "minimum_winner_goals": 2,
            "minimum_probability_ratio": 0.76,
            "short_note": f"总进球中枢不低，选择{outcome_label(favorite_group)}方向内概率支撑足够的更高比分。",
        }

    return None


def score_shape_adjusted_recommendation(
    aligned: list[dict],
    raw_recommendation: dict,
    match: dict,
    favorite_group: str,
) -> tuple[dict, dict | None]:
    context = score_shape_context(match, favorite_group)
    if context is None or not aligned:
        return raw_recommendation, None
    top_probability = float(raw_recommendation.get("blended_probability", 0.0) or 0.0)
    if top_probability <= 0:
        return raw_recommendation, None
    target_total = float(context["target_total"])
    minimum_total = int(context["minimum_total"])
    minimum_winner_goals = int(context["minimum_winner_goals"])
    minimum_probability_ratio = float(context["minimum_probability_ratio"])
    viable = []
    for candidate in aligned:
        total_goals = score_total_goals(candidate["score"])
        if total_goals is None:
            continue
        probability = float(candidate.get("blended_probability", 0.0) or 0.0)
        if probability < top_probability * minimum_probability_ratio:
            continue
        if total_goals < minimum_total:
            continue
        goals_for_winner = winner_goals(candidate["score"], favorite_group)
        if goals_for_winner is None or goals_for_winner < minimum_winner_goals:
            continue
        closeness = abs(total_goals - target_total)
        probability_ratio = probability / top_probability
        margin_bonus = 0.04 * max(goals_for_winner - 1, 0)
        total_bonus = 0.04 * max(total_goals - 2, 0)
        shape_score = probability_ratio - 0.18 * closeness + margin_bonus + total_bonus
        viable.append((shape_score, probability, candidate))
    if not viable:
        return raw_recommendation, None
    viable.sort(key=lambda item: (item[0], item[1]), reverse=True)
    selected = viable[0][2]
    if selected.get("score") == raw_recommendation.get("score"):
        return raw_recommendation, None
    return selected, context


def recommendation_analysis_note(
    favorite_group: str,
    favorite_probability: float,
    raw_top: dict,
    recommendation: dict,
    tie_count: int,
    openness_adjusted: bool = False,
    target_total: float | None = None,
    shape_context: dict | None = None,
) -> str:
    favorite_label = outcome_label(favorite_group)
    recommended_group = recommendation.get("group")
    raw_top_score = raw_top.get("score")
    raw_top_group = raw_top.get("group")
    if shape_context and recommended_group == favorite_group:
        kind = shape_context.get("kind")
        if kind == "open" and target_total is not None:
            return (
                f"胜负倾向为{favorite_label}（{format_pct(favorite_probability)}）；"
                f"近期与阵型对位指向开放比赛（目标总进球约{target_total:.1f}），"
                f"因此在{favorite_label}方向内上调到更高总进球比分。"
            )
        if kind == "favorite_margin":
            return (
                f"胜负倾向为{favorite_label}（{format_pct(favorite_probability)}）；"
                "胜负优势与赔率结构支持2球起步的胜比分，因此避免机械落在1球小胜。"
            )
        if kind == "balanced_goals":
            return (
                f"胜负倾向为{favorite_label}（{format_pct(favorite_probability)}）；"
                "总进球中枢不低，且更高比分仍有足够概率支撑，因此上调首选比分。"
            )
    if openness_adjusted and target_total is not None and recommended_group == favorite_group:
        return (
            f"胜负倾向为{favorite_label}（{format_pct(favorite_probability)}）；"
            f"近期与阵型对位指向开放比赛（目标总进球约{target_total:.1f}），"
            f"因此在{favorite_label}方向内上调到更高总进球比分。"
        )
    if recommended_group != favorite_group:
        return (
            f"胜负倾向为{favorite_label}（{format_pct(favorite_probability)}），"
            "但缺少该方向可用比分，退回全表概率最高比分。"
        )
    if raw_top_score and raw_top_group and raw_top_group != favorite_group:
        return (
            f"胜负倾向为{favorite_label}（{format_pct(favorite_probability)}）；"
            f"原始最高比分 {raw_top_score} 偏{outcome_label(raw_top_group)}，"
            f"因此改取{favorite_label}方向内概率最高比分。"
        )
    if tie_count > 1:
        return (
            f"胜负倾向为{favorite_label}（{format_pct(favorite_probability)}）；"
            f"{favorite_label}方向内存在多个并列最高比分，全部保留展示。"
        )
    return (
        f"胜负倾向为{favorite_label}（{format_pct(favorite_probability)}），"
        f"首选比分同属{favorite_label}方向且为该方向内最高概率比分。"
    )


def recommended_score_candidates(match: dict, tie_tolerance: float = 0.0005) -> dict:
    favorite_group, favorite_probability = match_favorite_group(match)
    ranked = sorted(match["candidates"], key=lambda row: row["blended_probability"], reverse=True)
    raw_top = ranked[0] if ranked else {}
    aligned = [
        candidate
        for candidate in ranked
        if candidate["group"] == favorite_group and "其它" not in candidate["score"]
    ]
    raw_recommendation = aligned[0] if aligned else raw_top
    recommendation, shape_context = score_shape_adjusted_recommendation(aligned, raw_recommendation, match, favorite_group)
    target_total = float(shape_context["target_total"]) if shape_context else openness_target_total(match)
    openness_adjusted = bool(shape_context and shape_context.get("kind") == "open")
    score_shape_adjusted = recommendation.get("score") != raw_recommendation.get("score")
    top_probability = float(recommendation.get("blended_probability", 0.0) or 0.0)
    tied = [
        candidate
        for candidate in (aligned if aligned else ranked)
        if "其它" not in candidate["score"]
        and abs(float(candidate.get("blended_probability", 0.0) or 0.0) - top_probability) <= tie_tolerance
    ]
    if not tied and recommendation:
        tied = [recommendation]
    score_items = [
        {
            "score": candidate.get("score"),
            "group": candidate.get("group"),
            "model_probability": candidate.get("model_probability", 0.0),
            "market_probability": candidate.get("market_probability", 0.0),
            "blended_probability": candidate.get("blended_probability", 0.0),
            "odds": candidate.get("odds"),
        }
        for candidate in tied
    ]
    analysis_note = recommendation_analysis_note(
        favorite_group,
        favorite_probability,
        raw_top,
        recommendation,
        len(score_items),
        openness_adjusted,
        target_total,
        shape_context,
    )
    return {
        "favorite_group": favorite_group,
        "favorite_probability": favorite_probability,
        "openness_adjusted": openness_adjusted,
        "score_shape_adjusted": score_shape_adjusted,
        "selection_kind": shape_context.get("kind") if shape_context else None,
        "short_note": shape_context.get("short_note") if shape_context else None,
        "openness_target_total": target_total,
        "raw_top_score": raw_top.get("score"),
        "raw_top_group": raw_top.get("group"),
        "raw_recommended_score": raw_recommendation.get("score"),
        "score": recommendation.get("score"),
        "group": recommendation.get("group"),
        "scores": score_items,
        "tie_count": len(score_items),
        "tie_tolerance": tie_tolerance,
        "model_probability": recommendation.get("model_probability", 0.0),
        "market_probability": recommendation.get("market_probability", 0.0),
        "blended_probability": recommendation.get("blended_probability", 0.0),
        "odds": recommendation.get("odds"),
        "aligned_with_wdl": recommendation.get("group") == favorite_group,
        "reason": recommendation_reason(favorite_group, raw_top.get("group"), recommendation.get("group")),
        "analysis_note": analysis_note,
    }


def analyze_match(db_path: Path, item: dict, team_map: dict[str, str], market_weight: float, stake: float) -> dict:
    home_name = item["home_team"]
    away_name = item["away_team"]
    home_id = team_map.get(home_name, home_name)
    away_id = team_map.get(away_name, away_name)
    result = predict(db_path, home_id, away_id, "Group Stage", True, False)
    dist = score_distribution(result)
    flat, market_wdl = flatten_odds(item["odds"])
    model_weight = 1.0 - market_weight
    model_wdl = {
        "home_win": result["probabilities"]["team_a_win"],
        "draw": result["probabilities"]["draw"],
        "away_win": result["probabilities"]["team_b_win"],
    }
    blend_wdl = {
        group: model_weight * model_wdl[group] + market_weight * market_wdl[group]
        for group in ("home_win", "draw", "away_win")
    }
    model_group_probability = {"home_win": 0.0, "draw": 0.0, "away_win": 0.0}
    for score, probability in dist.items():
        group = score_group(score)
        if group:
            model_group_probability[group] += probability
    explicit_model_probability = {"home_win": 0.0, "draw": 0.0, "away_win": 0.0}
    for row in flat:
        group = score_group(row["score"])
        if group:
            explicit_model_probability[group] += dist.get(row["score"], 0.0)
    candidates = []
    for row in flat:
        if "其它" in row["score"]:
            model_probability = max(
                model_group_probability.get(row["group"], 0.0)
                - explicit_model_probability.get(row["group"], 0.0),
                0.0,
            )
        else:
            model_probability = dist.get(row["score"], 0.0)
        blended_probability = model_weight * model_probability + market_weight * row["market_probability"]
        candidate = {
            "match": f"{home_name} vs {away_name}",
            "home_team": home_name,
            "away_team": away_name,
            "score": row["score"],
            "odds": row["odds"],
            "group": row["group"],
            "model_probability": model_probability,
            "market_probability": row["market_probability"],
            "blended_probability": blended_probability,
            "value_proxy": blended_probability * row["odds"],
        }
        candidate.update(expected_value_fields(blended_probability, row["odds"], stake))
        candidates.append(candidate)
    candidates.sort(key=lambda row: (row["value_proxy"], row["blended_probability"], row["odds"]), reverse=True)
    match = {
        "match": f"{home_name} vs {away_name}",
        "home_team": home_name,
        "away_team": away_name,
        "prediction": result,
        "model_wdl": model_wdl,
        "market_wdl": market_wdl,
        "blended_wdl": blend_wdl,
        "candidates": candidates,
    }
    match["recommended_score"] = recommended_score_candidates(match)
    return match


def candidate_balance_score(candidate: dict, odds_power: float) -> float:
    return candidate["blended_probability"] * (candidate["odds"] ** odds_power)


def match_favorite_group(match: dict) -> tuple[str, float]:
    favorite_group, favorite_probability = max(
        match["blended_wdl"].items(),
        key=lambda item: item[1],
    )
    return favorite_group, favorite_probability


def match_favorite_edge(match: dict) -> float:
    probabilities = sorted(match["blended_wdl"].values(), reverse=True)
    if len(probabilities) < 2:
        return 0.0
    return probabilities[0] - probabilities[1]


def match_dominant_favorite(match: dict) -> bool:
    _favorite_group, favorite_probability = match_favorite_group(match)
    favorite_edge = match_favorite_edge(match)
    return favorite_probability >= 0.52 and favorite_edge >= 0.20


def eligible_candidates(
    match: dict,
    min_leg_probability: float,
    min_leg_value: float,
    mode: str,
    strong_favorite_threshold: float,
    strong_favorite_edge_threshold: float,
    restrict_clear_favorite: bool,
    odds_power: float,
) -> list[dict]:
    favorite_group, favorite_probability = match_favorite_group(match)
    favorite_edge = match_favorite_edge(match)
    eligible = [
        candidate
        for candidate in match["candidates"]
        if candidate["blended_probability"] >= min_leg_probability
        and candidate["value_proxy"] >= min_leg_value
    ]
    is_clear_favorite = (
        favorite_probability >= strong_favorite_threshold
        or favorite_edge >= strong_favorite_edge_threshold
    )
    if mode == "strength-aware" and is_clear_favorite and (restrict_clear_favorite or match_dominant_favorite(match)):
        favorite_eligible = [candidate for candidate in eligible if candidate["group"] == favorite_group]
        if favorite_eligible:
            eligible = favorite_eligible
        eligible.sort(
            key=lambda row: (
                candidate_balance_score(row, odds_power),
                row["blended_probability"],
                row["odds"],
            ),
            reverse=True,
        )
        return eligible
    if mode == "high-odds":
        eligible.sort(key=lambda row: (row["odds"], row["value_proxy"]), reverse=True)
    elif mode == "strength-aware":
        eligible.sort(
            key=lambda row: (
                row["value_proxy"],
                candidate_balance_score(row, odds_power),
                row["odds"],
            ),
            reverse=True,
        )
    else:
        eligible.sort(key=lambda row: (row["value_proxy"], row["odds"]), reverse=True)
    return eligible


def build_parlays(
    matches: list[dict],
    min_leg_probability: float,
    min_leg_value: float,
    top: int,
    mode: str,
    strong_favorite_threshold: float,
    strong_favorite_edge_threshold: float,
    restrict_clear_favorites: bool,
    max_clear_favorite_deviations: int | None,
    odds_power: float,
) -> list[dict]:
    leg_sets = []
    favorite_context = []
    for match in matches:
        favorite_group, favorite_probability = match_favorite_group(match)
        favorite_edge = match_favorite_edge(match)
        is_clear_favorite = (
            favorite_probability >= strong_favorite_threshold
            or favorite_edge >= strong_favorite_edge_threshold
            or match_dominant_favorite(match)
        )
        favorite_context.append((is_clear_favorite, favorite_group))
        eligible = eligible_candidates(
            match,
            min_leg_probability,
            min_leg_value,
            mode,
            strong_favorite_threshold,
            strong_favorite_edge_threshold,
            restrict_clear_favorites,
            odds_power,
        )
        leg_sets.append(eligible[:14])
    if any(not legs for legs in leg_sets):
        return []
    parlays = []
    for legs in itertools.product(*leg_sets):
        clear_favorite_deviations = sum(
            1
            for leg, (is_clear_favorite, favorite_group) in zip(legs, favorite_context)
            if is_clear_favorite and leg["group"] != favorite_group
        )
        if (
            max_clear_favorite_deviations is not None
            and clear_favorite_deviations > max_clear_favorite_deviations
        ):
            continue
        combined_odds = math.prod(leg["odds"] for leg in legs)
        combined_probability = math.prod(leg["blended_probability"] for leg in legs)
        value_proxy = combined_odds * combined_probability
        balance_score = combined_probability * (combined_odds ** odds_power)
        parlays.append(
            {
                "legs": list(legs),
                "combined_odds": combined_odds,
                "blended_probability": combined_probability,
                "value_proxy": value_proxy,
                "balance_score": balance_score,
                "clear_favorite_deviations": clear_favorite_deviations,
            }
        )
    if mode == "high-odds":
        parlays.sort(key=lambda row: (row["combined_odds"], row["value_proxy"]), reverse=True)
    elif mode == "strength-aware":
        parlays.sort(key=lambda row: (row["balance_score"], row["value_proxy"], row["combined_odds"]), reverse=True)
    else:
        parlays.sort(key=lambda row: (row["value_proxy"], row["combined_odds"]), reverse=True)
    return parlays[:top]


def parlay_signature(parlay: dict) -> tuple[tuple[str, str], ...]:
    return tuple((leg["match"], leg["score"]) for leg in parlay["legs"])


def parlay_pool(
    matches: list[dict],
    min_leg_probability: float,
    min_leg_value: float,
    strong_favorite_threshold: float,
    strong_favorite_edge_threshold: float,
    restrict_clear_favorites: bool,
    max_clear_favorite_deviations: int | None,
    odds_power: float,
) -> list[dict]:
    leg_sets = []
    favorite_context = []
    for match in matches:
        favorite_group, favorite_probability = match_favorite_group(match)
        favorite_edge = match_favorite_edge(match)
        is_clear_favorite = (
            favorite_probability >= strong_favorite_threshold
            or favorite_edge >= strong_favorite_edge_threshold
            or match_dominant_favorite(match)
        )
        favorite_context.append((is_clear_favorite, favorite_group))
        eligible = eligible_candidates(
            match,
            min_leg_probability,
            min_leg_value,
            "strength-aware",
            strong_favorite_threshold,
            strong_favorite_edge_threshold,
            restrict_clear_favorites,
            odds_power,
        )
        leg_sets.append(eligible[:14])
    if any(not legs for legs in leg_sets):
        return []

    parlays = []
    for legs in itertools.product(*leg_sets):
        clear_favorite_deviations = sum(
            1
            for leg, (is_clear_favorite, favorite_group) in zip(legs, favorite_context)
            if is_clear_favorite and leg["group"] != favorite_group
        )
        if (
            max_clear_favorite_deviations is not None
            and clear_favorite_deviations > max_clear_favorite_deviations
        ):
            continue
        combined_odds = math.prod(leg["odds"] for leg in legs)
        combined_probability = math.prod(leg["blended_probability"] for leg in legs)
        value_proxy = combined_odds * combined_probability
        balance_score = combined_probability * (combined_odds ** odds_power)
        parlays.append(
            {
                "legs": list(legs),
                "combined_odds": combined_odds,
                "blended_probability": combined_probability,
                "value_proxy": value_proxy,
                "balance_score": balance_score,
                "clear_favorite_deviations": clear_favorite_deviations,
            }
        )
    return parlays


def enrich_parlays(parlays: list[dict], stake: float) -> list[dict]:
    for parlay in parlays:
        parlay.update(expected_value_fields(parlay["blended_probability"], parlay["combined_odds"], stake))
    return parlays


def split_parlays(
    matches: list[dict],
    probability_min_leg_probability: float,
    probability_min_leg_value: float,
    odds_min_leg_probability: float,
    odds_min_leg_value: float,
    ev_min_leg_probability: float,
    ev_min_leg_value: float,
    strong_favorite_threshold: float,
    strong_favorite_edge_threshold: float,
    odds_max_clear_favorite_deviations: int | None,
    ev_max_clear_favorite_deviations: int | None,
    odds_power: float,
    per_group: int = 3,
) -> dict[str, list[dict]]:
    probability_pool = parlay_pool(
        matches,
        probability_min_leg_probability,
        probability_min_leg_value,
        strong_favorite_threshold,
        strong_favorite_edge_threshold,
        True,
        0,
        odds_power,
    )
    enrich_parlays(probability_pool, 1.0)
    probability_pool.sort(
        key=lambda row: (row["blended_probability"], row["balance_score"], row["value_proxy"]),
        reverse=True,
    )
    probability_first = probability_pool[:per_group]

    seen = {parlay_signature(parlay) for parlay in probability_first}
    odds_pool = parlay_pool(
        matches,
        odds_min_leg_probability,
        odds_min_leg_value,
        strong_favorite_threshold,
        strong_favorite_edge_threshold,
        False,
        odds_max_clear_favorite_deviations,
        odds_power,
    )
    enrich_parlays(odds_pool, 1.0)
    odds_pool = [parlay for parlay in odds_pool if parlay_signature(parlay) not in seen]
    odds_pool.sort(
        key=lambda row: (row["combined_odds"], row["balance_score"], row["blended_probability"]),
        reverse=True,
    )
    odds_first = odds_pool[:per_group]

    seen.update(parlay_signature(parlay) for parlay in odds_first)
    ev_pool = parlay_pool(
        matches,
        ev_min_leg_probability,
        ev_min_leg_value,
        strong_favorite_threshold,
        strong_favorite_edge_threshold,
        False,
        ev_max_clear_favorite_deviations,
        odds_power,
    )
    enrich_parlays(ev_pool, 1.0)
    ev_pool = [parlay for parlay in ev_pool if parlay_signature(parlay) not in seen]
    ev_pool.sort(
        key=lambda row: (row["expected_profit"], row["roi"], row["balance_score"], row["blended_probability"]),
        reverse=True,
    )
    return {
        "probability_first": probability_first,
        "odds_first": odds_first,
        "expected_value_first": ev_pool[:per_group],
    }


def format_pct(value: float) -> str:
    if 0 < value < 0.001:
        return f"{value * 100:.4f}%"
    return f"{value * 100:.1f}%"


def format_money(value: float) -> str:
    if abs(value) >= 100:
        return f"{value:.2f}"
    if abs(value) >= 1:
        return f"{value:.3f}"
    return f"{value:.4f}"


def md_cell(value: object) -> str:
    return str(value).replace("|", "\\|")


def outcome_label(group: str) -> str:
    return {
        "home_win": "主胜",
        "draw": "平局",
        "away_win": "客胜",
    }.get(group, group)


def score_rows(match: dict, limit: int = 8, show_all: bool = False) -> list[dict]:
    rows = sorted(match["candidates"], key=lambda row: row["blended_probability"], reverse=True)
    if show_all:
        return rows
    return rows[:limit]


def parlay_legs_text(parlay: dict) -> str:
    return " / ".join(f"{leg['match']} {leg['score']}" for leg in parlay["legs"])


def recommended_scores_text(match: dict) -> str:
    recommendation = match.get("recommended_score") or {}
    scores = [item.get("score") for item in recommendation.get("scores", []) if item.get("score")]
    if not scores and recommendation.get("score"):
        scores = [recommendation["score"]]
    return " / ".join(scores) if scores else "-"


def recommended_score_probability(match: dict) -> float:
    recommendation = match.get("recommended_score") or {}
    scores = [item for item in recommendation.get("scores", []) if item.get("score")]
    if scores:
        return float(scores[0].get("blended_probability") or 0.0)
    return float(recommendation.get("blended_probability") or 0.0)


def write_markdown(result: dict) -> str:
    lines = [
        f"数据口径：{result['odds_path']}；混合概率 = 模型 {format_pct(1 - result['market_weight'])} + 赔率隐含 {format_pct(result['market_weight'])}。",
        (
            f"资金口径：每注 {format_money(result['stake'])} 单位；预期返还 = 概率 × 赔率 × 每注，"
            "预期净收益 = 预期返还 - 每注，ROI = 概率 × 赔率 - 1。"
        ),
        "说明：输出为概率和期望值分析，不是投注建议；精确比分 4 串 1 的单一命中率天然很低。",
        "",
        "## 1. 各场次的胜负关系",
        "",
        "| 场次 | 主胜 | 平局 | 客胜 | 倾向 | 倾向概率 |",
        "|---|---:|---:|---:|---|---:|",
    ]
    for match in result["matches"]:
        wdl = match["blended_wdl"]
        favorite_group, favorite_probability = match_favorite_group(match)
        lines.append(
            f"| {md_cell(match['match'])} | {format_pct(wdl['home_win'])} | "
            f"{format_pct(wdl['draw'])} | {format_pct(wdl['away_win'])} | "
            f"{outcome_label(favorite_group)} | {format_pct(favorite_probability)} |"
        )
    score_title = "## 2. 各场次的完整比分概率与预期收入" if result["show_all_scores"] else "## 2. 各场次的比分预测 Top 8 与预期收入"
    lines.extend(["", score_title])
    for match in result["matches"]:
        recommendation = match.get("recommended_score") or {}
        lines.extend(
            [
                "",
                f"### {match['match']}",
                "",
                (
                    f"推荐说明：首选比分 {md_cell(recommended_scores_text(match))}"
                    f"（混合概率 {format_pct(recommended_score_probability(match))}）；"
                    f"{md_cell(recommendation.get('analysis_note', '按胜负倾向内最高概率比分输出。'))}"
                ),
                "",
                "| 序号 | 比分 | 结果 | 推荐 | 模型概率 | 赔率隐含 | 混合概率 | 赔率 | 盈亏平衡 | 命中返还 | 预期返还 | 预期净收益 | ROI |",
                "|---:|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        recommended_scores = {
            item.get("score")
            for item in match.get("recommended_score", {}).get("scores", [])
            if item.get("score")
        }
        if not recommended_scores and match.get("recommended_score", {}).get("score"):
            recommended_scores = {match["recommended_score"]["score"]}
        for index, row in enumerate(score_rows(match, result["score_table_limit"], result["show_all_scores"]), start=1):
            marker = "胜负一致首选" if row["score"] in recommended_scores else ""
            lines.append(
                f"| {index} | {md_cell(row['score'])} | {outcome_label(row['group'])} | "
                f"{marker} | "
                f"{format_pct(row['model_probability'])} | {format_pct(row['market_probability'])} | "
                f"{format_pct(row['blended_probability'])} | {row['odds']:.2f} | "
                f"{format_pct(row['break_even_probability'])} | {format_money(row['return_if_hit'])} | "
                f"{format_money(row['expected_return'])} | {format_money(row['expected_profit'])} | "
                f"{format_pct(row['roi'])} |"
            )
    lines.extend(["", "## 3. 4 串 1 的预测 Top 9"])
    lines.extend(
        [
            "",
            "### 前 3 个：胜率优先，赔率其次",
            "",
            "| 序号 | 串关比分 | 热门偏离 | 综合赔率 | 估算命中率 | 命中返还 | 预期返还 | 预期净收益 | ROI | 平衡分 |",
            "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for index, parlay in enumerate(result["parlay_groups"]["probability_first"], start=1):
        lines.append(
            f"| {index} | {md_cell(parlay_legs_text(parlay))} | {parlay.get('clear_favorite_deviations', 0)} | {parlay['combined_odds']:.2f} | "
            f"{format_pct(parlay['blended_probability'])} | {format_money(parlay['return_if_hit'])} | "
            f"{format_money(parlay['expected_return'])} | {format_money(parlay['expected_profit'])} | "
            f"{format_pct(parlay['roi'])} | {parlay['balance_score']:.6f} |"
        )
    lines.extend(
        [
            "",
            "### 后 3 个：赔率优先，胜率保持中等门槛",
            "",
            "| 序号 | 串关比分 | 热门偏离 | 综合赔率 | 估算命中率 | 命中返还 | 预期返还 | 预期净收益 | ROI | 平衡分 |",
            "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for index, parlay in enumerate(result["parlay_groups"]["odds_first"], start=4):
        lines.append(
            f"| {index} | {md_cell(parlay_legs_text(parlay))} | {parlay.get('clear_favorite_deviations', 0)} | {parlay['combined_odds']:.2f} | "
            f"{format_pct(parlay['blended_probability'])} | {format_money(parlay['return_if_hit'])} | "
            f"{format_money(parlay['expected_return'])} | {format_money(parlay['expected_profit'])} | "
            f"{format_pct(parlay['roi'])} | {parlay['balance_score']:.6f} |"
        )
    lines.extend(
        [
            "",
            "### 期望收益最高 3 个：EV 优先，高方差",
            "",
            "| 序号 | 串关比分 | 热门偏离 | 综合赔率 | 估算命中率 | 命中返还 | 预期返还 | 预期净收益 | ROI | 平衡分 |",
            "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for index, parlay in enumerate(result["parlay_groups"]["expected_value_first"], start=7):
        lines.append(
            f"| {index} | {md_cell(parlay_legs_text(parlay))} | {parlay.get('clear_favorite_deviations', 0)} | {parlay['combined_odds']:.2f} | "
            f"{format_pct(parlay['blended_probability'])} | {format_money(parlay['return_if_hit'])} | "
            f"{format_money(parlay['expected_return'])} | {format_money(parlay['expected_profit'])} | "
            f"{format_pct(parlay['roi'])} | {parlay['balance_score']:.6f} |"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, help="SQLite database path.")
    parser.add_argument("--odds-json", required=True, help="Exact-score odds JSON file.")
    parser.add_argument("--team-map-json", help="Optional JSON object mapping odds names to team ids.")
    parser.add_argument("--market-weight", type=float, default=0.30, help="Weight for odds-implied probabilities.")
    parser.add_argument("--stake", type=float, default=1.0, help="Stake unit used for expected return/profit calculations.")
    parser.add_argument("--score-table-limit", type=int, default=8, help="Rows per match when not showing all scores.")
    parser.add_argument("--show-all-scores", action="store_true", help="Show every exact-score row from the odds table.")
    parser.add_argument("--min-leg-probability", type=float, default=0.04)
    parser.add_argument("--min-leg-value", type=float, default=0.55)
    parser.add_argument("--top", type=int, default=6)
    parser.add_argument("--mode", choices=["high-odds", "value", "strength-aware"], default="strength-aware")
    parser.add_argument(
        "--strong-favorite-threshold",
        type=float,
        default=0.55,
        help="In strength-aware mode, restrict strong-favorite matches to the favored outcome group above this blended WDL probability.",
    )
    parser.add_argument(
        "--strong-favorite-edge-threshold",
        type=float,
        default=0.16,
        help="Also restrict strength-aware matches when the favored WDL probability exceeds the runner-up by this margin.",
    )
    parser.add_argument(
        "--odds-power",
        type=float,
        default=0.35,
        help="Odds exponent for probability/odds balance score in strength-aware mode.",
    )
    parser.add_argument("--probability-first-min-leg-probability", type=float, default=0.07)
    parser.add_argument("--probability-first-min-leg-value", type=float, default=0.45)
    parser.add_argument("--odds-first-min-leg-probability", type=float, default=0.04)
    parser.add_argument("--odds-first-min-leg-value", type=float, default=0.45)
    parser.add_argument("--ev-first-min-leg-probability", type=float, default=0.04)
    parser.add_argument("--ev-first-min-leg-value", type=float, default=0.45)
    parser.add_argument(
        "--odds-first-max-clear-favorite-deviations",
        type=int,
        default=1,
        help="Maximum clear-favorite outcome deviations allowed in the odds-first four-leg group.",
    )
    parser.add_argument(
        "--ev-first-max-clear-favorite-deviations",
        type=int,
        default=2,
        help="Maximum clear-favorite outcome deviations allowed in the EV-first four-leg group.",
    )
    parser.add_argument("--format", choices=["json", "markdown"], default="markdown")
    parser.add_argument("--output", help="Optional path to write the JSON or Markdown result.")
    args = parser.parse_args()

    odds_path = Path(args.odds_json)
    odds_items = json.loads(odds_path.read_text(encoding="utf-8"))
    team_map = DEFAULT_TEAM_MAP.copy()
    if args.team_map_json:
        team_map.update(json.loads(Path(args.team_map_json).read_text(encoding="utf-8")))
    matches = [analyze_match(Path(args.db), item, team_map, args.market_weight, args.stake) for item in odds_items]
    parlays = build_parlays(
        matches,
        args.min_leg_probability,
        args.min_leg_value,
        args.top,
        args.mode,
        args.strong_favorite_threshold,
        args.strong_favorite_edge_threshold,
        args.mode == "strength-aware",
        None,
        args.odds_power,
    )
    enrich_parlays(parlays, args.stake)
    parlay_groups = split_parlays(
        matches,
        args.probability_first_min_leg_probability,
        args.probability_first_min_leg_value,
        args.odds_first_min_leg_probability,
        args.odds_first_min_leg_value,
        args.ev_first_min_leg_probability,
        args.ev_first_min_leg_value,
        args.strong_favorite_threshold,
        args.strong_favorite_edge_threshold,
        args.odds_first_max_clear_favorite_deviations,
        args.ev_first_max_clear_favorite_deviations,
        args.odds_power,
        3,
    )
    for group in parlay_groups.values():
        enrich_parlays(group, args.stake)
    result = {
        "odds_path": str(odds_path),
        "market_weight": args.market_weight,
        "stake": args.stake,
        "score_table_limit": args.score_table_limit,
        "show_all_scores": args.show_all_scores,
        "min_leg_probability": args.min_leg_probability,
        "min_leg_value": args.min_leg_value,
        "mode": args.mode,
        "strong_favorite_threshold": args.strong_favorite_threshold,
        "strong_favorite_edge_threshold": args.strong_favorite_edge_threshold,
        "odds_first_max_clear_favorite_deviations": args.odds_first_max_clear_favorite_deviations,
        "ev_first_max_clear_favorite_deviations": args.ev_first_max_clear_favorite_deviations,
        "odds_power": args.odds_power,
        "matches": matches,
        "parlays": parlays,
        "parlay_groups": parlay_groups,
    }
    if args.format == "json":
        payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    else:
        payload = write_markdown(result) + "\n"
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")


if __name__ == "__main__":
    main()
