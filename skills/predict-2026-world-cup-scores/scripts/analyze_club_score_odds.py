#!/usr/bin/env python3
"""Analyze a club exact-score odds slate from source-verified low-sample context.

This fallback deliberately does not resolve club names to World Cup national-team
IDs. It accepts an archived club context with a transparent Poisson prior, then
uses the same exact-score market blend, expected-value fields, and cap-aware
parlay selection as the national-team odds analyzer.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from analyze_score_odds_parlay import (
    PARLAY_ODDS_CAPS,
    build_parlays,
    expected_value_fields,
    flatten_odds,
    normalize_probabilities,
    parlay_odds_cap,
    recommended_score_candidates,
    relationship_context,
    score_group,
    split_parlays,
    enrich_parlays,
    write_markdown,
)
from predict_match import raw_score_grid


OUTCOME_GROUPS = ("home_win", "draw", "away_win")


def load_odds_items(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and "home_team" in payload and "away_team" in payload:
        return [payload]
    if isinstance(payload, dict) and isinstance(payload.get("matches"), list):
        return payload["matches"]
    if isinstance(payload, list):
        return payload
    raise ValueError("Odds JSON must be a match object, a list, or an object with a matches list.")


def source_context_by_match(context: dict) -> dict[tuple[str, str], dict]:
    rows = context.get("matches")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Context JSON must contain a non-empty matches list.")
    indexed: dict[tuple[str, str], dict] = {}
    for row in rows:
        try:
            key = (str(row["home_team"]), str(row["away_team"]))
            model = row["model"]
            if float(model["home_lambda"]) <= 0 or float(model["away_lambda"]) <= 0:
                raise ValueError("Expected-goal lambdas must be positive.")
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid club context row: {row!r}") from exc
        if key in indexed:
            raise ValueError(f"Duplicate club context for {key[0]} vs {key[1]}.")
        indexed[key] = row
    return indexed


def model_distribution(
    home_lambda: float,
    away_lambda: float,
    shape: dict | None,
) -> tuple[dict[str, float], dict[str, float], list[dict]]:
    rows = raw_score_grid(home_lambda, away_lambda, max_goals=10)
    shape = shape or {}
    nil_nil_multiplier = float(shape.get("nil_nil_multiplier") or 1.0)
    btts_multiplier = float(shape.get("btts_multiplier") or 1.0)
    if nil_nil_multiplier <= 0 or btts_multiplier <= 0:
        raise ValueError("Score-shape multipliers must be positive.")
    weighted_distribution: dict[str, float] = {}
    for row in rows:
        score = row["score"].replace("-", ":")
        home_goals, away_goals = (int(part) for part in score.split(":", 1))
        multiplier = 1.0
        if home_goals == 0 and away_goals == 0:
            multiplier *= nil_nil_multiplier
        elif home_goals > 0 and away_goals > 0:
            multiplier *= btts_multiplier
        weighted_distribution[score] = float(row["probability"]) * multiplier
    distribution = normalize_probabilities(weighted_distribution)
    outcome_probabilities = {group: 0.0 for group in OUTCOME_GROUPS}
    top_scorelines: list[dict] = []
    for score, probability in distribution.items():
        group = score_group(score)
        if group:
            outcome_probabilities[group] += probability
            top_scorelines.append({"score": score.replace(":", "-"), "group": group, "probability": probability})
    top_scorelines.sort(key=lambda row: row["probability"], reverse=True)
    return distribution, normalize_probabilities(outcome_probabilities), top_scorelines[:8]


def candidate_rows(
    odds: dict,
    distribution: dict[str, float],
    model_wdl: dict[str, float],
    market_weight: float,
    stake: float,
    match_name: str,
    home_name: str,
    away_name: str,
) -> tuple[list[dict], dict[str, float]]:
    flat, market_wdl = flatten_odds(odds)
    explicit_model_probability = {group: 0.0 for group in OUTCOME_GROUPS}
    for row in flat:
        group = score_group(row["score"])
        if group:
            explicit_model_probability[group] += distribution.get(row["score"], 0.0)

    model_weight = 1.0 - market_weight
    candidates: list[dict] = []
    for row in flat:
        if "其它" in row["score"]:
            model_probability = max(
                model_wdl.get(row["group"], 0.0) - explicit_model_probability.get(row["group"], 0.0),
                0.0,
            )
        else:
            model_probability = distribution.get(row["score"], 0.0)
        blended_probability = model_weight * model_probability + market_weight * row["market_probability"]
        candidate = {
            "match": match_name,
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
    return candidates, market_wdl


def availability_text(context_row: dict) -> str:
    availability = context_row.get("availability") or {}
    verified = availability.get("verified") or []
    unknown = availability.get("unknown") or []
    parts = []
    if verified:
        parts.append("已核实：" + "；".join(str(item) for item in verified))
    if unknown:
        parts.append("待确认：" + "；".join(str(item) for item in unknown))
    return " ".join(parts)


def analyze_match(item: dict, context_row: dict, market_weight: float, stake: float, stage: str) -> dict:
    home_name = str(item["home_team"])
    away_name = str(item["away_team"])
    match_name = f"{home_name} vs {away_name}"
    model = context_row["model"]
    home_lambda = float(model["home_lambda"])
    away_lambda = float(model["away_lambda"])
    distribution, model_wdl, top_scorelines = model_distribution(home_lambda, away_lambda, model.get("shape"))
    candidates, market_wdl = candidate_rows(
        item["odds"],
        distribution,
        model_wdl,
        market_weight,
        stake,
        match_name,
        home_name,
        away_name,
    )
    blended_wdl = normalize_probabilities(
        {
            group: (1.0 - market_weight) * model_wdl[group] + market_weight * market_wdl[group]
            for group in OUTCOME_GROUPS
        }
    )
    first_leg = context_row.get("first_leg") or {}
    factors = [str(item) for item in model.get("factors") or []]
    prediction = {
        "prediction_id": f"club-{str(first_leg.get('event_id') or match_name).lower().replace(' ', '-')}",
        "team_a": {"team_id": context_row.get("canonical_home_team", home_name), "name": context_row.get("canonical_home_team", home_name)},
        "team_b": {"team_id": context_row.get("canonical_away_team", away_name), "name": context_row.get("canonical_away_team", away_name)},
        "stage": stage,
        "neutral_site": False,
        "lambda_a": home_lambda,
        "lambda_b": away_lambda,
        "probabilities": {
            "team_a_win": model_wdl["home_win"],
            "draw": model_wdl["draw"],
            "team_b_win": model_wdl["away_win"],
        },
        "top_scorelines": top_scorelines,
        "openness": {
            "total_delta": float(model.get("openness_total_delta") or 0.0),
            "notes": factors,
        },
        "club_low_sample": True,
        "first_leg": first_leg,
        "availability": context_row.get("availability") or {},
        "model_factors": factors,
        "score_shape": model.get("shape") or {},
    }
    match = {
        "match": match_name,
        "home_team": home_name,
        "away_team": away_name,
        "prediction": prediction,
        "model_wdl": model_wdl,
        "market_wdl": market_wdl,
        "blended_wdl": blended_wdl,
        "candidates": candidates,
        "market_weight": market_weight,
        "data_readiness_market_penalty": 0.0,
        "data_readiness_market_weight_if_enabled": market_weight,
        "series_context": {
            "table_text": str(context_row.get("series_context_text") or "两回合赛制信息不足。"),
        },
        "tactical_adjustment": {
            "wdl_multipliers": {group: 1.0 for group in OUTCOME_GROUPS},
            "report_note": str(context_row.get("report_note") or ""),
        },
        "club_context": context_row,
    }
    match["recommended_score"] = recommended_score_candidates(match)
    match["relationship"] = relationship_context(match)
    return match


def result_payload(
    odds_path: Path,
    context_path: Path,
    context: dict,
    matches: list[dict],
    market_weight: float,
    stake: float,
    score_table_limit: int,
    parlay_groups: dict,
) -> dict:
    stage = str(context.get("stage") or "Club qualifying round")
    source_urls = [str(url) for url in context.get("source_urls") or []]
    limitations = [
        "未把俱乐部映射为国家队，也未调用世界杯国家队实力快照。",
        "模型使用已验证的首回合、阵型、总比分和可用性信息；缺失的近期联赛战绩、xG、伤病和官方次回合首发不做伪造。",
        "所有比分、概率和串关结算均为90分钟口径，晋级/加时/点球不作为精确比分命中。",
    ]
    return {
        "odds_path": str(odds_path),
        "context_path": str(context_path),
        "competition_label": str(context.get("competition_label") or "俱乐部资格赛"),
        "competition": str(context.get("competition") or "Club qualifying"),
        "stage": stage,
        "retrieved_on": context.get("retrieved_on"),
        "source_urls": source_urls,
        "model_scope": context.get("model_scope"),
        "relationship_context_header": "两回合/赛前因子",
        "market_weight": market_weight,
        "stake": stake,
        "score_table_limit": score_table_limit,
        "show_all_scores": False,
        "mode": "club-context-strength-aware",
        "analysis_date": context.get("retrieved_on"),
        "parlay_odds_cap": parlay_odds_cap(len(matches)),
        "parlay_effective_odds_note": (
            "2串1未配置额外结算赔率封顶；3串1单注结算赔率封顶100000倍；"
            "4串1单注结算赔率封顶250000倍。"
        ),
        "report_prelude": [
            f"赛制口径：{stage}；本报告只计算单场90分钟精确比分，首回合总比分仅作为战术/进球分布输入。",
            f"来源：{'; '.join(source_urls)}",
            "信息限制：" + " ".join(limitations),
        ],
        "matches": matches,
        "parlays": (
            parlay_groups["probability_first"]
            + parlay_groups["odds_first"]
            + parlay_groups["expected_value_first"]
        ),
        "parlay_groups": parlay_groups,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--odds-json", required=True, help="Exact-score odds JSON.")
    parser.add_argument("--context-json", required=True, help="Source-verified club context JSON.")
    parser.add_argument("--market-weight", type=float, default=0.30, help="Odds-implied probability weight.")
    parser.add_argument("--stake", type=float, default=100.0, help="Stake unit for EV calculations.")
    parser.add_argument("--score-table-limit", type=int, default=8)
    parser.add_argument("--top", type=int, default=9)
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    parser.add_argument("--output", help="Optional output path.")
    args = parser.parse_args()

    if not 0.0 <= args.market_weight <= 1.0:
        raise SystemExit("--market-weight must be between 0 and 1.")
    if args.stake <= 0:
        raise SystemExit("--stake must be positive.")
    if args.top != 9:
        raise SystemExit("Club reports keep the fixed Top 9 layout: three groups of three.")

    odds_path = Path(args.odds_json)
    context_path = Path(args.context_json)
    odds_items = load_odds_items(odds_path)
    context = json.loads(context_path.read_text(encoding="utf-8"))
    context_by_match = source_context_by_match(context)
    missing_context = [
        f"{item.get('home_team')} vs {item.get('away_team')}"
        for item in odds_items
        if (str(item.get("home_team")), str(item.get("away_team"))) not in context_by_match
    ]
    if missing_context:
        raise SystemExit("Missing source-verified club context for: " + ", ".join(missing_context))

    stage = str(context.get("stage") or "Club qualifying round")
    matches = [
        analyze_match(
            item,
            context_by_match[(str(item["home_team"]), str(item["away_team"]))],
            args.market_weight,
            args.stake,
            stage,
        )
        for item in odds_items
    ]
    parlay_groups = split_parlays(
        matches,
        probability_min_leg_probability=0.07,
        probability_min_leg_value=0.45,
        odds_min_leg_probability=0.01,
        odds_min_leg_value=0.75,
        ev_min_leg_probability=0.001,
        ev_min_leg_value=0.80,
        strong_favorite_threshold=0.58,
        strong_favorite_edge_threshold=0.16,
        odds_max_clear_favorite_deviations=1,
        ev_max_clear_favorite_deviations=2,
        odds_power=0.35,
        per_group=3,
    )
    for group in parlay_groups.values():
        enrich_parlays(group, args.stake, 0.35)
    result = result_payload(
        odds_path,
        context_path,
        context,
        matches,
        args.market_weight,
        args.stake,
        args.score_table_limit,
        parlay_groups,
    )
    payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n" if args.format == "json" else write_markdown(result) + "\n"
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")


if __name__ == "__main__":
    main()
