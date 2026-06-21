# Modeling Framework

## Goal

Produce a transparent probability distribution, not a deterministic "answer." The model should explain why a team is favored, where uncertainty comes from, and which matchup factors can change the score.

## Layer 1: Data Readiness

Before modeling, check:

- Source freshness.
- Official squad completeness.
- FIFA ranking publication date and whether the ranking is official or live.
- Injury, suspension, and availability status.
- Expected or official lineup availability.
- Third-party player rating coverage and license/provenance.
- Recent results and fixture context.
- xG coverage in recent results.
- Style profiles for both teams.
- Tactical plans and formation matchup samples.

If any major layer is missing, proceed only with an explicit caveat.

## Layer 2: Team Strength Table

The strength table converts mixed evidence into 0-100 team ratings.

### Critical Strategy Review

Before adding weight to a factor, ask whether it is source-backed, predictive, and available before kickoff. Current v0.3 strategy is useful but still has these risks:

- Current-snapshot leakage: using 2026 squads/features to backtest older matches is only a calibration pressure test, not strict historical validation.
- Market anchoring: exact-score odds can improve calibration but can also drown out model signal if the blend weight is too high.
- Odds overround: bookmaker odds include margin. Normalize implied probabilities before blending, and do not treat positive EV as a fact when market margin or stale prices are unknown.
- Parlay independence: four-leg hit probability is currently the product of leg probabilities. That ignores shared macro factors, market correlation, and model-error correlation.
- EV sorting risk: high expected value can be an artifact of model miscalibration on long-shot scorelines. Keep probability/value floors and label EV-first outputs as high variance.
- Thin tactical samples: formation matchup priors are low weight unless enough completed fixtures share reliable formation labels.
- Independent Poisson limits: low-scoring correlations and game-state effects are not yet modeled.
- Derived player ratings: baseline ratings from caps/age/position are weaker than licensed provider ratings and should raise uncertainty.
- Injury and lineup missingness: unknown availability should not be treated as full certainty.
- Club-match transfer risk: club backtests can increase sample size for tactical archetypes, but national-team chemistry, preparation time, player familiarity, travel, and tournament incentives differ.

Optimize only after documenting sample size, date window, data source, and whether historical features were actually available before those matches.

When reviewing a strategy, explicitly answer:

- Is the model leaning too heavily on a weak data layer such as baseline player ratings or current-snapshot tactical profiles?
- Is the market blend hiding disagreement between model and odds, or only damping extreme model outputs?
- Are exact-score tails inflated by independent Poisson or by "other score" buckets?
- Are strong favorites filtered by both absolute WDL probability and WDL edge over the runner-up?
- Does the backtest show favorite confidence matching actual hit rate by bucket?
- Does EV remain positive after reasonable shrinkage of model probability toward the market?

### Components

- `fifa_component`: FIFA ranking/points baseline. Useful but not sufficient.
- `squad_quality`: weighted player quality, with starters weighted by expected minutes.
- `attack_rating`: finishing, chance creation, forward/creative player quality.
- `defense_rating`: defensive personnel, structure, duel strength.
- `possession_rating`: buildup, press resistance, midfield control.
- `transition_rating`: attacking and defensive transitions.
- `set_piece_rating`: attacking/defensive dead-ball edge.
- `goalkeeper_rating`: shot-stopping, cross handling, sweeping.
- `depth_rating`: top XI plus bench quality.
- `form_rating`: recent results, xG, opponent-adjusted trend.
- `experience_rating`: caps, tournament experience, leadership.
- `fitness_rating`: availability, injury load, workload.
- `coaching_rating`: tactical cohesion and coach continuity.
- `overall_rating`: weighted composite.
- `uncertainty`: data gaps, injury ambiguity, lineup ambiguity.

### Starting Weights

The bundled `build_strength_table.py` uses:

- FIFA component: 12%
- Squad quality: 18%
- Attack: 16%
- Defense: 16%
- Possession: 8%
- Transition: 8%
- Set pieces: 6%
- Goalkeeper: 6%
- Depth: 4%
- Form: 4%
- Fitness: 2%

Treat these as v0 weights. Recalibrate after backtesting.

`build_strength_table.py` v0.2 also reads:

- Latest `fifa_rankings` rows before falling back to `teams.fifa_rank`.
- Latest `player_ratings` rows before falling back to baseline player ratings.
- Latest `injuries` and `lineup_players` to down-weight unavailable or low-minute players.
- Latest tactical-plan formation updates applied through `apply_enhancements.py`.

When enhancement data is missing, the snapshot remains usable but uncertainty should stay higher.

### Feature Extraction

Run `extract_team_features.py` after importing squads, recent results, player ratings, injuries, and lineups. It derives:

- Squad structure: weighted position mix, top-X player quality, age, caps, height, and role minutes.
- Player traits: attack, defense, possession, transition, set-piece, goalkeeping, and fitness.
- Recent form: recency- and competition-weighted goals, xG when available, chance volume from shots/shots-on-target, possession signal, clean-sheet rate, and failed-score rate.
- Tactical profile: formation tendency, tempo, press intensity, defensive line, buildup quality, transition attack/defense, wing/central creation, set-piece attack/defense, aerial strength, low-block attack/defense, goalkeeper profile, cohesion, and risk level.
- Generic tactical plan: defensive shape, pressing trigger, buildup pattern, chance creation route, transition plan, set-piece plan.

Treat these generated features as a scaffold. Override them with official lineups, reputable tactical reports, provider metrics, or opponent-specific `tactical_plans` when available.

Useful additional features to import when available:

- Player role and club context: club minutes, league strength, tactical role, weak foot/footedness, aerial duels, progressive passing/carrying, ball recoveries, shot creation, non-penalty xG/xA, set-piece role, goalkeeper PSxG and cross-claim profile.
- Team phase metrics: xG for/against, non-penalty xG, shots, shots on target, box entries, PPDA/pressing intensity, field tilt, counterattack shot share, set-piece xG, defensive-line height, possession under pressure.
- Context: rest days, travel distance/time zone, altitude/heat, venue familiarity, referee foul/card tendency, tournament incentives, rotation probability, match state from group standings.
- Market data: normalized 1X2 and exact-score odds, price movement, dispersion across books, and stale-price flags.
- Archetype labels: formation family, press/buildup style, transition profile, low-block profile, set-piece orientation, and goalkeeper type. These can be learned from club matches and transferred as low-weight priors to national teams.

### Player Aggregation

Player ratings should use expected role weights:

- Use `players.national_team_position` for squad role, formation inference, and national-team phase aggregation. Fall back to legacy `players.position` only for older data.
- Use `players.club_position` to explain and shrink club-derived features. If club role and national-team role diverge, keep the club-derived feature weight low or require manual review before increasing it.
- Confirmed starters: 70-100 minutes expected.
- Likely starters: 55-85.
- Rotation players: 20-50.
- Late substitutes: 5-25.
- Deep reserves: 0-10.
- Injured/suspended/withdrawn players: excluded or heavily down-weighted.

For goalkeepers, use the expected starter's value when known; otherwise use the max or weighted expectation among goalkeepers.

### Injury and Lineup Weighting

Use this order:

1. Official lineup minutes and starter flags.
2. Confirmed lineup reports.
3. Expected lineup projections.
4. Baseline `players.minutes_expected`.

Then multiply by `availability_pct` from lineup or injury rows. Suspended, withdrawn, and out players should receive zero or near-zero weight. Doubtful/limited players should keep some weight only when notes justify it.

### Form

Use last 12 matches as a starting point. Prefer competitive matches and recent matches. If xG exists, use xG differential more than raw goal differential. If Elo exists, use Elo change as opponent-adjusted form.

Suggested formula:

```text
form = 50
     + 18 * avg_recent(xg_for - xg_against)
     + 1.5 * avg_recent(elo_after - elo_before)
```

Clamp to 0-100.

## Layer 3: Style and "克制关系"

Generic strength is not enough. Use matchup analysis to adjust expected goals.

### Core Counter Patterns

Press versus buildup:

- High `press_intensity` against low `buildup_quality` adds chance creation.
- High buildup quality against aggressive press can reduce the pressing team's edge and create space behind.

Transition versus high line:

- High `transition_attack` against weak `transition_defense` and high `defensive_line` adds expected goals.
- Strong rest defense and compact spacing can suppress transition sides.

Set pieces:

- High `set_piece_attack` and `aerial_strength` against weak `set_piece_defense` adds expected goals.
- This matters more in low-event knockout matches.

Low block:

- High `low_block_defense` can reduce opponent xG despite possession.
- High `low_block_attack` or strong creators reduce that defensive edge.

Wide channels:

- High `wing_play` against weak fullback/air coverage creates volume.
- Check whether crosses match striker profile; volume without box targets should get a smaller boost.

Goalkeeper fit:

- Sweeper keeper matters against through balls and high lines.
- Cross handling matters against wing/cross-heavy teams.
- Shot-stopping matters in low-shot, high-quality-chance matchups.

Personnel:

- A missing ball-progressing midfielder can weaken buildup more than raw overall rating suggests.
- A suspended center back can hurt set-piece defense and high-line recovery.
- A compromised striker can lower finishing and pressing simultaneously.

Rest and travel:

- Extra rest, shorter travel, heat adaptation, and host familiarity can matter, but keep adjustments modest unless evidence is strong.

### Tactical Plans

Use `tactical_plans` for opponent-specific setup:

- `formation`: expected shape, such as `4-3-3`.
- `defensive_shape`: pressing, mid-block, low-block, or hybrid behavior.
- `pressing_trigger`: when pressure starts.
- `buildup_pattern`: back three, double pivot, direct play, goalkeeper involvement.
- `chance_creation`: wide overloads, central combinations, early crosses, cutbacks.
- `transition_plan`: counter speed and rest-defense posture.
- `set_piece_plan`: targeted mismatch or defensive concern.
- `risk_level`: higher values slightly raise expected goals and match openness.

Do not let tactical notes override hard availability facts. A tactical edge involving an absent player should be reduced or removed.

### Formation Matchup Backtesting

Use `analyze_formation_matchups.py` to create `formation_matchup_stats` from completed fixtures and team style profiles.

For each formation pair, store W/D/L rates, average goals, top scorelines, and sample size. Prediction uses this as a small prior; low samples have low confidence and should not dominate team strength.

### Adjustment Magnitudes

Use `matchup_adjustments.goal_delta` conservatively:

- Minor edge: `0.03` to `0.06`.
- Meaningful edge: `0.07` to `0.12`.
- Major edge: `0.13` to `0.20`.
- Extreme edge above `0.20` requires strong evidence and should be rare.

The script multiplies manual deltas by `confidence`.

## Layer 4: Expected Goals

The bundled `predict_match.py` computes expected goals using:

```text
lambda = base_goals
       + attack-vs-defense edge
       + overall edge
       + goalkeeper edge
       + set-piece edge
       + fitness edge
       + style counter delta
       + tactical risk delta
       + formation matchup prior
       + manual matchup delta
       + host edge
       + knockout tempo adjustment
```

Defaults:

- `base_goals`: 1.22 per team.
- Stage/round effects are adaptive. The model first reads completed same-stage fixtures from `fixtures`, estimates the stage's actual average total-goal level versus `openness_baseline_total`, and blends that evidence by sample confidence. With no same-stage sample, the stage multiplier stays neutral (`1.00`) instead of forcing a lower lambda. Configured round multipliers such as `stage_round16_goal_multiplier` default to neutral and should become non-neutral only after backtesting/optimization writes a calibrated parameter row.
- Host edge applies only when `--non-neutral` is used and the team is marked as host.
- `model_parameters` can override built-in expected-goals weights after optimization.

The lambdas are bounded between `0.2` and `3.8`.

### Match Openness Layer

Starting in `wc2026-skill-v0.4`, expected goals include a separate openness adjustment after the base team lambdas are calculated. This was added after the Netherlands 5-1 Sweden post-match review exposed a low-score bias: the model saw a home-win edge but compressed the exact-score distribution toward `1-0`/`1-1`.

The openness layer estimates whether the match environment should produce more or fewer total goals, using:

- Recent total-goal profile from each team's latest `team_results`.
- Formation-pair average goals from `formation_matchup_stats`.
- Style tempo, pressing, transition attack/defense, defensive line, and chance-route volume from `team_style_profiles`.
- Tactical `risk_level` from `tactical_plans`.

The total openness delta is bounded by `openness_max_delta`, then split between teams using base attacking claim, recent goals-for, and opponent recent goals-against. This avoids blindly raising both teams equally when the better attack faces a leaking defensive profile.

Use these parameters in `model_parameters`:

- `openness_baseline_total`: neutral total-goal reference, default `2.55`.
- `recent_goal_openness_weight`: weight on recent total-goal profile.
- `formation_openness_weight`: weight on formation-pair total-goal prior.
- `style_openness_weight`: weight on tempo/pressing/transition style signal.
- `tactical_openness_weight`: weight on tactical risk.
- `openness_max_delta`: maximum absolute total-goal adjustment.

When exact-score odds are provided, `analyze_score_odds_parlay.py` also uses the openness signal in the published score recommendation. If the match has a strong openness signal, the selected score remains inside the WDL favorite group, but it can be moved from the low-score probability peak toward a higher-total scoreline when the higher-total candidate keeps enough probability support. This is a publishing/calibration rule, not a claim that high scores are likely.

## Layer 5: Scoreline Distribution

Use independent Poisson as v0:

```text
P(score a-b) = Pois(a, lambda_a) * Pois(b, lambda_b)
```

Starting in `wc2026-skill-v0.5`, the scoreline layer is explicitly tied to the first-stage WDL model:

1. Compute a WDL prior from team strength, attack/defense edges, form, goalkeeper/set-piece edge, tactical/style counters, manual matchup adjustments, lineup/injury-aware strength snapshots, and the adaptive stage context.
2. Compute an initial exact-score grid from the expected-goals lambdas.
3. Blend the WDL prior with the score-grid WDL and low-weight formation-pair WDL samples.
4. Reweight exact-score probabilities by outcome group so the final top scorelines do not contradict the WDL layer.
5. Apply small within-group tilts for favorite-margin and draw-shape calibration.

Use these parameters in `model_parameters`:

- `wdl_prior_weight`: how strongly the first-stage WDL prior constrains the score-grid WDL.
- `formation_wdl_prior_max_weight`: cap for formation-pair WDL samples.
- `wdl_score_calibration_weight`: how strongly exact-score rows are reweighted toward target WDL.
- `favorite_score_tilt`: within-favorite-group tilt toward stronger winning margins.
- `draw_score_tilt`: within-draw-group tilt around plausible draw totals.
- `stage_data_weight`: how much completed same-stage goal samples influence the round multiplier.
- `stage_sample_half_life`: sample-size half-life for stage confidence.
- `stage_open_match_resistance`: lets current matchup openness neutralize an unsupported conservative stage prior, without using openness twice to inflate lambdas.

Aggregate and report:

- First-stage WDL prior.
- Raw score-grid WDL.
- Target WDL after prior/formation blending.
- Calibrated WDL after exact-score reweighting.
- Top scorelines.

Limitations:

- Independent Poisson underestimates tactical correlation in some matches.
- Low-scoring draw inflation may need Dixon-Coles correction after backtesting.
- Penalty shootouts are not modeled as regulation-time scorelines unless explicitly added.

## Calibration and Backtesting

After enough completed matches:

1. Store predictions before kickoff in `predictions`.
2. Compare predicted probabilities to actual results.
3. Track log loss, Brier score, calibration by probability bucket, and scoreline error.
4. Review whether favorites are over- or under-confident.
5. Review formation-pair samples for systematic tactical bias.
6. Review EV stability under model/market blend sensitivity, such as 70/30, 60/40, and 50/50.
7. Adjust weights, base-goals, and knockout adjustments.

Bundled scripts:

- `backtest_model.py`: writes `backtest_runs` and `backtest_predictions`.
- `optimize_model_parameters.py`: grid-searches a small parameter set and can write the best row to `model_parameters`.
- `analyze_formation_matchups.py`: builds formation-pair priors.

Suggested backtest fields:

- Prediction timestamp.
- Data cutoff.
- Official lineup availability.
- Match stage.
- Actual score.
- Result probability assigned to actual outcome.
- Favorite outcome, favorite confidence, favorite hit/miss.
- Calibration bucket, bucket sample size, average confidence, actual hit rate.

### Club-To-National Backtest Transfer

Use club matches only for feature-behavior relationships, not direct national-team strength:

- Build archetype pairs such as high press vs deep buildup, 4-3-3 vs 4-2-3-1, low block vs possession side, set-piece team vs aerially weak team.
- Backtest whether those archetype pairs change xG, WDL rates, or exact-score frequencies in large club samples.
- Import event-level club data into `club_*` tables with `ingest_statsbomb_open_data.py` or licensed provider CSVs. Use `club_player_feature_snapshots` for player role traits such as xG/shot volume, key passing, pressing, carrying, defensive actions, and box touches.
- Map club players to World Cup squad players through `unified_players` and `player_identity_links`. Auto-links from name/club/position matching must remain `verified=0` until reviewed; ambiguous names should not receive high weight.
- Run `merge_club_player_features.py` to turn linked club traits into low-weight model inputs:
  - `player_feature_snapshots`: keeps interpretable role features such as high pressing, ball progression, box presence, shot quality, key passing, duels, and defensive actions.
  - `player_ratings`: adds a low-weight `statsbomb-derived-low-weight` provider row that the strength table can aggregate.
  - `players.feature_*`: caches the latest low-weight role scores for fast feature extraction when `--apply-to-players` is used.
  - `team_style_profiles`: blends squad-level role traits into press intensity, buildup, transition attack/defense, wide/central creation, low-block attack/defense, and tempo.
- Transfer the learned effect as a small prior into `team_style_profiles`, `formation_matchup_stats`, or `matchup_adjustments`.
- Shrink club-derived effects aggressively unless the national-team player roles, coach style, and expected lineup match the club archetype.
- Keep strict separation between national-team outcome calibration and club-derived tactical priors.

### Historical Results Import

If no completed fixtures exist locally, use `import_historical_results.py` to import public men's international results into:

- `fixtures`: only matches where both teams map to current 2026 participant teams.
- `team_results`: every mapped 2026 participant result, even when the opponent is outside the 2026 field.

Recommended calibration window:

```bash
python3 skills/predict-2026-world-cup-scores/scripts/import_historical_results.py --db data/worldcup2026.sqlite --since 2021-01-01 --until 2026-06-20 --replace-source
python3 skills/predict-2026-world-cup-scores/scripts/extract_team_features.py --db data/worldcup2026.sqlite --profile-date 2026-06-20 --recent-limit 12
python3 skills/predict-2026-world-cup-scores/scripts/build_strength_table.py --db data/worldcup2026.sqlite --rating-date 2026-06-20
python3 skills/predict-2026-world-cup-scores/scripts/analyze_formation_matchups.py --db data/worldcup2026.sqlite --since 2021-01-01 --until 2026-06-20 --min-sample 3
python3 skills/predict-2026-world-cup-scores/scripts/backtest_model.py --db data/worldcup2026.sqlite --test-start 2021-01-01 --test-end 2026-06-20
python3 skills/predict-2026-world-cup-scores/scripts/optimize_model_parameters.py --db data/worldcup2026.sqlite --test-start 2021-01-01 --test-end 2026-06-20 --grid coarse --write-best
```

Use `--grid smoke --max-matches 200` for quick iteration. Use `--grid full` only when runtime is acceptable.

### Persistent Weighted Training Cache

After importing historical international results and optional StatsBomb/licensed event data, materialize `training_matches`:

```bash
python3 skills/predict-2026-world-cup-scores/scripts/build_training_dataset.py --db data/worldcup2026.sqlite --since 2018-01-01 --until 2026-06-21 --include-club --replace
```

Use the cache for repeated model training:

```bash
python3 skills/predict-2026-world-cup-scores/scripts/backtest_model.py --db data/worldcup2026.sqlite --test-start 2021-01-01 --test-end 2026-06-21 --use-training-cache
python3 skills/predict-2026-world-cup-scores/scripts/optimize_model_parameters.py --db data/worldcup2026.sqlite --test-start 2021-01-01 --test-end 2026-06-21 --grid smoke --max-matches 200 --use-training-cache
```

Weighting policy:

- FIFA World Cup main-tournament rows: default `1.25`. These are closest to the target tournament environment, so they get modestly higher influence.
- World Cup qualifiers: about `1.08`. Useful for national-team signal but less similar to neutral-site tournament play.
- Continental cups: about `1.06`. Competitive and tournament-like, but not identical to the World Cup.
- Nations League and qualifiers: around `1.00`.
- Friendlies: around `0.72`; lineups and incentives are less reliable.
- Club/league samples: default `0.88`; use primarily for tactical archetypes, score-shape behavior, xG/event relationships, and player-role features rather than direct national-team strength.
- Event/xG-rich rows receive a small bonus because they can train score-shape and chance-quality behavior.
- Knockout rows receive a small context bonus, but round conservatism should come from learned stage factors, not from a forced lambda clamp.

Keep the distinction between domains:

- `international`: mapped FIFA-team rows that can feed the current national-team `predict_match.py` backtests and optimization.
- `statsbomb_international`: provider international event rows, including World Cup or Euro samples, retained with tournament-aware weights but not directly passed to `predict_match.py` until provider team IDs are mapped.
- `club`: club samples for richer player/tactical feature learning and score-distribution calibration.

## Output Interpretation

Use probability language:

- "Model leans France 44%, draw 27%, Brazil 29%."
- "Most likely single scoreline is 1-1, but single scorelines are low-probability events."
- "A 2-1 forecast means the distribution center is around those goals, not that 2-1 is certain."

Avoid:

- "必胜"
- "稳胆"
- "guaranteed"
- Betting instructions or stake sizing.

## Human Review Checklist

Before sending a prediction:

- Were official lineups available?
- Were injuries/suspensions checked today?
- Is the fixture venue/stage correct?
- Are both strength snapshots current?
- Is there at least one matchup-specific explanation?
- Are top scorelines and win/draw/loss probabilities shown?
- Is uncertainty stated plainly?
