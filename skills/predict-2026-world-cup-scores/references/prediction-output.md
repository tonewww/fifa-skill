# Prediction Output

## Required Structure

When answering a user asking for a two-team prediction, use this order:

1. Data freshness and caveat.
2. Win/draw/loss analysis.
3. Top 8 scoreline probabilities.
4. Strength comparison, including FIFA ranking if available.
5. Lineups and availability.
6. Tactical arrangement and formation matchup.
7. Matchup counters / 克制关系.
8. Key uncertainty factors.
9. Final predicted score with confidence level.

Keep the answer concise unless the user asks for a deep report.

## Example

```markdown
数据口径：已刷新 FIFA 官方赛程和名单；伤病信息截至 2026-06-20 09:00 UTC。官方首发未公布，因此首发相关不确定性偏高。

| 结果 | 概率 |
|---|---:|
| Team A 胜 | 42.8% |
| 平局 | 27.6% |
| Team B 胜 | 29.6% |

Top 8 比分概率：1-1（12.4%）、1-0（10.8%）、2-1（9.6%）、0-0（8.7%）、0-1（8.1%）、2-0（7.6%）、1-2（7.1%）、2-2（5.8%）。

实力表：Team A 综合 76.2，进攻 78.4，防守 74.1；Team B 综合 73.8，进攻 75.0，防守 72.8。

首发与伤病：Team A 官方首发未公布，预计 4-3-3；主力中卫 doubtful。Team B 预计 4-2-3-1，暂无重大伤停。

战术与阵型：4-3-3 vs 4-2-3-1 的历史样本偏向 Team A，但样本较小，仅作为轻权重先验。Team A 高压迫和边路推进可能压制 Team B 的第一出球点。

克制关系：Team A 的前场压迫对 Team B 后场出球有优势，但 Team B 的快速反击会考验 Team A 的高位防线。定位球端 Team B 有轻微优势。

关键不确定性：Team A 主力中卫状态、Team B 中锋首发概率、比赛地天气。

预测：Team A 2-1 Team B。信心：中低。单一比分本身概率不高，比分应理解为分布中心。
```

## Confidence Labels

Use these labels:

- `高`: current official lineups, low injury uncertainty, strong model edge, well-populated data.
- `中`: good team/player data, some lineup or tactical uncertainty.
- `中低`: missing official lineups, moderate injuries, similar team strength, or sparse advanced data.
- `低`: stale data, major unknowns, missing squads, or no style profile.

## Probability Formatting

- Use one decimal place for user-facing percentages.
- Use exact JSON from `predict_match.py` only when the user asks for raw output.
- Clarify whether probabilities are regulation-time probabilities. If predicting knockout advancement, build a separate extra-time/penalty model.

## Scoreline Language

Recommended:

- "最可能的单一比分是 1-1，但它只有约 12% 概率。"
- "比分预测倾向 2-1，胜平负分布更重要。"
- "如果官方首发显示主力前锋缺阵，Team A 的 lambda 应下调。"
- "先看胜平负概率，再看 top 8 比分概率。"

Avoid:

- "一定会"
- "锁定"
- "稳赢"
- "100%"

## Deep Report Add-ons

When the user asks for a full report, add:

- Projected lineups and role weights.
- Player-by-player impact table.
- Recent form table.
- FIFA ranking and ranking-point trend.
- xG for/against trend when available.
- Injury/suspension availability table.
- Tactical plan by phase: buildup, press, transition, set piece.
- Formation matchup backtest sample and top historical scorelines.
- Phase ratings chart or table.
- Scenario analysis:
  - official strongest XI
  - star player out
  - early goal game state
  - knockout conservative setup
- Backtest/model calibration notes.
- Current model version, latest optimized parameter ID, calibration sample size, and whether the backtest is strict historical or current-snapshot pressure testing.

## Odds And Parlays

When exact-score odds are provided:

- Show the model/market blend weights.
- Use exactly three sections for the main Markdown output, and render all three sections as Markdown tables:
  1. `各场次的胜负关系`: one table with match, blended win/draw/loss probabilities, favored result, and favored probability.
  2. `各场次的比分预测 Top 8` or `各场次的完整比分概率与预期收入`: one table per match with exact scores by blended probability, including model probability, odds-implied probability, blended probability, odds, break-even probability, hit return, expected return, expected net profit, and ROI.
  3. `4 串 1 的预测 Top 6`: two tables, one for probability-first positions 1-3 and one for odds-first positions 4-6.
- Split the four-leg Top 6 into two ordered blocks:
  - Positions 1-3: prioritize combined blended hit probability first, then odds/balance.
  - Positions 4-6: prioritize combined decimal odds first, but keep a medium per-leg probability and value floor.
- In strength-aware mode, when a match has a clear blended WDL favorite, filter four-leg candidates to that favorite's outcome group before ranking exact scores.
- For four-leg exact-score combinations, show combined decimal odds, blended hit probability, hit return, expected return, expected net profit, ROI, and a balance or value proxy.
- When the odds table includes `胜其它`, `平其它`, or `负其它`, estimate its model probability as the remaining probability mass inside that outcome group after listed exact scores are removed.
- If the user does not provide a stake, use 1 unit; if they provide a stake, use it consistently for all single-score and parlay expected-value calculations.
- Keep language analytical. Do not call a combination a sure bet, lock, or staking recommendation.
- Prefer filtering out ultra-thin legs unless the user explicitly asks for maximum odds only.

## JSON Summary Option

For downstream use, include:

```json
{
  "team_a": "Team A",
  "team_b": "Team B",
  "p_team_a_win": 0.428,
  "p_draw": 0.276,
  "p_team_b_win": 0.296,
  "expected_goals": {
    "team_a": 1.42,
    "team_b": 1.08
  },
  "top_scorelines": [
    {"score": "1-1", "probability": 0.124},
    {"score": "1-0", "probability": 0.108}
  ],
  "recommended_score": "2-1",
  "confidence": "中低",
  "data_cutoff": "2026-06-20"
}
```

Only include JSON if the user asks for machine-readable output or the surrounding workflow needs it.
