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

- `player_id`, `unified_player_id`, `team_id`, `name`, `national_team_position`, `club_position`, `club`, `status`.
- `position`: legacy compatibility field. Keep it in sync with `national_team_position` until all importers and CSVs have migrated.
- `rating_overall`: 0-100 estimate or provider-derived value.
- Role-specific ratings: `rating_attack`, `rating_defense`, `rating_possession`, `rating_transition`, `rating_set_piece`, `rating_goalkeeping`, `rating_fitness`.
- Optional low-weight role-feature caches from linked club data: `feature_pressing`, `feature_progression`, `feature_box_presence`, `feature_shot_quality`, `feature_key_passing`, `feature_duel_activity`, `feature_defensive_activity`, `feature_sample_minutes`, `feature_source_weight`.
- `minutes_expected`: projected role weight.

Completion rule: usually 26 players per final squad. Fewer than 23 players is not prediction-ready.

Position guidance:

- Use `national_team_position` for squad role, formation inference, and national-team strength aggregation.
- Use `club_position` for provider-derived club role, player-feature interpretation, and club-to-national transfer checks.
- Use `GK`, `DF`, `MF`, `FW` for broad national-team grouping; provider club positions can be more specific, such as `Right Wing Back`, `Center Forward`, or `Defensive Midfield`.
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

`statsbomb-derived-low-weight` rows are generated from linked club event features by `merge_club_player_features.py`. They are low-weight priors for role behavior, not a complete licensed player-rating feed.

### `unified_players` and `player_identity_links`

Canonical identity layer for footballers across national-team and club-provider records.

Use this layer to avoid treating World Cup squad players and club-provider players as separate people:

- `unified_players`: one canonical person row, seeded from official World Cup squad players or reviewed provider identities.
- `players.unified_player_id`: national-team squad row linked to the canonical person.
- `club_players.unified_player_id`: provider club-player row linked to the canonical person.
- `player_identity_links`: auditable link rows with `source_table`, `source_player_id`, `provider`, `confidence`, `match_method`, and `verified`.

Official squad rows can be `verified=1`. Auto-linked club-provider rows should remain `verified=0` until reviewed, and their feature impact should stay low weight.

### `player_feature_snapshots`

Canonical player-role feature snapshots after club-provider features have been mapped to `unified_players`.

Important fields:

- `provider`, `snapshot_date`, `sample_minutes`, `source_weight`.
- Role scores: `pressing_score`, `progression_score`, `box_presence_score`, `shot_quality_score`, `key_passing_score`, `duel_activity_score`, `defensive_activity_score`.
- Raw per-90 context: `xg_per90`, `shots_per90`, `key_passes_per90`, `pressures_per90`, `carries_per90`, `dribbles_per90`, `touches_box_per90`, `duels_per90`, `def_actions_per90`.

Use this table for explanation, backtesting, and model feature enrichment. `players.feature_*` stores only the latest low-weight cache for fast aggregation.

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

### `training_matches`

Persistent weighted training cache built from local match tables.

Use this table to avoid reloading or rescanning raw result/event sources on every backtest or optimization run. Rebuild it after importing new historical results or event-level data:

```bash
python3 skills/predict-2026-world-cup-scores/scripts/build_training_dataset.py --db data/worldcup2026.sqlite --since 2018-01-01 --until 2026-06-21 --include-club --replace
```

Important fields:

- `source_table`, `source_match_id`: source-row lineage, unique together.
- `domain`: `international` for mapped national-team fixtures that can feed the current national-team predictor; `statsbomb_international` for provider international event samples not yet mapped to FIFA team IDs; `club` for club/league samples.
- `competition_family`: normalized bucket such as `world_cup`, `world_cup_qualification`, `continental_cup`, `qualifier_or_nations`, `champions_league`, `club_league`, or `friendly`.
- `score_a`, `score_b`, optional `xg_a`, `xg_b`, `shots_a`, `shots_b`: training targets and richer chance-quality context when available.
- `is_world_cup`, `is_knockout`, `neutral_site`: tournament/context flags.
- `data_quality`: rough source richness signal.
- `sample_weight`: training weight. World Cup main-tournament rows should be modestly higher than other international samples; friendlies and club samples should be lower unless used only for tactical/score-shape feature learning.

Current national-team `backtest_model.py --use-training-cache` and `optimize_model_parameters.py --use-training-cache` read only `domain='international'`, because `predict_match.py` expects team IDs from `teams`. `statsbomb_international` and `club` rows remain valuable for event-feature, archetype, xG, and score-shape models, but should not be passed directly into the FIFA-team predictor until provider teams are mapped.

### Club/League Training Tables

Use `club_*` tables to store public or licensed club-match data for feature learning, archetype backtesting, and player-role enrichment. Keep this layer separate from national-team facts; transfer learned effects only as shrunk priors or derived player/tactical features.

`club_competitions` stores provider competition-season rows, such as StatsBomb `competition_id` + `season_id`.

`club_teams` and `club_players` store provider team/player identities. These are linked to World Cup squad players through `unified_players` and `player_identity_links`, not by assuming provider IDs equal national-team IDs. Link them with careful name/club review; auto-links should remain unverified until checked.

`club_matches` stores club fixtures and results.

`club_lineups` and `club_lineup_players` store provider lineups, positions, starters, and available minutes when known.

`club_team_match_stats` stores event-derived team stats:

- goals, shots, xG, shots on target
- passes, completed passes, passes under pressure
- carries, dribbles, pressures, counterpressures
- duels, interceptions, blocks, clearances
- fouls, corners, crosses, deep progressions, box touches
- set-piece shots and open-play shots

`club_player_match_stats` stores event-derived player match stats:

- minutes, goals, shots, xG
- passing, key passes, assists
- carries, dribbles, pressures, counterpressures
- defensive actions, duels, fouls, box touches

`club_player_feature_snapshots` stores per-90 player feature summaries:

- attacking: goals/xG/shots/key passes/box touches
- possession: passing volume, pass completion, carries, dribbles
- defensive: defensive actions, duels, pressures
- transition: carries, pressures, dribbles

StatsBomb Open Data rows require attribution when publishing analysis. Open Bundesliga 2023/2024 rows currently imported from StatsBomb can be skewed toward a subset of teams, so use them as event-feature examples and tactical priors rather than a complete league baseline unless coverage is confirmed.

### `backtest_runs` and `backtest_predictions`

Backtest audit tables.

`backtest_runs` stores aggregate metrics:

- `brier_score`
- `log_loss`
- `mae_goals`
- `exact_score_accuracy`
- `top8_score_hit_rate`
- `favorite_accuracy`
- `avg_actual_outcome_probability`
- `calibration_json`: probability-bucket diagnostics for favorite confidence versus actual hit rate

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
