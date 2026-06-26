#!/usr/bin/env python3
"""Review completed matches against a saved exact-score odds analysis."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


GROUP_LABELS = {
    "home_win": "主胜",
    "draw": "平局",
    "away_win": "客胜",
}


def format_pct(value: float) -> str:
    if 0 < value < 0.001:
        return f"{value * 100:.4f}%"
    return f"{value * 100:.1f}%"


def format_float(value: float) -> str:
    return f"{value:.3f}"


def score_group(score: str) -> str:
    home_goals, away_goals = [int(part) for part in score.split(":", 1)]
    if home_goals > away_goals:
        return "home_win"
    if home_goals < away_goals:
        return "away_win"
    return "draw"


def match_key(home_team: str, away_team: str) -> str:
    return f"{home_team} vs {away_team}"


def load_results(path: Path) -> dict[str, dict]:
    items = json.loads(path.read_text(encoding="utf-8"))
    results = {}
    for item in items:
        score = f"{int(item['home_score'])}:{int(item['away_score'])}"
        key = match_key(item["home_team"], item["away_team"])
        results[key] = {
            **item,
            "match": key,
            "score": score,
            "group": score_group(score),
        }
    return results


def get_candidate(match: dict, score: str) -> dict | None:
    for candidate in match.get("candidates", []):
        if candidate.get("score") == score:
            return candidate
    return None


def ranked_candidate(match: dict, score: str) -> tuple[int | None, dict | None]:
    rows = sorted(match.get("candidates", []), key=lambda row: row.get("blended_probability", 0.0), reverse=True)
    for index, row in enumerate(rows, start=1):
        if row.get("score") == score:
            return index, row
    return None, None


def top_score_set(match: dict, limit: int) -> set[str]:
    rows = sorted(match.get("candidates", []), key=lambda row: row.get("blended_probability", 0.0), reverse=True)
    return {row.get("score", "") for row in rows[:limit]}


def favorite_group(match: dict) -> tuple[str, float]:
    wdl = match.get("blended_wdl") or {}
    if not wdl:
        return "unknown", 0.0
    group, probability = max(wdl.items(), key=lambda item: item[1])
    return group, float(probability)


def brier_score(wdl: dict, actual_group: str) -> float:
    return sum((float(wdl.get(group, 0.0)) - (1.0 if group == actual_group else 0.0)) ** 2 for group in GROUP_LABELS)


def log_loss(probability: float) -> float:
    return -math.log(max(min(probability, 1.0), 1e-12))


def actual_probability(match: dict, actual: dict) -> tuple[float, str]:
    candidate = get_candidate(match, actual["score"])
    if candidate:
        return float(candidate.get("blended_probability", 0.0)), "listed_score"
    group_probability = float((match.get("blended_wdl") or {}).get(actual["group"], 0.0))
    listed_group_probability = sum(
        float(candidate.get("blended_probability", 0.0))
        for candidate in match.get("candidates", [])
        if candidate.get("group") == actual["group"] and "其它" not in str(candidate.get("score", ""))
    )
    return max(group_probability - listed_group_probability, 0.0), "other_bucket_estimate"


def recommendation_score(match: dict) -> str | None:
    recommendation = match.get("recommended_score") or {}
    return recommendation.get("score")


def outcome_label(group: str) -> str:
    return GROUP_LABELS.get(group, group)


def analyze(review: dict) -> dict:
    matches = review["analysis"].get("matches", [])
    results = review["results"]
    rows = []
    totals = {
        "matches": 0,
        "favorite_hits": 0,
        "recommended_score_hits": 0,
        "top8_hits": 0,
        "actual_exact_probability": 0.0,
        "actual_wdl_probability": 0.0,
        "brier": 0.0,
        "log_loss": 0.0,
        "model_goals_error": 0.0,
        "recommended_goals_error": 0.0,
        "draw_actuals": 0,
        "favorite_draw_misses": 0,
        "home_under_calls": 0,
        "away_under_calls": 0,
    }
    for match in matches:
        actual = results.get(match["match"])
        if not actual:
            continue
        totals["matches"] += 1
        favorite, favorite_probability = favorite_group(match)
        recommended = recommendation_score(match)
        actual_prob, actual_prob_source = actual_probability(match, actual)
        actual_wdl_prob = float((match.get("blended_wdl") or {}).get(actual["group"], 0.0))
        rank, actual_candidate = ranked_candidate(match, actual["score"])
        top8_hit = actual["score"] in top_score_set(match, 8)
        recommendation_hit = recommended == actual["score"]
        favorite_hit = favorite == actual["group"]

        prediction = match.get("prediction") or {}
        lambda_a = float(prediction.get("lambda_a") or 0.0)
        lambda_b = float(prediction.get("lambda_b") or 0.0)
        actual_home = int(actual["home_score"])
        actual_away = int(actual["away_score"])
        model_goals_error = (abs(lambda_a - actual_home) + abs(lambda_b - actual_away)) / 2
        recommended_goals_error = None
        if recommended and "其它" not in recommended:
            rec_home, rec_away = [int(part) for part in recommended.split(":", 1)]
            recommended_goals_error = (abs(rec_home - actual_home) + abs(rec_away - actual_away)) / 2
            totals["recommended_goals_error"] += recommended_goals_error
            if rec_home < actual_home:
                totals["home_under_calls"] += 1
            if rec_away < actual_away:
                totals["away_under_calls"] += 1

        if favorite_hit:
            totals["favorite_hits"] += 1
        if recommendation_hit:
            totals["recommended_score_hits"] += 1
        if top8_hit:
            totals["top8_hits"] += 1
        if actual["group"] == "draw":
            totals["draw_actuals"] += 1
            if favorite != "draw":
                totals["favorite_draw_misses"] += 1
        totals["actual_exact_probability"] += actual_prob
        totals["actual_wdl_probability"] += actual_wdl_prob
        totals["brier"] += brier_score(match.get("blended_wdl") or {}, actual["group"])
        totals["log_loss"] += log_loss(actual_wdl_prob)
        totals["model_goals_error"] += model_goals_error

        rows.append(
            {
                "match": match["match"],
                "actual_score": actual["score"],
                "actual_group": actual["group"],
                "favorite_group": favorite,
                "favorite_probability": favorite_probability,
                "favorite_hit": favorite_hit,
                "recommended_score": recommended,
                "recommended_hit": recommendation_hit,
                "top8_hit": top8_hit,
                "actual_score_rank": rank,
                "actual_exact_probability": actual_prob,
                "actual_probability_source": actual_prob_source,
                "actual_market_probability": float((actual_candidate or {}).get("market_probability", 0.0) or 0.0),
                "actual_model_probability": float((actual_candidate or {}).get("model_probability", 0.0) or 0.0),
                "actual_wdl_probability": actual_wdl_prob,
                "lambda_a": lambda_a,
                "lambda_b": lambda_b,
                "model_goals_error": model_goals_error,
                "recommended_goals_error": recommended_goals_error,
                "raw_top_score": (match.get("recommended_score") or {}).get("raw_top_score"),
                "raw_top_group": (match.get("recommended_score") or {}).get("raw_top_group"),
                "recommendation_note": (match.get("recommended_score") or {}).get("analysis_note"),
            }
        )
    count = max(totals["matches"], 1)
    summary = {
        "matches": totals["matches"],
        "favorite_hit_rate": totals["favorite_hits"] / count,
        "recommended_score_hit_rate": totals["recommended_score_hits"] / count,
        "top8_hit_rate": totals["top8_hits"] / count,
        "average_actual_exact_probability": totals["actual_exact_probability"] / count,
        "average_actual_wdl_probability": totals["actual_wdl_probability"] / count,
        "average_brier_score": totals["brier"] / count,
        "average_log_loss": totals["log_loss"] / count,
        "average_model_goals_error": totals["model_goals_error"] / count,
        "average_recommended_goals_error": totals["recommended_goals_error"] / count,
        "draw_actuals": totals["draw_actuals"],
        "favorite_draw_misses": totals["favorite_draw_misses"],
        "home_under_calls": totals["home_under_calls"],
        "away_under_calls": totals["away_under_calls"],
    }
    return {"summary": summary, "matches": rows}


def issue_notes(result: dict) -> list[str]:
    summary = result["summary"]
    notes = []
    if summary["favorite_hit_rate"] < 0.55:
        notes.append("胜平负层对强弱边界仍偏脆弱，尤其在热门概率低于 55% 的场次，需要把平局保护和冷门上限写入推荐约束。")
    if summary["top8_hit_rate"] < 0.50:
        notes.append("精确比分分布覆盖不足，Top 8 对实际比分的覆盖率偏低，需要校准 0-0/1-1、2-1/1-2 与高比分尾部之间的质量分配。")
    if summary["favorite_draw_misses"] > 0:
        notes.append("平局被低估或被推荐规则绕开；当赔率平局很强、两队 WDL 差距小，应允许首选比分保持平局。")
    if summary["home_under_calls"] + summary["away_under_calls"] >= max(2, summary["matches"] // 2):
        notes.append("推荐比分对进球尾部仍有压缩倾向；开放度、强队尾部和 BTTS 信号需要在赛后优化中单独检查。")
    if not notes:
        notes.append("主要指标没有暴露单一严重偏差，但仍应按赛后样本继续滚动校准。")
    return notes


def write_markdown(result: dict, analysis_path: Path, results_path: Path) -> str:
    summary = result["summary"]
    lines = [
        f"数据口径：赛前分析 `{analysis_path}`；实际赛果 `{results_path}`。",
        "说明：这是赛后模型校准复盘，不是投注建议。",
        "",
        "## 1. 总览",
        "",
        "| 指标 | 数值 |",
        "|---|---:|",
        f"| 场次数 | {summary['matches']} |",
        f"| 胜平负倾向命中率 | {format_pct(summary['favorite_hit_rate'])} |",
        f"| 首选比分命中率 | {format_pct(summary['recommended_score_hit_rate'])} |",
        f"| 实际比分进入 Top 8 | {format_pct(summary['top8_hit_rate'])} |",
        f"| 实际比分平均混合概率 | {format_pct(summary['average_actual_exact_probability'])} |",
        f"| 实际胜平负平均概率 | {format_pct(summary['average_actual_wdl_probability'])} |",
        f"| 平均 Brier Score | {format_float(summary['average_brier_score'])} |",
        f"| 平均 Log Loss | {format_float(summary['average_log_loss'])} |",
        f"| lambda 平均绝对进球误差 | {format_float(summary['average_model_goals_error'])} |",
        f"| 首选比分平均绝对进球误差 | {format_float(summary['average_recommended_goals_error'])} |",
        "",
        "## 2. 单场复盘",
        "",
        "| 场次 | 实际 | 倾向 | 倾向概率 | 倾向命中 | 首选比分 | 比分命中 | Top8 | 实际比分排名 | 实际比分概率 | 实际WDL概率 | lambda |",
        "|---|---:|---|---:|---|---:|---|---|---:|---:|---:|---:|",
    ]
    for row in result["matches"]:
        rank = row["actual_score_rank"] if row["actual_score_rank"] is not None else "-"
        lines.append(
            f"| {row['match']} | {row['actual_score']} | {outcome_label(row['favorite_group'])} | "
            f"{format_pct(row['favorite_probability'])} | {'是' if row['favorite_hit'] else '否'} | "
            f"{row['recommended_score'] or '-'} | {'是' if row['recommended_hit'] else '否'} | "
            f"{'是' if row['top8_hit'] else '否'} | {rank} | "
            f"{format_pct(row['actual_exact_probability'])} | {format_pct(row['actual_wdl_probability'])} | "
            f"{row['lambda_a']:.2f}-{row['lambda_b']:.2f} |"
        )
    lines.extend(["", "## 3. 暴露的问题与优化方向", ""])
    for note in issue_notes(result):
        lines.append(f"- {note}")
    lines.extend(
        [
            "- 赛后优化应先写入真实赛果，再重建 `team_results`、`team_style_profiles`、`team_strength_snapshots` 和 `formation_matchup_stats`，最后用 `daily` 或 `smoke` grid 小步优化参数。",
            "- 发布层应区分“模型原始最高比分”和“为贴合 WDL 倾向而改写的推荐比分”；如果两者冲突，应在复盘中单独计数。",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--analysis-json", required=True, help="Saved JSON from analyze_score_odds_parlay.py.")
    parser.add_argument("--results-json", required=True, help="Completed match results JSON.")
    parser.add_argument("--format", choices=["json", "markdown"], default="markdown")
    parser.add_argument("--output", help="Optional output path.")
    args = parser.parse_args()

    analysis_path = Path(args.analysis_json)
    results_path = Path(args.results_json)
    review = {
        "analysis": json.loads(analysis_path.read_text(encoding="utf-8")),
        "results": load_results(results_path),
    }
    result = analyze(review)
    if args.format == "json":
        payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    else:
        payload = write_markdown(result, analysis_path, results_path)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")


if __name__ == "__main__":
    main()
