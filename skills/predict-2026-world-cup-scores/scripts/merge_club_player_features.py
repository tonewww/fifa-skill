#!/usr/bin/env python3
"""Unify player identities and merge club-derived features into national-team model inputs."""

from __future__ import annotations

import argparse
import json
import math
import re
import unicodedata
from pathlib import Path

from common import clamp, connect, now_utc, slugify, table_exists, today_utc, weighted_average


SOURCE_ID = "club_feature_merge"
PROVIDER = "statsbomb-derived-low-weight"


def normalize_name(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-zA-Z0-9]+", " ", text).lower().strip()
    return " ".join(text.split())


def name_tokens(value: str | None) -> set[str]:
    particles = {"de", "da", "do", "dos", "del", "van", "von", "bin", "al", "el", "la", "le", "du"}
    return {token for token in normalize_name(value).split() if token and token not in particles}


def token_similarity(a: str | None, b: str | None) -> float:
    a_tokens = name_tokens(a)
    b_tokens = name_tokens(b)
    if not a_tokens or not b_tokens:
        return 0.0
    overlap = len(a_tokens & b_tokens)
    precision = overlap / len(a_tokens)
    recall = overlap / len(b_tokens)
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def club_similarity(world_cup_club: str | None, club_name: str | None) -> float:
    wc_tokens = name_tokens(world_cup_club)
    club_tokens = name_tokens(club_name)
    if not wc_tokens or not club_tokens:
        return 0.0
    aliases = {
        "bayer": {"bayer", "leverkusen", "04"},
        "leverkusen": {"bayer", "leverkusen", "04"},
        "munich": {"bayern", "munich", "muenchen"},
        "bayern": {"bayern", "munich", "muenchen"},
    }
    expanded = set(wc_tokens)
    for token in wc_tokens:
        expanded.update(aliases.get(token, set()))
    overlap = len(expanded & club_tokens)
    return overlap / max(len(club_tokens), 1)


def ensure_schema(conn) -> None:
    additions = {
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
        "club_players": {"unified_player_id": "TEXT"},
    }
    for table, columns in additions.items():
        if not table_exists(conn, table):
            continue
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, ddl_type in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl_type}")
        if table == "players":
            conn.execute(
                """
                UPDATE players
                SET national_team_position = COALESCE(national_team_position, position)
                WHERE position IS NOT NULL
                  AND trim(position) <> ''
                """
            )
    conn.execute(
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
        """
    )
    conn.execute(
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
        """
    )
    conn.execute(
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
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_players_unified ON players(unified_player_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_club_players_unified ON club_players(unified_player_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_identity_links_unified ON player_identity_links(unified_player_id)")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_identity_links_source ON player_identity_links(source_table, source_player_id, provider)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_player_features_unified_date ON player_feature_snapshots(unified_player_id, snapshot_date DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_player_features_player_date ON player_feature_snapshots(player_id, snapshot_date DESC)"
    )
    conn.execute(
        """
        INSERT INTO sources (
            source_id, name, category, publisher, retrieved_at, freshness_days,
            reliability, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_id) DO UPDATE SET
            retrieved_at = excluded.retrieved_at,
            notes = excluded.notes
        """,
        (
            SOURCE_ID,
            "Club player feature merge",
            "derived_player_features",
            "Local model",
            now_utc(),
            7,
            "model-derived",
            "Maps provider club players to unified player identities and writes low-weight derived features.",
        ),
    )
    conn.commit()


def unified_id_from_player(player_id: str, name: str) -> str:
    return f"player-{slugify(player_id)}"


def upsert_unified_from_world_cup(conn, player: dict, created_at: str) -> str:
    desired_unified_id = unified_id_from_player(player["player_id"], player["name"])
    unified_id = player.get("unified_player_id")
    if unified_id:
        owner = conn.execute(
            """
            SELECT player_id
            FROM players
            WHERE unified_player_id = ?
              AND player_id <> ?
            LIMIT 1
            """,
            (unified_id, player["player_id"]),
        ).fetchone()
        if owner is not None:
            unified_id = desired_unified_id
    else:
        unified_id = desired_unified_id
    conn.execute(
        """
        INSERT INTO unified_players (
            unified_player_id, canonical_name, display_name, birth_date, primary_national_team_id,
            primary_position, dominant_foot, height_cm, source_id, created_at, updated_at, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(unified_player_id) DO UPDATE SET
            canonical_name = COALESCE(unified_players.canonical_name, excluded.canonical_name),
            display_name = COALESCE(unified_players.display_name, excluded.display_name),
            birth_date = COALESCE(unified_players.birth_date, excluded.birth_date),
            primary_national_team_id = COALESCE(unified_players.primary_national_team_id, excluded.primary_national_team_id),
            primary_position = COALESCE(unified_players.primary_position, excluded.primary_position),
            dominant_foot = COALESCE(unified_players.dominant_foot, excluded.dominant_foot),
            height_cm = COALESCE(unified_players.height_cm, excluded.height_cm),
            updated_at = excluded.updated_at,
            notes = excluded.notes
        """,
        (
            unified_id,
            player["name"],
            player.get("display_name") or player["name"],
            player.get("birth_date"),
            player.get("team_id"),
            player.get("national_team_position") or player.get("position"),
            player.get("dominant_foot"),
            player.get("height_cm"),
            player.get("source_id"),
            created_at,
            created_at,
            "Unified identity seeded from World Cup squad player row.",
        ),
    )
    conn.execute("UPDATE players SET unified_player_id = ? WHERE player_id = ?", (unified_id, player["player_id"]))
    conn.execute(
        """
        INSERT INTO player_identity_links (
            link_id, unified_player_id, source_table, source_player_id, provider, team_id,
            club_team_id, confidence, match_method, verified, created_at, notes
        )
        VALUES (?, ?, 'players', ?, 'fifa-squad', ?, NULL, 1.0, 'seed-world-cup-player', 1, ?, ?)
        ON CONFLICT(source_table, source_player_id, provider) DO UPDATE SET
            unified_player_id = excluded.unified_player_id,
            confidence = excluded.confidence,
            match_method = excluded.match_method,
            verified = excluded.verified,
            notes = excluded.notes
        """,
        (
            f"identity-players-{player['player_id']}",
            unified_id,
            player["player_id"],
            player.get("team_id"),
            created_at,
            "World Cup squad player is authoritative for national-team roster row.",
        ),
    )
    return unified_id


def latest_club_team_for_player(conn, club_player_id: str) -> dict | None:
    row = conn.execute(
        """
        SELECT cpms.club_team_id, ct.name AS club_team_name, COUNT(*) AS matches
        FROM club_player_match_stats cpms
        LEFT JOIN club_teams ct ON ct.club_team_id = cpms.club_team_id
        WHERE cpms.club_player_id = ?
        GROUP BY cpms.club_team_id
        ORDER BY matches DESC
        LIMIT 1
        """,
        (club_player_id,),
    ).fetchone()
    return None if row is None else dict(row)


def best_world_cup_match(conn, club_player: dict, minimum_confidence: float) -> tuple[dict | None, float, str]:
    candidates = []
    club_team = latest_club_team_for_player(conn, club_player["club_player_id"]) or {}
    for player in conn.execute("SELECT * FROM players").fetchall():
        player = dict(player)
        name_score = max(
            token_similarity(player.get("name"), club_player.get("name")),
            token_similarity(player.get("display_name"), club_player.get("name")),
        )
        if name_score < 0.55:
            continue
        club_score = club_similarity(player.get("club"), club_team.get("club_team_name"))
        position_bonus = 0.0
        national_pos = player.get("national_team_position") or player.get("position")
        if national_pos and club_player.get("primary_position"):
            wc_pos = str(national_pos).upper()
            club_pos = str(club_player["primary_position"]).upper()
            if wc_pos[:1] and wc_pos[:1] in club_pos:
                position_bonus = 0.04
        confidence = clamp(0.82 * name_score + 0.14 * club_score + position_bonus, 0.0, 1.0)
        candidates.append((confidence, name_score, club_score, player))
    if not candidates:
        return None, 0.0, "no-name-candidate"
    candidates.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    best = candidates[0]
    if best[0] < minimum_confidence:
        return None, best[0], "below-threshold"
    return best[3], best[0], f"name-token={best[1]:.2f}; club-token={best[2]:.2f}"


def sync_club_position_to_player(conn, unified_player_id: str | None, club_position: str | None) -> None:
    if not unified_player_id or not club_position:
        return
    conn.execute(
        """
        UPDATE players
        SET club_position = COALESCE(?, club_position),
            national_team_position = COALESCE(national_team_position, position)
        WHERE unified_player_id = ?
        """,
        (club_position, unified_player_id),
    )


def link_club_players(conn, minimum_confidence: float) -> dict:
    created_at = now_utc()
    wc_players = [dict(row) for row in conn.execute("SELECT * FROM players").fetchall()]
    for player in wc_players:
        upsert_unified_from_world_cup(conn, player, created_at)

    linked = 0
    candidates = 0
    for row in conn.execute("SELECT * FROM club_players").fetchall():
        club_player = dict(row)
        if club_player.get("unified_player_id"):
            sync_club_position_to_player(conn, club_player.get("unified_player_id"), club_player.get("primary_position"))
            linked += 1
            continue
        match, confidence, method = best_world_cup_match(conn, club_player, minimum_confidence)
        if match is None:
            if confidence > 0:
                candidates += 1
            continue
        unified_id = match["unified_player_id"] or upsert_unified_from_world_cup(conn, match, created_at)
        club_team = latest_club_team_for_player(conn, club_player["club_player_id"]) or {}
        conn.execute(
            "UPDATE club_players SET unified_player_id = ? WHERE club_player_id = ?",
            (unified_id, club_player["club_player_id"]),
        )
        sync_club_position_to_player(conn, unified_id, club_player.get("primary_position"))
        conn.execute(
            """
            INSERT INTO player_identity_links (
                link_id, unified_player_id, source_table, source_player_id, provider, team_id,
                club_team_id, confidence, match_method, verified, created_at, notes
            )
            VALUES (?, ?, 'club_players', ?, ?, ?, ?, ?, ?, 0, ?, ?)
            ON CONFLICT(source_table, source_player_id, provider) DO UPDATE SET
                unified_player_id = excluded.unified_player_id,
                team_id = excluded.team_id,
                club_team_id = excluded.club_team_id,
                confidence = excluded.confidence,
                match_method = excluded.match_method,
                notes = excluded.notes
            """,
            (
                f"identity-club-{club_player['club_player_id']}",
                unified_id,
                club_player["club_player_id"],
                club_player.get("provider") or "club-provider",
                match.get("team_id"),
                club_team.get("club_team_id"),
                confidence,
                method,
                created_at,
                "Auto-linked from club provider player to unified World Cup player identity; review before high-weight use.",
            ),
        )
        linked += 1
    conn.commit()
    return {"world_cup_players_seeded": len(wc_players), "club_players_linked": linked, "club_link_candidates_below_threshold": candidates}


def rating_from_club_features(feature: dict, base_player: dict, weight: float) -> dict:
    attack = clamp(
        0.62 * float(feature.get("attacking_score") or 50.0)
        + 0.20 * float(feature.get("touches_box_per90") or 0.0) * 8.0
        + 0.18 * float(feature.get("xg_per90") or 0.0) * 100.0
    )
    possession = clamp(
        0.70 * float(feature.get("possession_score") or 50.0)
        + 0.30 * float(feature.get("key_passes_per90") or 0.0) * 22.0
    )
    defense = clamp(float(feature.get("defensive_score") or 50.0))
    transition = clamp(float(feature.get("transition_score") or 50.0))
    set_piece = base_player.get("rating_set_piece")
    if set_piece is None:
        set_piece = 50.0
    fitness = base_player.get("rating_fitness")
    if fitness is None:
        fitness = 50.0
    overall = clamp(
        0.30 * attack
        + 0.24 * possession
        + 0.20 * defense
        + 0.18 * transition
        + 0.08 * float(fitness)
    )
    def blend(existing: float | None, derived: float) -> float:
        base = 50.0 if existing is None else float(existing)
        return clamp((1.0 - weight) * base + weight * derived)

    return {
        "overall": blend(base_player.get("rating_overall"), overall),
        "attack": blend(base_player.get("rating_attack"), attack),
        "defense": blend(base_player.get("rating_defense"), defense),
        "possession": blend(base_player.get("rating_possession"), possession),
        "transition": blend(base_player.get("rating_transition"), transition),
        "set_piece": blend(base_player.get("rating_set_piece"), float(set_piece)),
        "goalkeeping": base_player.get("rating_goalkeeping"),
        "fitness": blend(base_player.get("rating_fitness"), float(fitness)),
        "minutes_recent": feature.get("minutes_played"),
    }


def feature_scores_from_club_features(feature: dict) -> dict:
    xg_per90 = float(feature.get("xg_per90") or 0.0)
    shots_per90 = float(feature.get("shots_per90") or 0.0)
    key_passes_per90 = float(feature.get("key_passes_per90") or 0.0)
    pressures_per90 = float(feature.get("pressures_per90") or 0.0)
    carries_per90 = float(feature.get("carries_per90") or 0.0)
    dribbles_per90 = float(feature.get("dribbles_per90") or 0.0)
    touches_box_per90 = float(feature.get("touches_box_per90") or 0.0)
    duels_per90 = float(feature.get("duels_per90") or 0.0)
    def_actions_per90 = float(feature.get("def_actions_per90") or 0.0)
    attacking = float(feature.get("attacking_score") or 50.0)
    possession = float(feature.get("possession_score") or 50.0)
    defensive = float(feature.get("defensive_score") or 50.0)
    transition = float(feature.get("transition_score") or 50.0)
    shot_quality = xg_per90 / max(shots_per90, 0.25)
    return {
        "pressing_score": clamp(34.0 + pressures_per90 * 2.8),
        "progression_score": clamp(0.42 * possession + 0.35 * transition + 0.23 * carries_per90 * 4.8),
        "box_presence_score": clamp(0.48 * attacking + 0.32 * touches_box_per90 * 10.0 + 0.20 * xg_per90 * 100.0),
        "shot_quality_score": clamp(38.0 + shot_quality * 420.0 + xg_per90 * 22.0),
        "key_passing_score": clamp(0.55 * possession + 0.45 * key_passes_per90 * 24.0),
        "duel_activity_score": clamp(38.0 + duels_per90 * 5.0),
        "defensive_activity_score": clamp(0.50 * defensive + 0.30 * def_actions_per90 * 12.0 + 0.20 * pressures_per90 * 2.0),
    }


def latest_linked_feature_rows(conn, min_minutes: float) -> list[dict]:
    rows = conn.execute(
        """
        SELECT
            f.*,
            cp.unified_player_id,
            p.player_id,
            p.team_id,
            p.rating_overall,
            p.rating_attack,
            p.rating_defense,
            p.rating_possession,
            p.rating_transition,
            p.rating_set_piece,
            p.rating_goalkeeping,
            p.rating_fitness
        FROM club_player_feature_snapshots f
        JOIN club_players cp ON cp.club_player_id = f.club_player_id
        JOIN players p ON p.unified_player_id = cp.unified_player_id
        JOIN (
            SELECT club_player_id, MAX(snapshot_date) AS snapshot_date
            FROM club_player_feature_snapshots
            GROUP BY club_player_id
        ) latest
          ON latest.club_player_id = f.club_player_id
         AND latest.snapshot_date = f.snapshot_date
        WHERE cp.unified_player_id IS NOT NULL
          AND COALESCE(f.minutes_played, 0) >= ?
        """,
        (min_minutes,),
    ).fetchall()
    return [dict(row) for row in rows]


def write_player_feature_snapshots(conn, feature_weight: float, min_minutes: float, rating_date: str) -> dict:
    rows = latest_linked_feature_rows(conn, min_minutes)
    written = 0
    for row in rows:
        scores = feature_scores_from_club_features(row)
        conn.execute(
            """
            INSERT INTO player_feature_snapshots (
                snapshot_id, unified_player_id, player_id, team_id, provider, snapshot_date,
                sample_minutes, source_weight, pressing_score, progression_score,
                box_presence_score, shot_quality_score, key_passing_score,
                duel_activity_score, defensive_activity_score, xg_per90, shots_per90,
                key_passes_per90, pressures_per90, carries_per90, dribbles_per90,
                touches_box_per90, duels_per90, def_actions_per90, source_id, notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(snapshot_id) DO UPDATE SET
                sample_minutes = excluded.sample_minutes,
                source_weight = excluded.source_weight,
                pressing_score = excluded.pressing_score,
                progression_score = excluded.progression_score,
                box_presence_score = excluded.box_presence_score,
                shot_quality_score = excluded.shot_quality_score,
                key_passing_score = excluded.key_passing_score,
                duel_activity_score = excluded.duel_activity_score,
                defensive_activity_score = excluded.defensive_activity_score,
                xg_per90 = excluded.xg_per90,
                shots_per90 = excluded.shots_per90,
                key_passes_per90 = excluded.key_passes_per90,
                pressures_per90 = excluded.pressures_per90,
                carries_per90 = excluded.carries_per90,
                dribbles_per90 = excluded.dribbles_per90,
                touches_box_per90 = excluded.touches_box_per90,
                duels_per90 = excluded.duels_per90,
                def_actions_per90 = excluded.def_actions_per90,
                source_id = excluded.source_id,
                notes = excluded.notes
            """,
            (
                f"pfeature-{PROVIDER}-{row['player_id']}-{rating_date}",
                row["unified_player_id"],
                row["player_id"],
                row["team_id"],
                PROVIDER,
                rating_date,
                row.get("minutes_played"),
                feature_weight,
                round(scores["pressing_score"], 2),
                round(scores["progression_score"], 2),
                round(scores["box_presence_score"], 2),
                round(scores["shot_quality_score"], 2),
                round(scores["key_passing_score"], 2),
                round(scores["duel_activity_score"], 2),
                round(scores["defensive_activity_score"], 2),
                row.get("xg_per90"),
                row.get("shots_per90"),
                row.get("key_passes_per90"),
                row.get("pressures_per90"),
                row.get("carries_per90"),
                row.get("dribbles_per90"),
                row.get("touches_box_per90"),
                row.get("duels_per90"),
                row.get("def_actions_per90"),
                SOURCE_ID,
                (
                    "Low-weight role feature snapshot from linked club_player_feature_snapshots; "
                    f"source_weight={feature_weight:.2f}, minutes={float(row.get('minutes_played') or 0):.1f}."
                ),
            ),
        )
        written += 1
    conn.commit()
    return {"player_feature_snapshot_rows_written": written}


def merge_player_ratings(conn, feature_weight: float, min_minutes: float, rating_date: str) -> dict:
    rows = latest_linked_feature_rows(conn, min_minutes)
    applied = 0
    for row in rows:
        rating = rating_from_club_features(row, row, feature_weight)
        conn.execute(
            """
            INSERT INTO player_ratings (
                rating_id, player_id, team_id, provider, rating_date, overall,
                attack, defense, possession, transition, set_piece, goalkeeping,
                fitness, market_value_eur, minutes_recent, source_id, notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)
            ON CONFLICT(rating_id) DO UPDATE SET
                overall = excluded.overall,
                attack = excluded.attack,
                defense = excluded.defense,
                possession = excluded.possession,
                transition = excluded.transition,
                set_piece = excluded.set_piece,
                goalkeeping = excluded.goalkeeping,
                fitness = excluded.fitness,
                minutes_recent = excluded.minutes_recent,
                source_id = excluded.source_id,
                notes = excluded.notes
            """,
            (
                f"rating-{PROVIDER}-{row['player_id']}-{rating_date}",
                row["player_id"],
                row["team_id"],
                PROVIDER,
                rating_date,
                round(rating["overall"], 2),
                round(rating["attack"], 2),
                round(rating["defense"], 2),
                round(rating["possession"], 2),
                round(rating["transition"], 2),
                round(rating["set_piece"], 2),
                rating["goalkeeping"],
                round(rating["fitness"], 2),
                rating["minutes_recent"],
                SOURCE_ID,
                (
                    f"Low-weight merge from club_player_feature_snapshots; feature_weight={feature_weight:.2f}, "
                    f"minutes={float(row.get('minutes_played') or 0):.1f}."
                ),
            ),
        )
        applied += 1
    conn.commit()
    return {"player_rating_rows_written": applied}


def apply_latest_ratings_to_players(conn) -> int:
    rows = conn.execute(
        """
        SELECT
            pr.*,
            pf.pressing_score,
            pf.progression_score,
            pf.box_presence_score,
            pf.shot_quality_score,
            pf.key_passing_score,
            pf.duel_activity_score,
            pf.defensive_activity_score,
            pf.sample_minutes,
            pf.source_weight
        FROM player_ratings pr
        JOIN (
            SELECT player_id, MAX(rating_date) AS rating_date
            FROM player_ratings
            WHERE provider = ?
            GROUP BY player_id
        ) latest
          ON latest.player_id = pr.player_id
         AND latest.rating_date = pr.rating_date
        LEFT JOIN player_feature_snapshots pf
          ON pf.player_id = pr.player_id
         AND pf.provider = pr.provider
         AND pf.snapshot_date = pr.rating_date
        WHERE pr.provider = ?
        """,
        (PROVIDER, PROVIDER),
    ).fetchall()
    applied = 0
    for row in rows:
        conn.execute(
            """
            UPDATE players
            SET rating_overall = COALESCE(?, rating_overall),
                rating_attack = COALESCE(?, rating_attack),
                rating_defense = COALESCE(?, rating_defense),
                rating_possession = COALESCE(?, rating_possession),
                rating_transition = COALESCE(?, rating_transition),
                rating_set_piece = COALESCE(?, rating_set_piece),
                rating_goalkeeping = COALESCE(?, rating_goalkeeping),
                rating_fitness = COALESCE(?, rating_fitness),
                feature_pressing = COALESCE(?, feature_pressing),
                feature_progression = COALESCE(?, feature_progression),
                feature_box_presence = COALESCE(?, feature_box_presence),
                feature_shot_quality = COALESCE(?, feature_shot_quality),
                feature_key_passing = COALESCE(?, feature_key_passing),
                feature_duel_activity = COALESCE(?, feature_duel_activity),
                feature_defensive_activity = COALESCE(?, feature_defensive_activity),
                feature_sample_minutes = COALESCE(?, feature_sample_minutes),
                feature_source_weight = COALESCE(?, feature_source_weight),
                last_verified_at = ?
            WHERE player_id = ?
            """,
            (
                row["overall"],
                row["attack"],
                row["defense"],
                row["possession"],
                row["transition"],
                row["set_piece"],
                row["goalkeeping"],
                row["fitness"],
                row["pressing_score"],
                row["progression_score"],
                row["box_presence_score"],
                row["shot_quality_score"],
                row["key_passing_score"],
                row["duel_activity_score"],
                row["defensive_activity_score"],
                row["sample_minutes"],
                row["source_weight"],
                now_utc(),
                row["player_id"],
            ),
        )
        applied += 1
    conn.commit()
    return applied


def team_club_feature_summary(conn, min_minutes: float) -> dict[str, dict]:
    rows = latest_linked_feature_rows(conn, min_minutes)
    by_team: dict[str, list[dict]] = {}
    for row in rows:
        by_team.setdefault(row["team_id"], []).append(row)
    summary = {}
    for team_id, items in by_team.items():
        weighted = lambda key, default=50.0: weighted_average(  # noqa: E731
            [(item.get(key), item.get("minutes_played")) for item in items],
            default,
        )
        role_scores = [feature_scores_from_club_features(item) for item in items]
        weighted_score = lambda key, default=50.0: weighted_average(  # noqa: E731
            [
                (score.get(key), item.get("minutes_played"))
                for score, item in zip(role_scores, items, strict=False)
            ],
            default,
        )
        summary[team_id] = {
            "sample_players": len(items),
            "minutes": sum(float(item.get("minutes_played") or 0.0) for item in items),
            "pressures_per90": weighted("pressures_per90", 8.0),
            "carries_per90": weighted("carries_per90", 8.0),
            "dribbles_per90": weighted("dribbles_per90", 1.5),
            "duels_per90": weighted("duels_per90", 5.0),
            "def_actions_per90": weighted("def_actions_per90", 3.0),
            "touches_box_per90": weighted("touches_box_per90", 1.5),
            "attacking_score": weighted("attacking_score"),
            "possession_score": weighted("possession_score"),
            "defensive_score": weighted("defensive_score"),
            "transition_score": weighted("transition_score"),
            "xg_per90": weighted("xg_per90", 0.12),
            "key_passes_per90": weighted("key_passes_per90", 0.7),
            "pressing_score": weighted_score("pressing_score"),
            "progression_score": weighted_score("progression_score"),
            "box_presence_score": weighted_score("box_presence_score"),
            "shot_quality_score": weighted_score("shot_quality_score"),
            "key_passing_score": weighted_score("key_passing_score"),
            "duel_activity_score": weighted_score("duel_activity_score"),
            "defensive_activity_score": weighted_score("defensive_activity_score"),
        }
    return summary


def ensure_team_style(conn, team_id: str, profile_date: str) -> str:
    row = conn.execute(
        """
        SELECT profile_id
        FROM team_style_profiles
        WHERE team_id = ?
        ORDER BY profile_date DESC
        LIMIT 1
        """,
        (team_id,),
    ).fetchone()
    if row is not None:
        return row["profile_id"]
    profile_id = f"style-{team_id.lower()}-{profile_date}"
    conn.execute(
        """
        INSERT INTO team_style_profiles (
            profile_id, team_id, profile_date, formation_primary, tempo,
            press_intensity, defensive_line, buildup_quality, transition_attack,
            transition_defense, wing_play, central_progression, set_piece_attack,
            set_piece_defense, aerial_strength, low_block_attack, low_block_defense,
            keeper_sweeper, keeper_shot_stopping, injury_load, cohesion,
            travel_fatigue, source_id, notes
        )
        VALUES (?, ?, ?, NULL, 50, 50, 50, 50, 50, 50, 50, 50, 50, 50, 50, 50, 50, 50, 50, 0, 50, 0, ?, ?)
        """,
        (
            profile_id,
            team_id,
            profile_date,
            SOURCE_ID,
            "Created by club feature merge with neutral defaults before low-weight adjustment.",
        ),
    )
    return profile_id


def blend_value(existing: float | None, derived: float, weight: float) -> float:
    base = 50.0 if existing is None else float(existing)
    return clamp((1.0 - weight) * base + weight * derived)


def merge_team_style_profiles(conn, style_weight: float, min_minutes: float, profile_date: str) -> dict:
    summary = team_club_feature_summary(conn, min_minutes)
    applied = 0
    for team_id, item in summary.items():
        profile_id = ensure_team_style(conn, team_id, profile_date)
        row = conn.execute("SELECT * FROM team_style_profiles WHERE profile_id = ?", (profile_id,)).fetchone()
        if row is None:
            continue
        row = dict(row)
        derived = {
            "press_intensity": clamp(0.58 * item["pressing_score"] + 0.42 * (35.0 + item["pressures_per90"] * 1.35)),
            "buildup_quality": clamp(0.60 * item["possession_score"] + 0.40 * item["progression_score"]),
            "transition_attack": clamp(
                0.46 * item["transition_score"]
                + 0.34 * item["progression_score"]
                + 0.20 * item["carries_per90"] * 2.0
            ),
            "transition_defense": clamp(
                0.45 * item["defensive_score"]
                + 0.35 * item["defensive_activity_score"]
                + 0.20 * item["pressing_score"]
            ),
            "wing_play": clamp(
                0.34 * item["dribbles_per90"] * 12.0
                + 0.33 * item["touches_box_per90"] * 8.0
                + 0.33 * item["box_presence_score"]
            ),
            "central_progression": clamp(0.40 * item["possession_score"] + 0.33 * item["progression_score"] + 0.27 * item["key_passing_score"]),
            "low_block_attack": clamp(
                0.46 * item["attacking_score"]
                + 0.27 * item["box_presence_score"]
                + 0.27 * item["shot_quality_score"]
            ),
            "low_block_defense": clamp(0.54 * item["defensive_score"] + 0.28 * item["defensive_activity_score"] + 0.18 * item["duel_activity_score"]),
            "tempo": clamp(
                0.32 * item["transition_score"]
                + 0.24 * item["pressing_score"]
                + 0.24 * item["progression_score"]
                + 0.20 * item["box_presence_score"]
            ),
        }
        conn.execute(
            """
            UPDATE team_style_profiles
            SET tempo = ?,
                press_intensity = ?,
                buildup_quality = ?,
                transition_attack = ?,
                transition_defense = ?,
                wing_play = ?,
                central_progression = ?,
                low_block_attack = ?,
                low_block_defense = ?,
                source_id = ?,
                notes = ?
            WHERE profile_id = ?
            """,
            (
                round(blend_value(row.get("tempo"), derived["tempo"], style_weight), 2),
                round(blend_value(row.get("press_intensity"), derived["press_intensity"], style_weight), 2),
                round(blend_value(row.get("buildup_quality"), derived["buildup_quality"], style_weight), 2),
                round(blend_value(row.get("transition_attack"), derived["transition_attack"], style_weight), 2),
                round(blend_value(row.get("transition_defense"), derived["transition_defense"], style_weight), 2),
                round(blend_value(row.get("wing_play"), derived["wing_play"], style_weight), 2),
                round(blend_value(row.get("central_progression"), derived["central_progression"], style_weight), 2),
                round(blend_value(row.get("low_block_attack"), derived["low_block_attack"], style_weight), 2),
                round(blend_value(row.get("low_block_defense"), derived["low_block_defense"], style_weight), 2),
                SOURCE_ID,
                (
                    f"Low-weight club feature merge; players={item['sample_players']}, "
                    f"minutes={item['minutes']:.0f}, style_weight={style_weight:.2f}. "
                    f"Previous profile blended, not replaced."
                ),
                profile_id,
            ),
        )
        applied += 1
    conn.commit()
    return {"team_style_profiles_updated": applied}


def merge(db_path: Path, args) -> dict:
    conn = connect(db_path)
    try:
        ensure_schema(conn)
        identity = link_club_players(conn, args.min_link_confidence)
        feature_snapshots = write_player_feature_snapshots(
            conn,
            args.player_feature_weight,
            args.min_feature_minutes,
            args.rating_date,
        )
        ratings = merge_player_ratings(conn, args.player_feature_weight, args.min_feature_minutes, args.rating_date)
        applied_players = apply_latest_ratings_to_players(conn) if args.apply_to_players else 0
        styles = merge_team_style_profiles(conn, args.team_style_weight, args.min_feature_minutes, args.profile_date)
        return {
            **identity,
            **feature_snapshots,
            **ratings,
            "players_updated_from_low_weight_ratings": applied_players,
            **styles,
        }
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, help="SQLite database path.")
    parser.add_argument("--min-link-confidence", type=float, default=0.78)
    parser.add_argument("--min-feature-minutes", type=float, default=90.0)
    parser.add_argument("--player-feature-weight", type=float, default=0.18)
    parser.add_argument("--team-style-weight", type=float, default=0.12)
    parser.add_argument("--rating-date", default=today_utc())
    parser.add_argument("--profile-date", default=today_utc())
    parser.add_argument("--apply-to-players", action="store_true", help="Also update players baseline rating columns.")
    args = parser.parse_args()
    result = merge(Path(args.db), args)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
