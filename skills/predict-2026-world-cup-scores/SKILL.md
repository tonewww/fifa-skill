---
name: predict-2026-world-cup-scores
description: Build, refresh, audit, and use a 2026 FIFA World Cup team/player database and probabilistic score prediction model. Use when Codex needs to collect or update 2026 World Cup teams, squads, fixtures, player profiles, injuries, historical results, ratings, style matchups, strength tables, win/draw/loss probabilities, likely scorelines, or matchup-specific analysis between two national teams.
---

# Predict 2026 World Cup Scores

## Overview

Use this skill to create a fresh, source-tracked 2026 World Cup data set and turn it into a transparent match prediction. Treat every prediction as probabilistic: report uncertainty, data freshness, assumptions, and model limits; do not present outputs as betting advice.

## Core Workflow

1. Refresh sources before analysis.
   - Read [references/data-sources.md](references/data-sources.md).
   - Prefer FIFA official tournament pages, squad PDFs, match reports, and rankings for canonical 2026 data.
   - Browse or otherwise verify current squads, injuries, suspensions, fixtures, and results before predicting a live or upcoming match.

2. Build or open the database.
   - Read [references/database-schema.md](references/database-schema.md) for table semantics.
   - Initialize a project database:

```bash
python3 skills/predict-2026-world-cup-scores/scripts/init_database.py --db data/worldcup2026.sqlite --with-reference-sources
```

3. Ingest official team and player baseline data.
   - Download/archive FIFA SquadLists PDF, then parse all teams and players into local CSV and SQLite:

```bash
python3 skills/predict-2026-world-cup-scores/scripts/fetch_official_sources.py --db data/worldcup2026.sqlite --out-dir data/raw --source fifa_squad_lists_pdf
python3 skills/predict-2026-world-cup-scores/scripts/ingest_fifa_squad_pdf.py --pdf data/raw/YYYY-MM-DD/fifa-squad-lists-pdf.pdf --db data/worldcup2026.sqlite --out-dir data/normalized --replace
```

4. Import curated enhancement data.
   - Normalize teams, players, fixtures, results, FIFA rankings, xG, injuries, lineups, player ratings, tactical plans, style profiles, and external metrics into CSV files.
   - Import each CSV with:

```bash
python3 skills/predict-2026-world-cup-scores/scripts/import_csv.py --db data/worldcup2026.sqlite --table teams --csv data/teams.csv
```
   - For FIFA ranking CSVs, prefer the dedicated importer so it can also sync latest official ranks back to `teams`:

```bash
python3 skills/predict-2026-world-cup-scores/scripts/ingest_fifa_rankings.py --db data/worldcup2026.sqlite --csv data/fifa_rankings.csv --ranking-date 2026-06-11 --sync-teams
```

   - After importing player ratings, injuries, lineups, or tactical plans, apply them to baseline player/style fields:

```bash
python3 skills/predict-2026-world-cup-scores/scripts/apply_enhancements.py --db data/worldcup2026.sqlite
```

   - Import public club/league event data when richer player-role, tactical, pressing, xG, and per-90 features are needed for model training. StatsBomb Open Data is attribution-required open data; use it for feature-behavior training, not as a direct national-team strength replacement.

```bash
python3 skills/predict-2026-world-cup-scores/scripts/ingest_statsbomb_open_data.py --db data/worldcup2026.sqlite --competition-id 9 --season-id 281 --max-matches 20 --male-only --club-only
```

   - After importing international results and optional club/event data, build a persistent weighted training cache. This avoids repeatedly scanning raw sources during training and gives FIFA World Cup main-tournament results slightly higher sample weight than qualifiers, friendlies, and club/league cups.

```bash
python3 skills/predict-2026-world-cup-scores/scripts/build_training_dataset.py --db data/worldcup2026.sqlite --since 2018-01-01 --until 2026-06-21 --include-club --replace
```

   - After importing club data, unify provider club players with World Cup squad players and merge club-derived role features at low weight. This writes canonical links to `unified_players` / `player_identity_links`, keeps detailed role traits in `player_feature_snapshots`, adds low-weight rows to `player_ratings`, optionally caches the feature priors on `players`, and blends team-level role signals into `team_style_profiles`.

```bash
python3 skills/predict-2026-world-cup-scores/scripts/merge_club_player_features.py --db data/worldcup2026.sqlite --min-link-confidence 0.70 --min-feature-minutes 90 --player-feature-weight 0.18 --team-style-weight 0.12 --rating-date 2026-06-21 --profile-date 2026-06-21 --apply-to-players
```

   - Treat auto-linked club players as unverified low-weight priors until manually reviewed. Strong role signals such as high pressing, ball progression, box presence, shot quality, key passing, duels, and defensive actions should enrich the model, not override official squads, injuries, lineups, or national-team form.

5. Validate the data set.

```bash
python3 skills/predict-2026-world-cup-scores/scripts/validate_database.py --db data/worldcup2026.sqlite --strict
```

6. Build the strength table.
   - Read [references/modeling-framework.md](references/modeling-framework.md) for weights and formulas.
   - Extract richer team features after importing squads/results/enhancements. This derives formation tendency, tempo, pressing, buildup, transition, set-piece, goalkeeper, cohesion, and a generic tactical plan from player traits and recent results.
   - If club-role features are available, run `merge_club_player_features.py` before this step or rerun it after extraction; `club_feature_merge` profiles have priority on the same profile date.

```bash
python3 skills/predict-2026-world-cup-scores/scripts/extract_team_features.py --db data/worldcup2026.sqlite --profile-date 2026-06-20 --recent-limit 12
```

   - Rebuild after every material update to FIFA ranking, recent results/xG, player ratings, injuries, lineups, or tactical plans.

```bash
python3 skills/predict-2026-world-cup-scores/scripts/build_strength_table.py --db data/worldcup2026.sqlite --rating-date 2026-06-20
```

7. Backtest and optimize when completed matches are available.
   - Import public or official historical results first when the local `fixtures` and `team_results` tables lack samples. The public importer is useful for calibration pressure tests; strict pre-match validation still requires archived historical snapshots.

```bash
python3 skills/predict-2026-world-cup-scores/scripts/import_historical_results.py --db data/worldcup2026.sqlite --since 2021-01-01 --until 2026-06-20 --raw-out data/raw/2026-06-20/international_results.csv --replace-source
```

   - During tournament matchdays, ingest just-finished FOX/FIFA score pages or boxscore URLs before recalibrating. FOX Nuxt score fields are index references; use the dedicated ingester instead of ad hoc parsing so final scores, xG, shots, shots on target, and possession are archived consistently. If only a score page is available, the script still stores final scores with source notes and leaves xG/shot fields empty.

```bash
python3 skills/predict-2026-world-cup-scores/scripts/ingest_fox_boxscores.py --db data/worldcup2026.sqlite --date 2026-06-24 --score-url "https://www.foxsports.com/soccer/fifa-world-cup/scores?date=2026-06-24" --boxscore-url "https://www.foxsports.com/soccer/fifa-world-cup-men-portugal-vs-uzbekistan-jun-24-2026-game-boxscore-647660?tab=boxscore"
```

   - If a day has already been verified from FIFA/media match reports, store those final scores in a local results JSON and ingest that instead of re-parsing a fragile live page.
   - For knockout matches, store the 90-minute regulation score in `home_score` / `away_score`, because the exact-score prediction market and model are regulation-time scorelines. Keep extra-time scores, penalty scores, and the advancing team in optional JSON fields and in `notes`. Treat model training WDL as the 90-minute result, while using advancement notes only for bracket context.

```bash
python3 skills/predict-2026-world-cup-scores/scripts/ingest_results_json.py --db data/worldcup2026.sqlite --date 2026-06-26 --results-json data/results/2026-06-26.json
```

```bash
python3 skills/predict-2026-world-cup-scores/scripts/analyze_formation_matchups.py --db data/worldcup2026.sqlite --min-sample 3
python3 skills/predict-2026-world-cup-scores/scripts/backtest_model.py --db data/worldcup2026.sqlite --use-training-cache
python3 skills/predict-2026-world-cup-scores/scripts/optimize_model_parameters.py --db data/worldcup2026.sqlite --grid coarse --use-training-cache --write-best
```
   - The optimizer also searches WDL-prior-to-score calibration, high-score tail boosts, 0-0/1-1 protection, both-teams-scoring boost, and adaptive stage/round parameters. Do not hard-code knockout score suppression; let completed same-stage samples and current matchup openness decide whether the round should lower, neutralize, or slightly raise total-goal expectation.
   - For daily tournament iteration, first compare yesterday's saved odds-analysis JSON against a source-backed results JSON. Review favorite hit rate, exact-score Top 8 coverage, actual-score probability, Brier/log-loss, and whether the publishing recommendation changed the model's raw top score in a harmful way. Use that review to decide whether to adjust parameters, recommendation rules, or data freshness before predicting the next slate.

```bash
python3 skills/predict-2026-world-cup-scores/scripts/review_completed_matches.py --analysis-json data/reports/odds-ev-YYYY-MM-DD.json --results-json data/results/YYYY-MM-DD.json --format markdown --output data/reports/postmatch-review-YYYY-MM-DD.md
```

   - When the review shows the same failure mode across a slate, prefer a narrow fix: flat favorite misses usually point to WDL calibration or stale team strength; missed draws point to draw protection/low-score correlation; missed high-total wins point to openness and tail calibration; recommendation-score misses with a correct WDL favorite point to publishing rules rather than the underlying model.
   - Publishing rule after the 2026-06-26 review: keep the headline score aligned with the blended WDL favorite by default, but preserve an original draw top score when the WDL favorite is weak, the favorite edge is small, and the draw/low-score market structure is strong. Do not force a 2-1 or 1-2 recommendation just to match a low-confidence WDL favorite.
   - Publishing rule after the 2026-06-27 review: do not collapse WDL relationships into only win/loss labels. If draw is the highest probability, publish `平局优先`; if draw is close to a weak favorite or the protected headline score is a draw, publish `主胜防平` / `客胜防平` and allow the probability-first parlay block to use draw scorelines. When favorites win by high margins that were outside Top 8, inspect openness, strength mismatch, and group-stage goal-difference pressure before suppressing 3+ goal tails.
   - Publishing/model rule after the 2026-06-28 review: the score model must not force every group-stage favorite into 1-0/2-1/0-1 shapes. Use same-stage completed results to adapt the score-tail shape: elevated 4+ goal, 5+ goal, and 3+ margin frequencies should increase high-total and very-high-total score mass, while conservative stages can dampen it. Keep lambda stage multipliers adaptive rather than hard-capped; apply high-score evidence mainly to distribution shape and headline-score selection. In the publishing layer, if team-strength edge, tactical openness, group incentive, and exact-score market tail all point to a wider win, allow 3-0/3-1/4-1 style recommendations inside the WDL-favored outcome.
   - Publishing/model rule after the 2026-07-01 review: WDL direction can be correct while the score tail is still too narrow. If the favorite is above roughly 52%, exact-score market prices 3-0/3-1 close to the normal favorite scores, and the underdog clean-sheet/low-xG profile is weak, keep a 3-0 or 3-1 tail candidate inside the displayed Top 8/Top 3 pool instead of letting 1-1/2-2 draw shapes crowd out every wider favorite win. This is a publishing/distribution-shape correction, not a reason to inflate all favorites.
   - Publishing/model rule after the 2026-07-02 review: score prediction and score backtests use 90-minute regulation scorelines, not extra-time or penalty scores. The Belgium-Senegal match is therefore 2-2 for score-model training, with 3-2 after extra time only stored as bracket context. If a weak favorite is below roughly 50%, the draw probability is above roughly 22%, and Top 3 exact scores include 1-1 or 2-2, publish `主胜防平` / `客胜防平` and allow probability-first materials to surface the draw score instead of forcing every headline or parlay leg into the narrow favorite win.

8. Add group standing and qualification incentive context before predicting group-stage slates.
   - Ingest the latest completed match results before analyzing the next odds JSON.
   - Compute current group standings from completed World Cup group fixtures: played, points, goals for/against, goal difference, and rank. If `teams.group_name` is missing, infer the mini-group from the current slate plus already-completed group-stage fixtures instead of ignoring standings.
   - Translate standings into modest tactical incentives:
     - teams on 3 points with healthy goal difference can value a draw and control risk;
     - teams on 0 points need to chase points, and if goal difference is already poor they may also chase margin;
     - teams on 1 point need to win but should not be treated as reckless by default;
     - when the local data lacks the group's first-round result, mark the incentive as unknown and avoid a fake correction.
   - Apply these incentives as small WDL and score-shape adjustments only. They should explain likely tactical choice and game state pressure, not override team strength, market odds, injuries, or matchup effects.
   - In Markdown odds reports, show the current points/goal-difference context in the WDL relationship table so draw protection, conservative favorites, and high-total tails are auditable.

9. Predict a match.
   - Run the script, then explain the result in the format from [references/prediction-output.md](references/prediction-output.md).

```bash
python3 skills/predict-2026-world-cup-scores/scripts/predict_match.py --db data/worldcup2026.sqlite --team-a BRA --team-b FRA --stage "Group Stage" --format report
```

10. Analyze exact-score odds when the user provides a local odds JSON.
   - Treat odds as a calibration/reference layer, not betting advice.
   - Use a blended probability when exact-score odds are supplied; default is model 70%, market-implied 30%.
   - Use the default `--mode strength-aware` for parlays so matches with clear strength/market favorites stay inside the favored outcome group while still considering exact-score odds. The script treats either an absolute favorite probability or a favorite-vs-runner-up gap as a clear edge.
   - For Markdown output, present exactly three sections: match win/draw/loss relationships, each match's scoreline probability/expected-value table, and `N 串 1` Top 9, where `N` is the number of matches in the input odds JSON.
   - The win/draw/loss relationship table must not hide draws. If draw is the highest WDL probability, label the relationship `平局优先`. If draw is close to a weak home/away favorite or the selected headline score is a protected draw, label it `主胜防平` or `客胜防平` and show both the favorite probability and draw probability.
   - Use `--stake` to set the stake unit for expected return/profit calculations. Use `--show-all-scores` when the user asks for a complete odds-table calculation, including `胜其它` / `平其它` / `负其它` rows.
   - Split the parlay Top 9 into three blocks: first 3 by win-probability first and odds second; next 3 by odds first while retaining a medium probability/value floor; final 3 by expected net profit / ROI with high-variance caveats.
   - Keep every leg in the probability-first block aligned with that match's published relationship, not blindly with raw WDL max. For `平局优先` and protected `主胜防平` / `客胜防平` matches, allow the probability-first block to use the draw group. Let odds-first and EV-first allow bounded deviations from clear-favorite outcomes (`--odds-first-max-clear-favorite-deviations`, `--ev-first-max-clear-favorite-deviations`) so strength mismatches still matter without suppressing every value candidate.

```bash
python3 skills/predict-2026-world-cup-scores/scripts/analyze_score_odds_parlay.py --db data/worldcup2026.sqlite --odds-json pl/YYYY-MM-DD.json --top 9 --mode strength-aware --stake 100 --show-all-scores
```

## Modeling Rules

- Never invent missing data. Mark unknowns explicitly and increase uncertainty.
- Treat the FIFA SquadLists PDF ingestion as the local baseline for all participating teams and players. Use it to bootstrap predictions, then overlay better ranking, form, xG, injury, lineup, and player-rating data when available.
- Keep official FIFA rankings in `fifa_rankings`; copy latest official rows into `teams` only as a convenience cache.
- Keep source provenance for every imported data set; include retrieval time and URL where possible.
- Separate observed facts from model judgments. Facts live in source-backed tables; judgments live in ratings, style profiles, matchup adjustments, and prediction records.
- Store third-party player ratings, xG, injuries, and expected lineups only when licensing allows local derived values or the user provides the data. Do not scrape or reproduce restricted provider data beyond permitted use.
- Prefer recent, opponent-adjusted performance over raw win/loss narratives.
- Include matchup effects ("ke zhi"/style counters): pressing versus buildup resistance, transition speed versus high defensive line, set pieces versus aerial defense, low block versus shot creation, wing overloads versus fullback weakness, and goalkeeper profile versus crossing volume.
- Use official lineups when available. Before official lineups, model expected lineups and apply higher uncertainty.
- Backtest score distributions and formation matchups when enough results are available; write optimized weights to `model_parameters` rather than hard-coding them.
- When completed matches show an underweighted score tail, tune distribution-shape factors such as high-total boosts, both-teams-scoring boosts, open-draw boosts, nil-nil dampening, and low-total one-goal-win dampening; then re-run backtests before publishing.
- Use stage/round context as an adaptive factor. Group and knockout stages can differ, but same-stage completed results should drive the multiplier; when no same-stage evidence exists, keep the stage multiplier neutral and state the uncertainty.

## Resources

### scripts

- `fetch_official_sources.py`: archive FIFA official pages/PDFs into dated raw files.
- `import_historical_results.py`: import public international result rows into `fixtures` and `team_results` for calibration/backtesting.
- `ingest_fox_boxscores.py`: archive FOX Sports score pages/boxscores and ingest completed World Cup scores plus available xG, shots, shots on target, and possession; resolves Nuxt index references before reading score fields.
- `ingest_results_json.py`: ingest manually verified/source-backed final scores from local JSON into `fixtures` and `team_results`.
- `ingest_fifa_squad_pdf.py`: parse FIFA SquadLists PDF into local 48-team and 1248-player baseline data.
- `ingest_fifa_rankings.py`: import FIFA ranking CSVs or best-effort official ranking API responses and sync latest official ranks to teams.
- `ingest_statsbomb_open_data.py`: import StatsBomb Open Data club competitions, matches, lineups, events, team/player match stats, and derived player feature snapshots for training tactical/player feature relationships.
- `build_training_dataset.py`: materialize weighted `training_matches` from local fixtures and `club_matches`; World Cup main-tournament rows receive a modest higher weight, while club rows are stored as lower-weight feature/score-shape samples.
- `merge_club_player_features.py`: unify World Cup and club provider player identities, write low-weight club-derived player feature snapshots/ratings, update player feature caches, and blend player-role traits into team style profiles.
- `init_database.py`: create the SQLite database and source-tracking tables.
- `import_csv.py`: import normalized CSV files into known tables.
- `apply_enhancements.py`: apply latest player ratings, injuries, lineups, and tactical plans to baseline fields.
- `extract_team_features.py`: derive squad/role/form-based style features and generic tactical plans.
- `validate_database.py`: audit table presence, team/player coverage, ratings, styles, and stale sources.
- `build_strength_table.py`: aggregate player/team data into dated team ratings.
- `export_strength_table.py`: export the latest strength table as Markdown or CSV.
- `analyze_formation_matchups.py`: aggregate completed matches by formation pairing for tactical priors.
- `analyze_score_odds_parlay.py`: combine exact-score odds with model probabilities, calculate expected return/profit/ROI, and rank multi-leg scoreline candidates.
- `review_completed_matches.py`: compare saved pre-match odds analysis against completed results and summarize WDL hit rate, scoreline coverage, calibration metrics, recommendation-rule drift, and optimization priorities.
- `backtest_model.py`: score stored predictions against completed fixtures.
- `optimize_model_parameters.py`: grid-search model parameters and optionally store the best row.
- `predict_match.py`: compute win/draw/loss probabilities and likely scorelines.

### references

- `data-sources.md`: source hierarchy, freshness windows, and ingestion rules.
- `database-schema.md`: schema, data dictionary, and minimum completeness criteria.
- `modeling-framework.md`: strength table, matchup adjustments, expected-goals model, and validation approach.
- `prediction-output.md`: required response format and uncertainty language.
