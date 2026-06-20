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

5. Validate the data set.

```bash
python3 skills/predict-2026-world-cup-scores/scripts/validate_database.py --db data/worldcup2026.sqlite --strict
```

6. Build the strength table.
   - Read [references/modeling-framework.md](references/modeling-framework.md) for weights and formulas.
   - Extract richer team features after importing squads/results/enhancements. This derives formation tendency, tempo, pressing, buildup, transition, set-piece, goalkeeper, cohesion, and a generic tactical plan from player traits and recent results.

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

```bash
python3 skills/predict-2026-world-cup-scores/scripts/analyze_formation_matchups.py --db data/worldcup2026.sqlite --min-sample 3
python3 skills/predict-2026-world-cup-scores/scripts/backtest_model.py --db data/worldcup2026.sqlite
python3 skills/predict-2026-world-cup-scores/scripts/optimize_model_parameters.py --db data/worldcup2026.sqlite --grid coarse --write-best
```

8. Predict a match.
   - Run the script, then explain the result in the format from [references/prediction-output.md](references/prediction-output.md).

```bash
python3 skills/predict-2026-world-cup-scores/scripts/predict_match.py --db data/worldcup2026.sqlite --team-a BRA --team-b FRA --stage "Group Stage" --format report
```

9. Analyze exact-score odds when the user provides a local odds JSON.
   - Treat odds as a calibration/reference layer, not betting advice.
   - Use a blended probability when exact-score odds are supplied; default is model 70%, market-implied 30%.
   - Use the default `--mode strength-aware` for parlays so matches with clear strength/market favorites stay inside the favored outcome group while still considering exact-score odds. The script treats either an absolute favorite probability or a favorite-vs-runner-up gap as a clear edge.
   - For Markdown output, present exactly three sections: match win/draw/loss relationships, each match's scoreline probability/expected-value table, and four-leg parlay Top 9.
   - Use `--stake` to set the stake unit for expected return/profit calculations. Use `--show-all-scores` when the user asks for a complete odds-table calculation, including `胜其它` / `平其它` / `负其它` rows.
   - Split the parlay Top 9 into three blocks: first 3 by win-probability first and odds second; next 3 by odds first while retaining a medium probability/value floor; final 3 by expected net profit / ROI with high-variance caveats.
   - Keep the probability-first block inside clear favorites. Let odds-first and EV-first allow bounded deviations from clear-favorite outcomes (`--odds-first-max-clear-favorite-deviations`, `--ev-first-max-clear-favorite-deviations`) so strength mismatches still matter without suppressing every value candidate.

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

## Resources

### scripts

- `fetch_official_sources.py`: archive FIFA official pages/PDFs into dated raw files.
- `import_historical_results.py`: import public international result rows into `fixtures` and `team_results` for calibration/backtesting.
- `ingest_fifa_squad_pdf.py`: parse FIFA SquadLists PDF into local 48-team and 1248-player baseline data.
- `ingest_fifa_rankings.py`: import FIFA ranking CSVs or best-effort official ranking API responses and sync latest official ranks to teams.
- `init_database.py`: create the SQLite database and source-tracking tables.
- `import_csv.py`: import normalized CSV files into known tables.
- `apply_enhancements.py`: apply latest player ratings, injuries, lineups, and tactical plans to baseline fields.
- `extract_team_features.py`: derive squad/role/form-based style features and generic tactical plans.
- `validate_database.py`: audit table presence, team/player coverage, ratings, styles, and stale sources.
- `build_strength_table.py`: aggregate player/team data into dated team ratings.
- `export_strength_table.py`: export the latest strength table as Markdown or CSV.
- `analyze_formation_matchups.py`: aggregate completed matches by formation pairing for tactical priors.
- `analyze_score_odds_parlay.py`: combine exact-score odds with model probabilities, calculate expected return/profit/ROI, and rank multi-leg scoreline candidates.
- `backtest_model.py`: score stored predictions against completed fixtures.
- `optimize_model_parameters.py`: grid-search model parameters and optionally store the best row.
- `predict_match.py`: compute win/draw/loss probabilities and likely scorelines.

### references

- `data-sources.md`: source hierarchy, freshness windows, and ingestion rules.
- `database-schema.md`: schema, data dictionary, and minimum completeness criteria.
- `modeling-framework.md`: strength table, matchup adjustments, expected-goals model, and validation approach.
- `prediction-output.md`: required response format and uncertainty language.
