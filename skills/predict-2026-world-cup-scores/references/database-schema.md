# Database Schema

## Principle

Use SQLite as the canonical local store. Keep raw source archives outside the database and import normalized facts with source IDs. All subjective model values should be inspectable and replaceable.

## Tables

### `sources`

Tracks every data source.

Key fields:

- `source_id`: stable text ID.
- `name`, `url`, `publisher`, `category`: provenance.
- `retrieved_at`, `published_at`, `freshness_days`: freshness audit.
- `reliability`: use values such as `official`, `provider`, `reputable-media`, `manual`.
- `license_note`: usage constraints.

### `teams`

One row per 2026 World Cup team.

Minimum required fields:

- `team_id`: stable ID, preferably FIFA code.
- `fifa_code`: official three-letter code.
- `name`: official team name.
- `group_name`: group label after draw.
- `is_host`: `1` for Canada, Mexico, United States, otherwise `0`.
- `fifa_rank`, `fifa_points`: current FIFA ranking baseline.
- `squad_status`: `provisional`, `final`, `updated`, or `unknown`.
- `last_verified_at`, `source_id`: source tracking.

Completion rule: exactly 48 rows before full tournament analysis.

### `players`

One row per squad player.

Minimum required fields:

- `player_id`, `team_id`, `name`, `position`, `club`, `status`.
- `rating_overall`: 0-100 estimate or provider-derived value.
- Role-specific ratings: `rating_attack`, `rating_defense`, `rating_possession`, `rating_transition`, `rating_set_piece`, `rating_goalkeeping`, `rating_fitness`.
- `minutes_expected`: projected role weight.

Completion rule: usually 26 players per final squad. Fewer than 23 players is not prediction-ready.

Position guidance:

- Use `GK`, `DF`, `MF`, `FW` for broad grouping.
- More specific roles can live in `notes`, such as `inverted fullback`, `ball-winning 6`, or `target 9`.

### `fixtures`

Tournament schedule and results.

Use `status` values:

- `scheduled`
- `lineups-announced`
- `live`
- `final`
- `postponed`

Before the knockout bracket is known, team IDs may be empty and notes can identify placeholders.

### `team_results`

Recent national team results for form and opponent-adjusted performance.

Prefer the last 12-24 matches, with higher weight on the latest 6-10 competitive matches.

Important fields:

- `goals_for`, `goals_against`
- `xg_for`, `xg_against` when available
- `elo_before`, `elo_after` when available
- `competition`, `venue_type`, `is_neutral`

### `fifa_rankings`

Historical FIFA ranking rows.

Use this table as the authoritative ranking history and treat `teams.fifa_rank` / `teams.fifa_points` as a convenience cache.

Important fields:

- `ranking_date`: official publication date.
- `ranking_type`: `official`, `live`, `provider`, or `manual`.
- `team_id`, `fifa_code`: map to `teams`.
- `rank`, `points`, `previous_rank`, `previous_points`, `rank_change`.
- `source_id`, `notes`: record whether data came from FIFA page/API, reviewed CSV, or manual import.

### `player_ratings`

Provider or analyst player ratings over time.

Do not overwrite baseline player facts without provenance. Import provider rows here, then run `apply_enhancements.py` or rebuild the strength table, which reads latest provider rows directly.

Fields:

- `provider`: rating source, such as a licensed provider name or `manual-scouting`.
- `rating_date`.
- 0-100 ratings: `overall`, `attack`, `defense`, `possession`, `transition`, `set_piece`, `goalkeeping`, `fitness`.
- `market_value_eur`, `minutes_recent` when allowed.

### `injuries`

Latest injury, suspension, availability, and fitness reports.

Recommended status values:

- `available`
- `doubtful`
- `limited`
- `suspended`
- `out`
- `withdrawn`

Use `availability_pct` for model weighting and `impact_rating` to flag important absences.

### `lineups` and `lineup_players`

Expected, confirmed, or official lineups.

`lineups.lineup_type` values:

- `expected`: model/analyst projected XI.
- `confirmed`: reliable pre-match report.
- `official`: official match lineup.

`lineup_players` stores starters and role weights:

- `is_starter`: `1` for starting XI.
- `minutes_expected`: role weight for aggregation.
- `availability_pct`: player-level adjustment for this match.

Official lineups should dominate expected lineups in prediction reports.

### `tactical_plans`

Opponent-specific tactical assumptions.

Use this table for structured analysis that is more specific than broad `team_style_profiles`.

Fields:

- `formation`: expected or observed shape.
- `defensive_shape`, `pressing_trigger`, `buildup_pattern`, `chance_creation`, `transition_plan`, `set_piece_plan`.
- `risk_level`: 0-100, where higher means more aggressive/open.
- `opponent_team_id`: blank means generic team plan; populated means matchup-specific.

### `formation_matchup_stats`

Aggregated historical performance for formation pairings, such as `4-3-3` vs `4-2-3-1`.

Fields:

- `sample_size`.
- `p_a_win`, `p_draw`, `p_b_win`.
- `avg_goals_a`, `avg_goals_b`.
- `scoreline_json`: top historical scorelines as JSON.

Use this as a low-weight prior. Small samples should not dominate team quality or lineup evidence.

### `team_style_profiles`

Numeric tactical profile for each team, all 0-100 unless noted.

Core dimensions:

- `tempo`: speed of circulation and attacking rhythm.
- `press_intensity`: ability and willingness to press.
- `defensive_line`: higher values mean higher line.
- `buildup_quality`: resistance to pressure and ability to progress from the back.
- `transition_attack`, `transition_defense`.
- `wing_play`, `central_progression`.
- `set_piece_attack`, `set_piece_defense`.
- `aerial_strength`.
- `low_block_attack`, `low_block_defense`.
- `keeper_sweeper`, `keeper_shot_stopping`.
- `injury_load`: higher means worse availability/fitness situation.
- `cohesion`: continuity of coach, XI, and tactical system.
- `travel_fatigue`: travel/rest burden.

Profile scores can come from data, scouting, or analyst judgment, but the `notes` field must say which.

### `team_strength_snapshots`

Dated output of `build_strength_table.py`.

These snapshots are the model's team-level ratings:

- `overall_rating`
- phase ratings: attack, defense, possession, transition, set piece, goalkeeper
- context ratings: form, depth, experience, fitness, coaching
- `uncertainty`: lower means more confidence
- `model_version`: script/model version
- `source_count`: distinct sources contributing to the snapshot

Rebuild after importing relevant new data.

### `matchup_adjustments`

Stores specific "克制关系" adjustments that are not captured by generic ratings.

Examples:

- A high press that targets a weak build-up side.
- A fast transition team versus a high defensive line.
- A set-piece mismatch caused by aerial dominance.
- A fullback injury against elite wing overloads.
- A goalkeeper weakness against crosses or long shots.

Fields:

- `team_a_id`, `team_b_id`: matchup context.
- `affected_team_id`: team receiving the adjustment.
- `category`: `pressing`, `transition`, `set-piece`, `wide-channel`, `low-block`, `keeper`, `personnel`, `rest-travel`.
- `goal_delta`: expected goals adjustment for affected team, usually between `-0.20` and `0.20`.
- `confidence`: 0-1.
- `rationale`: concise explanation.

### `predictions`

Stores model outputs for audit and backtesting.

Important fields:

- `lambda_a`, `lambda_b`: expected goals.
- `p_team_a_win`, `p_draw`, `p_team_b_win`.
- `top_scorelines_json`.
- `data_cutoff`.

### `model_parameters`

Stores calibrated expected-goals weights used by `predict_match.py`.

If the table is empty, scripts use built-in defaults. After backtesting, write a new row instead of editing code.

Important fields:

- `base_goals`, `home_edge`, `knockout_drag`.
- `attack_weight`, `overall_weight`, `keeper_weight`, `set_piece_weight`, `fitness_weight`.
- `style_weight`, `formation_weight`.

### `backtest_runs` and `backtest_predictions`

Backtest audit tables.

`backtest_runs` stores aggregate metrics:

- `brier_score`
- `log_loss`
- `mae_goals`
- `exact_score_accuracy`
- `top8_score_hit_rate`

`backtest_predictions` stores match-level predictions and actual outcomes for inspection.

## Minimum Prediction-Ready Dataset

For a single match:

- Both teams exist in `teams`.
- Both teams have player rows and recent availability.
- Both teams have current `team_strength_snapshots`.
- Both teams have `team_style_profiles`.
- Fixture context is known: stage, venue neutrality, rest/travel if available.

For a high-confidence prediction:

- Official or credible expected lineups.
- Injury/suspension status checked within the freshness window.
- Recent results include xG or equivalent chance-quality signal.
- FIFA ranking row is current.
- Third-party/player-rating inputs are licensed or manually supplied with provenance.
- Tactical plan and formation matchup priors have been reviewed.
- Matchup-specific notes reviewed.
- Model uncertainty is reported.

## ID Conventions

- Teams: use FIFA code where possible, such as `BRA`, `FRA`, `ARG`.
- Players: use `TEAM-slug-name`, such as `FRA-kylian-mbappe`, until a provider ID is available.
- Fixtures: use source match IDs if available; otherwise `fixture-YYYYMMDD-team-a-team-b`.
- Snapshots: `strength-team-date`.
- Rankings: `ranking-date-type-team`.
- Lineups: `lineup-match-team-type-asof`.
- Formation stats: `formation-433-vs-4231`.

## SQL Checks

Team coverage:

```sql
SELECT COUNT(*) FROM teams;
```

Squad counts:

```sql
SELECT t.name, COUNT(p.player_id) AS players
FROM teams t
LEFT JOIN players p ON p.team_id = t.team_id
GROUP BY t.team_id
ORDER BY players ASC;
```

Latest strength table:

```sql
SELECT t.name, s.overall_rating, s.attack_rating, s.defense_rating, s.uncertainty
FROM team_strength_snapshots s
JOIN teams t ON t.team_id = s.team_id
WHERE s.rating_date = (SELECT MAX(rating_date) FROM team_strength_snapshots)
ORDER BY s.overall_rating DESC;
```
