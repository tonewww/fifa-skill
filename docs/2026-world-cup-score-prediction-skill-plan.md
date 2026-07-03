# 2026 世界杯球队比分预测 Skill 搭建方案

## 目标

建立一个可持续更新、可解释、可回测的  skill，用于 2026 FIFA 世界杯球队资料库建设、球队实力表生成、两队胜平负与比分预测，以及克制关系分析。

本方案不把球队、球员、伤病和赛果硬编码在 skill 内。2026 世界杯期间数据变化很快，skill 应当先刷新数据，再建模预测。

## 已搭建 Skill

路径：

```text
skills/predict-2026-world-cup-scores
```

主要能力：

- 归档 FIFA 官方数据源。
- 初始化 SQLite 数据库。
- 从官方 SquadLists PDF 拉取 48 支球队和 1248 名球员作为本地基础参数。
- 生成 normalized CSV 模板。
- 导入球队、球员、赛程、赛果、FIFA 排名、xG、第三方球员评分、伤病、首发、战术风格、克制关系等数据。
- 校验 48 队覆盖、球员覆盖、实力表、风格画像和数据新鲜度。
- 建立球队实力表。
- 导出实力表。
- 预测两队胜平负概率和 top 8 最可能比分。
- 回测历史/已完赛比赛，生成阵型对位先验，并优化模型参数。

## 数据库分层

### 原始数据层

保存到：

```text
data/raw/YYYY-MM-DD/
```

来源优先级：

- FIFA 官方赛事页、球队页、赛程/比分页、官方 SquadLists PDF、FIFA 男足排名。
- 各国家队/足协官方伤病、替补、停赛、首发信息。
- 可信数据供应商的 xG、球员评分、俱乐部出场时间、伤病和身价。
- 可靠媒体的预计首发和临场消息。

### 规范化数据层

CSV 表：

- `sources.csv`
- `teams.csv`
- `players.csv`
- `fixtures.csv`
- `team_results.csv`
- `fifa_rankings.csv`
- `player_ratings.csv`
- `injuries.csv`
- `lineups.csv`
- `lineup_players.csv`
- `tactical_plans.csv`
- `formation_matchup_stats.csv`
- `team_style_profiles.csv`
- `matchup_adjustments.csv`

### 模型输出层

SQLite 表：

- `team_strength_snapshots`
- `predictions`
- `model_parameters`
- `backtest_runs`
- `backtest_predictions`

## 数据表设计

### teams

记录 48 支参赛队：

- FIFA code
- 队名
- 大洲
- 小组
- 东道主标记
- 主教练
- FIFA 排名与积分
- 球队名单状态
- 来源与最后核验时间

### players

记录每队球员：

- 球员名、号码、位置、俱乐部、联赛
- 年龄、国家队出场、国家队进球
- 身高、惯用脚、身价
- 综合评分、进攻、防守、控球、转换、定位球、门将、健康评分
- 当前状态：available、doubtful、limited、suspended、out、withdrawn
- 预计出场权重 `minutes_expected`

### team_results

记录近期战绩：

- 比赛日期
- 对手
- 比赛性质
- 中立/主客场
- 进失球
- xG
- 射门/射正
- 控球
- Elo 赛前赛后

### fifa_rankings

记录 FIFA 排名历史：

- 排名发布日期
- official/live/provider/manual 口径
- FIFA code
- rank
- points
- previous rank/points
- source

最新 official 行可同步到 `teams.fifa_rank` 与 `teams.fifa_points`，但历史排名保留在 `fifa_rankings`。

### player_ratings

记录第三方或人工球探评分：

- provider
- rating_date
- overall
- attack/defense/possession/transition/set_piece/goalkeeping/fitness
- market value
- recent minutes

有授权限制的数据只存允许落地的派生值，不保存不可再分发的原始表。

### injuries

记录伤病、停赛和可用性：

- status
- severity
- injury_type
- expected_return
- availability_pct
- impact_rating
- verified_at

### lineups / lineup_players

记录 expected/confirmed/official 首发：

- formation
- starter flag
- role
- position
- minutes_expected
- availability_pct

官方首发优先级最高，预计首发只作为赛前模型输入。

### tactical_plans

记录战术安排：

- formation
- defensive_shape
- pressing_trigger
- buildup_pattern
- chance_creation
- transition_plan
- set_piece_plan
- risk_level

### formation_matchup_stats

记录同类型或不同类型阵型对比回测：

- formation_a vs formation_b
- sample_size
- 胜平负比例
- 平均进球
- 高频比分 JSON

### team_style_profiles

记录球队风格：

- 节奏
- 压迫强度
- 防线高度
- 后场出球质量
- 反击
- 转换防守
- 边路进攻
- 中路推进
- 定位球攻防
- 空中优势
- 破低位防守
- 低位防守能力
- 门将出击/扑救
- 伤病负荷
- 磨合度
- 旅行疲劳

### matchup_adjustments

记录两队之间的克制关系：

- 高压迫 vs 后场出球
- 快速反击 vs 高位防线
- 定位球/空中优势 vs 定位球防守
- 边路爆破 vs 边后卫弱点
- 低位防守 vs 破密集能力
- 门将特点 vs 对手射门/传中方式
- 关键球员伤停导致的结构性变化

## 实力表模型

综合评分由以下维度构成：

- FIFA 排名基线：12%
- 阵容质量：18%
- 进攻：16%
- 防守：16%
- 控球/出球：8%
- 转换攻防：8%
- 定位球：6%
- 门将：6%
- 阵容深度：4%
- 近期状态：4%
- 健康/可用性：2%

v0 权重强调可解释性。后续应通过世界杯真实赛果回测调参。

## 比分预测模型

预测流程：

1. 读取双方最新实力快照。
2. 读取双方最新战术风格。
3. 计算进攻 vs 防守、综合实力、门将、定位球、体能差异。
4. 根据风格克制关系调整 expected goals。
5. 叠加人工审核过的 matchup adjustments。
6. 生成双方 `lambda`。
7. 用 Poisson 分布生成比分概率。
8. 汇总胜平负概率和最可能比分。

注意：单一比分天然低概率，输出时应先给胜平负分析，再给 top 8 比分概率。

## 推进步骤

### 第一阶段：资料库闭环

完成内容：

- skill 骨架。
- schema。
- 初始化/导入/校验/建模/预测脚本。
- FIFA 官方源归档入口。

下一步：

```bash
python3 skills/predict-2026-world-cup-scores/scripts/init_database.py \
  --db data/worldcup2026.sqlite \
  --with-reference-sources \
  --template-dir data/templates

python3 skills/predict-2026-world-cup-scores/scripts/fetch_official_sources.py \
  --db data/worldcup2026.sqlite \
  --out-dir data/raw
```

### 第二阶段：48 队与官方名单入库

目标：

- 建立完整 `teams.csv`。
- 解析或人工整理官方 SquadLists PDF。
- 建立完整 `players.csv`。
- 每队至少 23 人，理想状态为官方最终 26 人。

校验：

```bash
python3 skills/predict-2026-world-cup-scores/scripts/validate_database.py \
  --db data/worldcup2026.sqlite \
  --strict
```

### 第三阶段：实力评分数据

补充：

- FIFA ranking points。
- 最近 12-24 场国家队比赛。
- xG 或替代机会质量指标。
- 球员俱乐部表现、出场时间、健康状态、角色权重。
- 第三方球员评分和身价，必须保留 provider/source/license。
- 伤病/停赛/可用性。
- expected/confirmed/official 首发。
- 门将、定位球、压迫、转换、破低位等专项评分。

输出：

```bash
python3 skills/predict-2026-world-cup-scores/scripts/build_strength_table.py \
  --db data/worldcup2026.sqlite \
  --rating-date 2026-06-20

python3 skills/predict-2026-world-cup-scores/scripts/export_strength_table.py \
  --db data/worldcup2026.sqlite \
  --format markdown \
  --out docs/latest-strength-table.md
```

增强数据导入：

```bash
python3 skills/predict-2026-world-cup-scores/scripts/ingest_fifa_rankings.py \
  --db data/worldcup2026.sqlite \
  --csv data/fifa_rankings.csv \
  --ranking-date 2026-06-11 \
  --sync-teams

python3 skills/predict-2026-world-cup-scores/scripts/import_csv.py \
  --db data/worldcup2026.sqlite \
  --table player_ratings \
  --csv data/player_ratings.csv

python3 skills/predict-2026-world-cup-scores/scripts/import_csv.py \
  --db data/worldcup2026.sqlite \
  --table injuries \
  --csv data/injuries.csv

python3 skills/predict-2026-world-cup-scores/scripts/import_csv.py \
  --db data/worldcup2026.sqlite \
  --table lineups \
  --csv data/lineups.csv

python3 skills/predict-2026-world-cup-scores/scripts/import_csv.py \
  --db data/worldcup2026.sqlite \
  --table lineup_players \
  --csv data/lineup_players.csv

python3 skills/predict-2026-world-cup-scores/scripts/import_csv.py \
  --db data/worldcup2026.sqlite \
  --table tactical_plans \
  --csv data/tactical_plans.csv

python3 skills/predict-2026-world-cup-scores/scripts/apply_enhancements.py \
  --db data/worldcup2026.sqlite
```

### 第四阶段：克制关系库

为重点对阵建立 `matchup_adjustments.csv`：

- 每条 adjustment 必须有 `category`、`goal_delta`、`confidence`、`rationale`。
- 单条调整通常控制在 `-0.20` 到 `0.20` expected goals。
- 强主观判断必须写清楚依据。

### 第五阶段：预测与回测

单场预测：

```bash
python3 skills/predict-2026-world-cup-scores/scripts/predict_match.py \
  --db data/worldcup2026.sqlite \
  --team-a BRA \
  --team-b FRA \
  --stage "Group Stage" \
  --save
```

回测指标：

- Brier score
- log loss
- 概率校准
- 实际比分与 top scorelines 偏差
- 小组赛/淘汰赛分场景表现

阵型对位回测：

```bash
python3 skills/predict-2026-world-cup-scores/scripts/analyze_formation_matchups.py \
  --db data/worldcup2026.sqlite \
  --min-sample 3
```

模型回测与参数优化：

```bash
python3 skills/predict-2026-world-cup-scores/scripts/backtest_model.py \
  --db data/worldcup2026.sqlite

python3 skills/predict-2026-world-cup-scores/scripts/optimize_model_parameters.py \
  --db data/worldcup2026.sqlite \
  --write-best
```

## 关键风险

- 官方名单、伤病、首发会持续变化，不能依赖旧数据。
- 球员评分和 xG 供应商可能有授权限制。
- 独立 Poisson 对低比分相关性建模有限，后续可加 Dixon-Coles 修正。
- 赔率/市场数据只能作为校准参考，不应输出下注建议。
- 球队风格评分带有主观性，必须记录来源和置信度。

## 推荐验收标准

- `validate_database.py --strict` 通过。
- 48 队全部入库。
- 每队官方最终名单入库，球员状态有来源。
- 每队至少一个 style profile。
- 每队至少一个 strength snapshot。
- 能导出完整实力表。
- 任意两队能生成胜平负概率和 top scorelines。
- 输出包含数据时间、克制关系、不确定性和非确定性表述。
