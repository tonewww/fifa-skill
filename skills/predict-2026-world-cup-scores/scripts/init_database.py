#!/usr/bin/env python3
"""Initialize the SQLite database for the 2026 World Cup predictor."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from common import MODEL_VERSION, TABLE_COLUMNS, connect, ensure_parent, now_utc


DDL = [
    """
    CREATE TABLE IF NOT EXISTS sources (
        source_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        url TEXT,
        category TEXT,
        publisher TEXT,
        retrieved_at TEXT,
        published_at TEXT,
        freshness_days INTEGER,
        license_note TEXT,
        reliability TEXT,
        notes TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS teams (
        team_id TEXT PRIMARY KEY,
        fifa_code TEXT UNIQUE,
        name TEXT NOT NULL,
        short_name TEXT,
        confederation TEXT,
        group_name TEXT,
        is_host INTEGER DEFAULT 0,
        qualification_method TEXT,
        seed_pot INTEGER,
        coach TEXT,
        fifa_rank INTEGER,
        fifa_points REAL,
        squad_status TEXT,
        last_verified_at TEXT,
        source_id TEXT REFERENCES sources(source_id),
        notes TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS players (
        player_id TEXT PRIMARY KEY,
        unified_player_id TEXT,
        team_id TEXT NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
        fifa_code TEXT,
        name TEXT NOT NULL,
        display_name TEXT,
        shirt_number INTEGER,
        position TEXT,
        national_team_position TEXT,
        club_position TEXT,
        club TEXT,
        league TEXT,
        birth_date TEXT,
        age INTEGER,
        caps INTEGER,
        goals INTEGER,
        dominant_foot TEXT,
        height_cm INTEGER,
        market_value_eur REAL,
        rating_overall REAL,
        rating_attack REAL,
        rating_defense REAL,
        rating_possession REAL,
        rating_transition REAL,
        rating_set_piece REAL,
        rating_goalkeeping REAL,
        rating_fitness REAL,
        feature_pressing REAL,
        feature_progression REAL,
        feature_box_presence REAL,
        feature_shot_quality REAL,
        feature_key_passing REAL,
        feature_duel_activity REAL,
        feature_defensive_activity REAL,
        feature_sample_minutes REAL,
        feature_source_weight REAL,
        status TEXT DEFAULT 'available',
        minutes_expected REAL,
        last_verified_at TEXT,
        source_id TEXT REFERENCES sources(source_id),
        notes TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fixtures (
        match_id TEXT PRIMARY KEY,
        competition TEXT DEFAULT 'FIFA World Cup 2026',
        stage TEXT,
        group_name TEXT,
        match_date TEXT,
        venue TEXT,
        city TEXT,
        country TEXT,
        team_a_id TEXT REFERENCES teams(team_id),
        team_b_id TEXT REFERENCES teams(team_id),
        score_a INTEGER,
        score_b INTEGER,
        status TEXT,
        source_id TEXT REFERENCES sources(source_id),
        notes TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS team_results (
        result_id TEXT PRIMARY KEY,
        match_date TEXT NOT NULL,
        team_id TEXT NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
        opponent_team_id TEXT REFERENCES teams(team_id),
        venue_type TEXT,
        competition TEXT,
        is_neutral INTEGER DEFAULT 1,
        goals_for INTEGER,
        goals_against INTEGER,
        xg_for REAL,
        xg_against REAL,
        shots INTEGER,
        shots_on_target INTEGER,
        possession REAL,
        elo_before REAL,
        elo_after REAL,
        source_id TEXT REFERENCES sources(source_id),
        notes TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fifa_rankings (
        ranking_id TEXT PRIMARY KEY,
        ranking_date TEXT NOT NULL,
        ranking_type TEXT DEFAULT 'official',
        team_id TEXT REFERENCES teams(team_id) ON DELETE CASCADE,
        fifa_code TEXT,
        rank INTEGER,
        points REAL,
        previous_rank INTEGER,
        previous_points REAL,
        rank_change INTEGER,
        confederation TEXT,
        source_id TEXT REFERENCES sources(source_id),
        notes TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS player_ratings (
        rating_id TEXT PRIMARY KEY,
        player_id TEXT REFERENCES players(player_id) ON DELETE CASCADE,
        team_id TEXT REFERENCES teams(team_id) ON DELETE CASCADE,
        provider TEXT NOT NULL,
        rating_date TEXT NOT NULL,
        overall REAL,
        attack REAL,
        defense REAL,
        possession REAL,
        transition REAL,
        set_piece REAL,
        goalkeeping REAL,
        fitness REAL,
        market_value_eur REAL,
        minutes_recent REAL,
        source_id TEXT REFERENCES sources(source_id),
        notes TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS injuries (
        injury_id TEXT PRIMARY KEY,
        player_id TEXT REFERENCES players(player_id) ON DELETE CASCADE,
        team_id TEXT REFERENCES teams(team_id) ON DELETE CASCADE,
        status TEXT,
        severity TEXT,
        injury_type TEXT,
        expected_return TEXT,
        availability_pct REAL,
        impact_rating REAL,
        verified_at TEXT,
        source_id TEXT REFERENCES sources(source_id),
        notes TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS lineups (
        lineup_id TEXT PRIMARY KEY,
        match_id TEXT REFERENCES fixtures(match_id) ON DELETE SET NULL,
        team_id TEXT NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
        opponent_team_id TEXT REFERENCES teams(team_id) ON DELETE SET NULL,
        lineup_type TEXT DEFAULT 'expected',
        as_of TEXT NOT NULL,
        formation TEXT,
        source_id TEXT REFERENCES sources(source_id),
        notes TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS lineup_players (
        lineup_player_id TEXT PRIMARY KEY,
        lineup_id TEXT NOT NULL REFERENCES lineups(lineup_id) ON DELETE CASCADE,
        player_id TEXT NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
        role TEXT,
        position TEXT,
        is_starter INTEGER DEFAULT 0,
        minutes_expected REAL,
        availability_pct REAL,
        source_id TEXT REFERENCES sources(source_id),
        notes TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tactical_plans (
        plan_id TEXT PRIMARY KEY,
        team_id TEXT NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
        opponent_team_id TEXT REFERENCES teams(team_id) ON DELETE CASCADE,
        as_of_date TEXT NOT NULL,
        formation TEXT,
        defensive_shape TEXT,
        pressing_trigger TEXT,
        buildup_pattern TEXT,
        chance_creation TEXT,
        transition_plan TEXT,
        set_piece_plan TEXT,
        risk_level REAL,
        source_id TEXT REFERENCES sources(source_id),
        notes TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS formation_matchup_stats (
        matchup_id TEXT PRIMARY KEY,
        formation_a TEXT NOT NULL,
        formation_b TEXT NOT NULL,
        sample_size INTEGER DEFAULT 0,
        p_a_win REAL,
        p_draw REAL,
        p_b_win REAL,
        avg_goals_a REAL,
        avg_goals_b REAL,
        scoreline_json TEXT,
        source_id TEXT REFERENCES sources(source_id),
        notes TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS model_parameters (
        parameter_id TEXT PRIMARY KEY,
        model_version TEXT NOT NULL,
        as_of_date TEXT NOT NULL,
        base_goals REAL DEFAULT 1.22,
        home_edge REAL DEFAULT 0.12,
        knockout_drag REAL DEFAULT -0.07,
        attack_weight REAL DEFAULT 0.85,
        overall_weight REAL DEFAULT 0.55,
        keeper_weight REAL DEFAULT 0.25,
        set_piece_weight REAL DEFAULT 0.18,
        fitness_weight REAL DEFAULT 0.12,
        style_weight REAL DEFAULT 1.00,
        formation_weight REAL DEFAULT 0.25,
        openness_baseline_total REAL DEFAULT 2.55,
        recent_goal_openness_weight REAL DEFAULT 0.28,
        formation_openness_weight REAL DEFAULT 0.38,
        style_openness_weight REAL DEFAULT 0.20,
        tactical_openness_weight REAL DEFAULT 0.14,
        openness_max_delta REAL DEFAULT 0.95,
        stage_group_goal_multiplier REAL DEFAULT 1.00,
        stage_round32_goal_multiplier REAL DEFAULT 1.00,
        stage_round16_goal_multiplier REAL DEFAULT 1.00,
        stage_quarter_goal_multiplier REAL DEFAULT 1.00,
        stage_semi_goal_multiplier REAL DEFAULT 1.00,
        stage_final_goal_multiplier REAL DEFAULT 1.00,
        stage_data_weight REAL DEFAULT 0.70,
        stage_sample_half_life REAL DEFAULT 36.00,
        stage_open_match_resistance REAL DEFAULT 0.45,
        wdl_prior_weight REAL DEFAULT 0.65,
        formation_wdl_prior_max_weight REAL DEFAULT 0.35,
        wdl_score_calibration_weight REAL DEFAULT 0.70,
        favorite_score_tilt REAL DEFAULT 0.10,
        draw_score_tilt REAL DEFAULT 0.08,
        notes TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS backtest_runs (
        run_id TEXT PRIMARY KEY,
        run_at TEXT NOT NULL,
        model_version TEXT NOT NULL,
        train_start TEXT,
        train_end TEXT,
        test_start TEXT,
        test_end TEXT,
        sample_size INTEGER,
        brier_score REAL,
        log_loss REAL,
        mae_goals REAL,
        exact_score_accuracy REAL,
        top8_score_hit_rate REAL,
        favorite_accuracy REAL,
        avg_actual_outcome_probability REAL,
        calibration_json TEXT,
        notes TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS backtest_predictions (
        backtest_prediction_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES backtest_runs(run_id) ON DELETE CASCADE,
        match_id TEXT,
        predicted_at TEXT NOT NULL,
        team_a_id TEXT NOT NULL REFERENCES teams(team_id),
        team_b_id TEXT NOT NULL REFERENCES teams(team_id),
        lambda_a REAL,
        lambda_b REAL,
        p_team_a_win REAL,
        p_draw REAL,
        p_team_b_win REAL,
        top_scorelines_json TEXT,
        actual_score_a INTEGER,
        actual_score_b INTEGER,
        actual_outcome TEXT,
        formation_a TEXT,
        formation_b TEXT,
        notes TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS team_style_profiles (
        profile_id TEXT PRIMARY KEY,
        team_id TEXT NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
        profile_date TEXT NOT NULL,
        formation_primary TEXT,
        tempo REAL,
        press_intensity REAL,
        defensive_line REAL,
        buildup_quality REAL,
        transition_attack REAL,
        transition_defense REAL,
        wing_play REAL,
        central_progression REAL,
        set_piece_attack REAL,
        set_piece_defense REAL,
        aerial_strength REAL,
        low_block_attack REAL,
        low_block_defense REAL,
        keeper_sweeper REAL,
        keeper_shot_stopping REAL,
        injury_load REAL,
        cohesion REAL,
        travel_fatigue REAL,
        source_id TEXT REFERENCES sources(source_id),
        notes TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS team_strength_snapshots (
        snapshot_id TEXT PRIMARY KEY,
        team_id TEXT NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
        rating_date TEXT NOT NULL,
        fifa_component REAL,
        squad_quality REAL,
        attack_rating REAL,
        defense_rating REAL,
        possession_rating REAL,
        transition_rating REAL,
        set_piece_rating REAL,
        goalkeeper_rating REAL,
        depth_rating REAL,
        form_rating REAL,
        experience_rating REAL,
        fitness_rating REAL,
        coaching_rating REAL,
        overall_rating REAL,
        uncertainty REAL,
        model_version TEXT,
        source_count INTEGER,
        notes TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS matchup_adjustments (
        adjustment_id TEXT PRIMARY KEY,
        as_of_date TEXT NOT NULL,
        team_a_id TEXT NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
        team_b_id TEXT NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
        affected_team_id TEXT NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
        category TEXT,
        goal_delta REAL DEFAULT 0,
        win_prob_delta REAL DEFAULT 0,
        confidence REAL DEFAULT 0.5,
        rationale TEXT,
        source_id TEXT REFERENCES sources(source_id),
        notes TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS predictions (
        prediction_id TEXT PRIMARY KEY,
        predicted_at TEXT NOT NULL,
        team_a_id TEXT NOT NULL REFERENCES teams(team_id),
        team_b_id TEXT NOT NULL REFERENCES teams(team_id),
        stage TEXT,
        neutral_site INTEGER DEFAULT 1,
        lambda_a REAL,
        lambda_b REAL,
        p_team_a_win REAL,
        p_draw REAL,
        p_team_b_win REAL,
        top_scorelines_json TEXT,
        model_version TEXT,
        data_cutoff TEXT,
        notes TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS training_matches (
        training_match_id TEXT PRIMARY KEY,
        source_table TEXT NOT NULL,
        source_match_id TEXT NOT NULL,
        domain TEXT NOT NULL,
        competition TEXT,
        competition_family TEXT,
        match_date TEXT,
        stage TEXT,
        team_a_id TEXT,
        team_b_id TEXT,
        team_a_name TEXT,
        team_b_name TEXT,
        score_a INTEGER,
        score_b INTEGER,
        xg_a REAL,
        xg_b REAL,
        shots_a INTEGER,
        shots_b INTEGER,
        neutral_site INTEGER DEFAULT 1,
        is_world_cup INTEGER DEFAULT 0,
        is_knockout INTEGER DEFAULT 0,
        data_quality REAL,
        sample_weight REAL DEFAULT 1.0,
        source_id TEXT REFERENCES sources(source_id),
        imported_at TEXT,
        notes TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS club_competitions (
        club_competition_id TEXT PRIMARY KEY,
        provider TEXT NOT NULL,
        provider_competition_id TEXT,
        provider_season_id TEXT,
        country TEXT,
        competition_name TEXT,
        season_name TEXT,
        gender TEXT,
        is_international INTEGER DEFAULT 0,
        match_available TEXT,
        metadata_json TEXT,
        source_id TEXT REFERENCES sources(source_id),
        imported_at TEXT,
        notes TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS club_teams (
        club_team_id TEXT PRIMARY KEY,
        provider TEXT NOT NULL,
        provider_team_id TEXT,
        name TEXT NOT NULL,
        country TEXT,
        gender TEXT,
        source_id TEXT REFERENCES sources(source_id),
        imported_at TEXT,
        notes TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS club_players (
        club_player_id TEXT PRIMARY KEY,
        unified_player_id TEXT,
        provider TEXT NOT NULL,
        provider_player_id TEXT,
        name TEXT NOT NULL,
        nickname TEXT,
        country TEXT,
        primary_position TEXT,
        source_id TEXT REFERENCES sources(source_id),
        imported_at TEXT,
        notes TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS unified_players (
        unified_player_id TEXT PRIMARY KEY,
        canonical_name TEXT NOT NULL,
        display_name TEXT,
        birth_date TEXT,
        primary_national_team_id TEXT REFERENCES teams(team_id),
        primary_position TEXT,
        dominant_foot TEXT,
        height_cm INTEGER,
        source_id TEXT REFERENCES sources(source_id),
        created_at TEXT,
        updated_at TEXT,
        notes TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS player_identity_links (
        link_id TEXT PRIMARY KEY,
        unified_player_id TEXT NOT NULL REFERENCES unified_players(unified_player_id) ON DELETE CASCADE,
        source_table TEXT NOT NULL,
        source_player_id TEXT NOT NULL,
        provider TEXT,
        team_id TEXT REFERENCES teams(team_id),
        club_team_id TEXT REFERENCES club_teams(club_team_id),
        confidence REAL,
        match_method TEXT,
        verified INTEGER DEFAULT 0,
        created_at TEXT,
        notes TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS player_feature_snapshots (
        snapshot_id TEXT PRIMARY KEY,
        unified_player_id TEXT NOT NULL REFERENCES unified_players(unified_player_id) ON DELETE CASCADE,
        player_id TEXT REFERENCES players(player_id) ON DELETE SET NULL,
        team_id TEXT REFERENCES teams(team_id) ON DELETE SET NULL,
        provider TEXT NOT NULL,
        snapshot_date TEXT NOT NULL,
        sample_minutes REAL,
        source_weight REAL,
        pressing_score REAL,
        progression_score REAL,
        box_presence_score REAL,
        shot_quality_score REAL,
        key_passing_score REAL,
        duel_activity_score REAL,
        defensive_activity_score REAL,
        xg_per90 REAL,
        shots_per90 REAL,
        key_passes_per90 REAL,
        pressures_per90 REAL,
        carries_per90 REAL,
        dribbles_per90 REAL,
        touches_box_per90 REAL,
        duels_per90 REAL,
        def_actions_per90 REAL,
        source_id TEXT REFERENCES sources(source_id),
        notes TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS club_matches (
        club_match_id TEXT PRIMARY KEY,
        provider TEXT NOT NULL,
        provider_match_id TEXT,
        club_competition_id TEXT REFERENCES club_competitions(club_competition_id) ON DELETE CASCADE,
        match_date TEXT,
        kick_off TEXT,
        stage TEXT,
        round_name TEXT,
        season_name TEXT,
        home_team_id TEXT REFERENCES club_teams(club_team_id),
        away_team_id TEXT REFERENCES club_teams(club_team_id),
        home_score INTEGER,
        away_score INTEGER,
        match_status TEXT,
        match_week INTEGER,
        metadata_json TEXT,
        source_id TEXT REFERENCES sources(source_id),
        imported_at TEXT,
        notes TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS club_lineups (
        club_lineup_id TEXT PRIMARY KEY,
        club_match_id TEXT NOT NULL REFERENCES club_matches(club_match_id) ON DELETE CASCADE,
        club_team_id TEXT NOT NULL REFERENCES club_teams(club_team_id) ON DELETE CASCADE,
        formation TEXT,
        source_id TEXT REFERENCES sources(source_id),
        imported_at TEXT,
        notes TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS club_lineup_players (
        club_lineup_player_id TEXT PRIMARY KEY,
        club_lineup_id TEXT NOT NULL REFERENCES club_lineups(club_lineup_id) ON DELETE CASCADE,
        club_player_id TEXT NOT NULL REFERENCES club_players(club_player_id) ON DELETE CASCADE,
        club_team_id TEXT REFERENCES club_teams(club_team_id) ON DELETE CASCADE,
        player_name TEXT,
        jersey_number INTEGER,
        position TEXT,
        is_starter INTEGER DEFAULT 0,
        minutes_played REAL,
        source_id TEXT REFERENCES sources(source_id),
        notes TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS club_team_match_stats (
        stat_id TEXT PRIMARY KEY,
        club_match_id TEXT NOT NULL REFERENCES club_matches(club_match_id) ON DELETE CASCADE,
        club_team_id TEXT NOT NULL REFERENCES club_teams(club_team_id) ON DELETE CASCADE,
        opponent_club_team_id TEXT REFERENCES club_teams(club_team_id),
        is_home INTEGER DEFAULT 0,
        goals INTEGER,
        shots INTEGER,
        xg REAL,
        shots_on_target INTEGER,
        passes INTEGER,
        completed_passes INTEGER,
        passes_under_pressure INTEGER,
        carries INTEGER,
        dribbles INTEGER,
        pressures INTEGER,
        counterpressures INTEGER,
        duels INTEGER,
        interceptions INTEGER,
        blocks INTEGER,
        clearances INTEGER,
        fouls_committed INTEGER,
        fouls_won INTEGER,
        corners INTEGER,
        crosses INTEGER,
        deep_progressions INTEGER,
        touches_box INTEGER,
        set_piece_shots INTEGER,
        open_play_shots INTEGER,
        possession_events INTEGER,
        source_id TEXT REFERENCES sources(source_id),
        notes TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS club_player_match_stats (
        stat_id TEXT PRIMARY KEY,
        club_match_id TEXT NOT NULL REFERENCES club_matches(club_match_id) ON DELETE CASCADE,
        club_team_id TEXT REFERENCES club_teams(club_team_id) ON DELETE CASCADE,
        club_player_id TEXT NOT NULL REFERENCES club_players(club_player_id) ON DELETE CASCADE,
        player_name TEXT,
        position TEXT,
        minutes_played REAL,
        goals INTEGER,
        shots INTEGER,
        xg REAL,
        passes INTEGER,
        completed_passes INTEGER,
        key_passes INTEGER,
        assists INTEGER,
        carries INTEGER,
        dribbles INTEGER,
        pressures INTEGER,
        counterpressures INTEGER,
        duels INTEGER,
        interceptions INTEGER,
        blocks INTEGER,
        clearances INTEGER,
        fouls_committed INTEGER,
        fouls_won INTEGER,
        under_pressure_events INTEGER,
        touches_box INTEGER,
        source_id TEXT REFERENCES sources(source_id),
        notes TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS club_player_feature_snapshots (
        snapshot_id TEXT PRIMARY KEY,
        club_player_id TEXT NOT NULL REFERENCES club_players(club_player_id) ON DELETE CASCADE,
        provider TEXT NOT NULL,
        snapshot_date TEXT NOT NULL,
        sample_matches INTEGER,
        minutes_played REAL,
        goals_per90 REAL,
        xg_per90 REAL,
        shots_per90 REAL,
        passes_per90 REAL,
        pass_completion_pct REAL,
        key_passes_per90 REAL,
        pressures_per90 REAL,
        carries_per90 REAL,
        dribbles_per90 REAL,
        duels_per90 REAL,
        def_actions_per90 REAL,
        touches_box_per90 REAL,
        attacking_score REAL,
        possession_score REAL,
        defensive_score REAL,
        transition_score REAL,
        source_id TEXT REFERENCES sources(source_id),
        notes TEXT
    )
    """,
]


INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_players_team ON players(team_id)",
    "CREATE INDEX IF NOT EXISTS idx_results_team_date ON team_results(team_id, match_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_rankings_team_date ON fifa_rankings(team_id, ranking_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_rankings_code_date ON fifa_rankings(fifa_code, ranking_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_player_ratings_player_date ON player_ratings(player_id, rating_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_injuries_team_verified ON injuries(team_id, verified_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_lineups_match_team ON lineups(match_id, team_id, as_of DESC)",
    "CREATE INDEX IF NOT EXISTS idx_lineup_players_lineup ON lineup_players(lineup_id)",
    "CREATE INDEX IF NOT EXISTS idx_tactical_plans_team_opponent ON tactical_plans(team_id, opponent_team_id, as_of_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_formation_matchup ON formation_matchup_stats(formation_a, formation_b)",
    "CREATE INDEX IF NOT EXISTS idx_backtest_predictions_run ON backtest_predictions(run_id)",
    "CREATE INDEX IF NOT EXISTS idx_style_team_date ON team_style_profiles(team_id, profile_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_strength_team_date ON team_strength_snapshots(team_id, rating_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_fixtures_teams ON fixtures(team_a_id, team_b_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_training_matches_source ON training_matches(source_table, source_match_id)",
    "CREATE INDEX IF NOT EXISTS idx_training_matches_date ON training_matches(match_date)",
    "CREATE INDEX IF NOT EXISTS idx_training_matches_domain_weight ON training_matches(domain, sample_weight)",
    "CREATE INDEX IF NOT EXISTS idx_club_matches_competition ON club_matches(club_competition_id, match_date)",
    "CREATE INDEX IF NOT EXISTS idx_club_matches_teams ON club_matches(home_team_id, away_team_id)",
    "CREATE INDEX IF NOT EXISTS idx_club_lineups_match_team ON club_lineups(club_match_id, club_team_id)",
    "CREATE INDEX IF NOT EXISTS idx_club_lineup_players_player ON club_lineup_players(club_player_id)",
    "CREATE INDEX IF NOT EXISTS idx_club_team_stats_match_team ON club_team_match_stats(club_match_id, club_team_id)",
    "CREATE INDEX IF NOT EXISTS idx_club_player_stats_player ON club_player_match_stats(club_player_id)",
    "CREATE INDEX IF NOT EXISTS idx_club_player_features_player_date ON club_player_feature_snapshots(club_player_id, snapshot_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_players_unified ON players(unified_player_id)",
    "CREATE INDEX IF NOT EXISTS idx_club_players_unified ON club_players(unified_player_id)",
    "CREATE INDEX IF NOT EXISTS idx_identity_links_unified ON player_identity_links(unified_player_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_identity_links_source ON player_identity_links(source_table, source_player_id, provider)",
    "CREATE INDEX IF NOT EXISTS idx_player_features_unified_date ON player_feature_snapshots(unified_player_id, snapshot_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_player_features_player_date ON player_feature_snapshots(player_id, snapshot_date DESC)",
]


REFERENCE_SOURCES = [
    (
        "fifa_tournament_hub",
        "FIFA World Cup 2026 tournament hub",
        "https://www.fifa.com/en/tournaments/mens/worldcup/canadamexicousa2026",
        "tournament",
        "FIFA",
        1,
        "official",
    ),
    (
        "fifa_teams",
        "FIFA World Cup 2026 teams",
        "https://www.fifa.com/en/tournaments/mens/worldcup/canadamexicousa2026/teams",
        "teams",
        "FIFA",
        1,
        "official",
    ),
    (
        "fifa_scores_fixtures",
        "FIFA World Cup 2026 scores and fixtures",
        "https://www.fifa.com/en/tournaments/mens/worldcup/canadamexicousa2026/scores-fixtures",
        "fixtures_results",
        "FIFA",
        0,
        "official",
    ),
    (
        "fifa_squad_lists_pdf",
        "FIFA World Cup 2026 official squad lists PDF",
        "https://fdp.fifa.org/assetspublic/ce281/pdf/SquadLists-English.pdf",
        "squads",
        "FIFA",
        1,
        "official",
    ),
    (
        "fifa_squads_confirmed_article",
        "FIFA World Cup 2026 squads confirmed article",
        "https://www.fifa.com/en/tournaments/mens/worldcup/canadamexicousa2026/articles/fifa-world-cup-2026-squads-confirmed",
        "squads",
        "FIFA",
        7,
        "official",
    ),
    (
        "fifa_mens_ranking",
        "FIFA/Coca-Cola Men's World Ranking",
        "https://inside.fifa.com/fifa-world-ranking/men",
        "ranking",
        "FIFA",
        45,
        "official",
    ),
    (
        "fifa_live_world_ranking",
        "FIFA Live World Ranking API surface",
        "https://www.fifa.com/en/world-rankings",
        "ranking",
        "FIFA",
        7,
        "official-live",
    ),
    (
        "manual_enhancement_feed",
        "Curated enhancement CSV feed",
        "",
        "ratings_injuries_lineups_tactics",
        "Manual or licensed provider",
        7,
        "manual-provider",
    ),
    (
        "statsbomb_open_data",
        "StatsBomb Open Data",
        "https://github.com/statsbomb/open-data",
        "club_events_lineups",
        "StatsBomb",
        90,
        "open-data-attribution-required",
    ),
]


SCHEMA_ADDITIONS = {
    "players": {
        "unified_player_id": "TEXT",
        "national_team_position": "TEXT",
        "club_position": "TEXT",
        "feature_pressing": "REAL",
        "feature_progression": "REAL",
        "feature_box_presence": "REAL",
        "feature_shot_quality": "REAL",
        "feature_key_passing": "REAL",
        "feature_duel_activity": "REAL",
        "feature_defensive_activity": "REAL",
        "feature_sample_minutes": "REAL",
        "feature_source_weight": "REAL",
    },
    "club_players": {
        "unified_player_id": "TEXT",
    },
    "model_parameters": {
        "openness_baseline_total": "REAL",
        "recent_goal_openness_weight": "REAL",
        "formation_openness_weight": "REAL",
        "style_openness_weight": "REAL",
        "tactical_openness_weight": "REAL",
        "openness_max_delta": "REAL",
        "stage_group_goal_multiplier": "REAL",
        "stage_round32_goal_multiplier": "REAL",
        "stage_round16_goal_multiplier": "REAL",
        "stage_quarter_goal_multiplier": "REAL",
        "stage_semi_goal_multiplier": "REAL",
        "stage_final_goal_multiplier": "REAL",
        "stage_data_weight": "REAL",
        "stage_sample_half_life": "REAL",
        "stage_open_match_resistance": "REAL",
        "wdl_prior_weight": "REAL",
        "formation_wdl_prior_max_weight": "REAL",
        "wdl_score_calibration_weight": "REAL",
        "favorite_score_tilt": "REAL",
        "draw_score_tilt": "REAL",
    },
}


def apply_schema_additions(conn) -> None:
    for table, columns in SCHEMA_ADDITIONS.items():
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        if not exists:
            continue
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for column, ddl_type in columns.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}")
        if table == "players":
            conn.execute(
                """
                UPDATE players
                SET national_team_position = COALESCE(national_team_position, position)
                WHERE position IS NOT NULL
                  AND trim(position) <> ''
                """
            )


def initialize(db_path: Path, with_sources: bool) -> None:
    ensure_parent(db_path)
    conn = connect(db_path)
    try:
        for ddl in DDL:
            conn.execute(ddl)
        apply_schema_additions(conn)
        for index_sql in INDEXES:
            conn.execute(index_sql)
        if with_sources:
            retrieved_at = now_utc()
            conn.executemany(
                """
                INSERT OR IGNORE INTO sources
                    (source_id, name, url, category, publisher, retrieved_at,
                     freshness_days, reliability, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        source_id,
                        name,
                        url,
                        category,
                        publisher,
                        retrieved_at,
                        freshness_days,
                        reliability,
                        "Seeded by init_database.py; refresh before production predictions.",
                    )
                    for source_id, name, url, category, publisher, freshness_days, reliability in REFERENCE_SOURCES
                ],
            )
        conn.execute(
            """
            INSERT OR IGNORE INTO model_parameters (
                parameter_id, model_version, as_of_date, base_goals, home_edge, knockout_drag,
                attack_weight, overall_weight, keeper_weight, set_piece_weight, fitness_weight,
                style_weight, formation_weight, openness_baseline_total, recent_goal_openness_weight,
                formation_openness_weight, style_openness_weight, tactical_openness_weight,
                openness_max_delta, stage_group_goal_multiplier, stage_round32_goal_multiplier,
                stage_round16_goal_multiplier, stage_quarter_goal_multiplier, stage_semi_goal_multiplier,
                stage_final_goal_multiplier, stage_data_weight, stage_sample_half_life,
                stage_open_match_resistance, wdl_prior_weight, formation_wdl_prior_max_weight,
                wdl_score_calibration_weight, favorite_score_tilt, draw_score_tilt, notes
            )
            VALUES (?, ?, ?, 1.22, 0.12, -0.07, 0.85, 0.55, 0.25, 0.18, 0.12,
                    1.00, 0.25, 2.55, 0.28, 0.38, 0.20, 0.14, 0.95,
                    1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 0.70, 36.00, 0.45,
                    0.65, 0.35, 0.70, 0.10, 0.08, ?)
            """,
            (
                f"params-{MODEL_VERSION}-default",
                MODEL_VERSION,
                "2026-06-21",
                (
                    "Seed default parameters with openness, WDL-to-score calibration, and adaptive stage goal context. "
                    "Replace with optimized rows after backtesting completed fixtures."
                ),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def write_templates(template_dir: Path) -> None:
    template_dir.mkdir(parents=True, exist_ok=True)
    for table, columns in TABLE_COLUMNS.items():
        path = template_dir / f"{table}.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(columns)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, help="SQLite database path to create or update.")
    parser.add_argument(
        "--with-reference-sources",
        action="store_true",
        help="Seed official FIFA source URLs into the sources table.",
    )
    parser.add_argument(
        "--template-dir",
        help="Optional directory for normalized CSV templates.",
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    initialize(db_path, args.with_reference_sources)
    if args.template_dir:
        write_templates(Path(args.template_dir))
    print(f"Initialized {db_path}")
    if args.template_dir:
        print(f"Wrote CSV templates to {args.template_dir}")


if __name__ == "__main__":
    main()
