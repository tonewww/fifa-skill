#!/usr/bin/env python3
"""Import StatsBomb Open Data club/league matches, lineups, events, and derived features."""

from __future__ import annotations

import argparse
import json
import math
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any

from common import clamp, connect, ensure_parent, now_utc, slugify


PROVIDER = "statsbomb"
SOURCE_ID = "statsbomb_open_data"
BASE_URL = "https://raw.githubusercontent.com/statsbomb/open-data/master/data"


def fetch_json(url: str, cache_path: Path | None = None, refresh: bool = False) -> Any:
    if cache_path and cache_path.exists() and not refresh:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    with urllib.request.urlopen(url, timeout=60) as response:
        payload = response.read().decode("utf-8")
    if cache_path:
        ensure_parent(cache_path)
        cache_path.write_text(payload, encoding="utf-8")
    return json.loads(payload)


def ensure_schema(conn) -> None:
    conn.execute(
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
        """
    )
    conn.execute(
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
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS club_players (
            club_player_id TEXT PRIMARY KEY,
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
        """
    )
    conn.execute(
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
        """
    )
    conn.execute(
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
        """
    )
    conn.execute(
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
        """
    )
    conn.execute(
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
        """
    )
    conn.execute(
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
        """
    )
    conn.execute(
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
        """
    )
    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_club_matches_competition ON club_matches(club_competition_id, match_date)",
        "CREATE INDEX IF NOT EXISTS idx_club_matches_teams ON club_matches(home_team_id, away_team_id)",
        "CREATE INDEX IF NOT EXISTS idx_club_lineups_match_team ON club_lineups(club_match_id, club_team_id)",
        "CREATE INDEX IF NOT EXISTS idx_club_lineup_players_player ON club_lineup_players(club_player_id)",
        "CREATE INDEX IF NOT EXISTS idx_club_team_stats_match_team ON club_team_match_stats(club_match_id, club_team_id)",
        "CREATE INDEX IF NOT EXISTS idx_club_player_stats_player ON club_player_match_stats(club_player_id)",
        "CREATE INDEX IF NOT EXISTS idx_club_player_features_player_date ON club_player_feature_snapshots(club_player_id, snapshot_date DESC)",
    ]
    for sql in indexes:
        conn.execute(sql)
    conn.execute(
        """
        INSERT INTO sources (
            source_id, name, url, category, publisher, retrieved_at, freshness_days,
            license_note, reliability, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_id) DO UPDATE SET
            name = excluded.name,
            url = excluded.url,
            category = excluded.category,
            publisher = excluded.publisher,
            retrieved_at = excluded.retrieved_at,
            freshness_days = excluded.freshness_days,
            license_note = excluded.license_note,
            reliability = excluded.reliability,
            notes = excluded.notes
        """,
        (
            SOURCE_ID,
            "StatsBomb Open Data",
            "https://github.com/statsbomb/open-data",
            "club_events_lineups",
            "StatsBomb",
            now_utc(),
            90,
            "Open data; attribution to StatsBomb required when publishing derived analysis.",
            "open-data-attribution-required",
            "Imported by ingest_statsbomb_open_data.py.",
        ),
    )
    conn.commit()


def club_competition_id(competition_id: int | str, season_id: int | str) -> str:
    return f"{PROVIDER}-competition-{competition_id}-season-{season_id}"


def club_team_id(team_id: int | str | None, name: str) -> str:
    if team_id not in (None, ""):
        return f"{PROVIDER}-team-{team_id}"
    return f"{PROVIDER}-team-{slugify(name)}"


def club_player_id(player_id: int | str | None, name: str) -> str:
    if player_id not in (None, ""):
        if str(player_id).startswith(f"{PROVIDER}-player-"):
            return str(player_id)
        return f"{PROVIDER}-player-{player_id}"
    return f"{PROVIDER}-player-{slugify(name)}"


def club_match_id(match_id: int | str) -> str:
    return f"{PROVIDER}-match-{match_id}"


def lookup_country(obj: dict) -> str | None:
    value = obj.get("country") or {}
    if isinstance(value, dict):
        return value.get("name")
    return None


def extract_team(row: dict, side: str) -> dict:
    team = row.get(f"{side}_team") or {}
    return {
        "team_id": team.get(f"{side}_team_id") or team.get("team_id"),
        "name": team.get(f"{side}_team_name") or team.get("team_name"),
        "country": lookup_country(team),
    }


def upsert_club_team(conn, team: dict, gender: str | None, imported_at: str) -> str:
    team_id = club_team_id(team.get("team_id"), team.get("name") or "unknown")
    conn.execute(
        """
        INSERT INTO club_teams (
            club_team_id, provider, provider_team_id, name, country, gender, source_id, imported_at, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(club_team_id) DO UPDATE SET
            provider = excluded.provider,
            provider_team_id = excluded.provider_team_id,
            name = excluded.name,
            country = COALESCE(excluded.country, club_teams.country),
            gender = COALESCE(excluded.gender, club_teams.gender),
            source_id = excluded.source_id,
            imported_at = excluded.imported_at,
            notes = excluded.notes
        """,
        (
            team_id,
            PROVIDER,
            None if team.get("team_id") is None else str(team.get("team_id")),
            team.get("name") or team_id,
            team.get("country"),
            gender,
            SOURCE_ID,
            imported_at,
            "StatsBomb Open Data team.",
        ),
    )
    return team_id


def upsert_club_player(
    conn,
    player_id: int | str | None,
    name: str,
    nickname: str | None,
    country: str | None,
    position: str | None,
    imported_at: str,
) -> str:
    cid = club_player_id(player_id, name)
    conn.execute(
        """
        INSERT INTO club_players (
            club_player_id, provider, provider_player_id, name, nickname, country,
            primary_position, source_id, imported_at, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, COALESCE(?, (SELECT primary_position FROM club_players WHERE club_player_id = ?)), ?, ?, ?)
        ON CONFLICT(club_player_id) DO UPDATE SET
            provider = excluded.provider,
            provider_player_id = excluded.provider_player_id,
            name = excluded.name,
            nickname = COALESCE(excluded.nickname, club_players.nickname),
            country = COALESCE(excluded.country, club_players.country),
            primary_position = COALESCE(excluded.primary_position, club_players.primary_position),
            source_id = excluded.source_id,
            imported_at = excluded.imported_at,
            notes = excluded.notes
        """,
        (
            cid,
            PROVIDER,
            None if player_id is None else str(player_id),
            name,
            nickname,
            country,
            position,
            cid,
            SOURCE_ID,
            imported_at,
            "StatsBomb Open Data player.",
        ),
    )
    return cid


def insert_competition(conn, competition: dict, imported_at: str) -> str:
    cid = club_competition_id(competition["competition_id"], competition["season_id"])
    conn.execute(
        """
        INSERT INTO club_competitions (
            club_competition_id, provider, provider_competition_id, provider_season_id,
            country, competition_name, season_name, gender, is_international,
            match_available, metadata_json, source_id, imported_at, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(club_competition_id) DO UPDATE SET
            provider = excluded.provider,
            provider_competition_id = excluded.provider_competition_id,
            provider_season_id = excluded.provider_season_id,
            country = excluded.country,
            competition_name = excluded.competition_name,
            season_name = excluded.season_name,
            gender = excluded.gender,
            is_international = excluded.is_international,
            match_available = excluded.match_available,
            metadata_json = excluded.metadata_json,
            source_id = excluded.source_id,
            imported_at = excluded.imported_at,
            notes = excluded.notes
        """,
        (
            cid,
            PROVIDER,
            str(competition["competition_id"]),
            str(competition["season_id"]),
            competition.get("country_name"),
            competition.get("competition_name"),
            competition.get("season_name"),
            competition.get("competition_gender"),
            1 if competition.get("competition_international") else 0,
            competition.get("match_available"),
            json.dumps(competition, ensure_ascii=True),
            SOURCE_ID,
            imported_at,
            "StatsBomb Open Data competition-season row.",
        ),
    )
    return cid


def insert_match(conn, match: dict, competition_row: dict, competition_db_id: str, imported_at: str) -> dict:
    home = extract_team(match, "home")
    away = extract_team(match, "away")
    home_id = upsert_club_team(conn, home, competition_row.get("competition_gender"), imported_at)
    away_id = upsert_club_team(conn, away, competition_row.get("competition_gender"), imported_at)
    mid = club_match_id(match["match_id"])
    competition_stage = match.get("competition_stage") or {}
    season = match.get("season") or {}
    conn.execute(
        """
        INSERT INTO club_matches (
            club_match_id, provider, provider_match_id, club_competition_id, match_date,
            kick_off, stage, round_name, season_name, home_team_id, away_team_id,
            home_score, away_score, match_status, match_week, metadata_json, source_id, imported_at, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(club_match_id) DO UPDATE SET
            provider = excluded.provider,
            provider_match_id = excluded.provider_match_id,
            club_competition_id = excluded.club_competition_id,
            match_date = excluded.match_date,
            kick_off = excluded.kick_off,
            stage = excluded.stage,
            round_name = excluded.round_name,
            season_name = excluded.season_name,
            home_team_id = excluded.home_team_id,
            away_team_id = excluded.away_team_id,
            home_score = excluded.home_score,
            away_score = excluded.away_score,
            match_status = excluded.match_status,
            match_week = excluded.match_week,
            metadata_json = excluded.metadata_json,
            source_id = excluded.source_id,
            imported_at = excluded.imported_at,
            notes = excluded.notes
        """,
        (
            mid,
            PROVIDER,
            str(match["match_id"]),
            competition_db_id,
            match.get("match_date"),
            match.get("kick_off"),
            competition_stage.get("name"),
            match.get("match_week"),
            season.get("season_name") or competition_row.get("season_name"),
            home_id,
            away_id,
            match.get("home_score"),
            match.get("away_score"),
            match.get("match_status"),
            match.get("match_week"),
            json.dumps(match, ensure_ascii=True),
            SOURCE_ID,
            imported_at,
            "StatsBomb Open Data match metadata.",
        ),
    )
    return {"match_id": mid, "provider_match_id": match["match_id"], "home_team_id": home_id, "away_team_id": away_id}


def event_team_id(event: dict) -> str | None:
    team = event.get("team") or {}
    raw = team.get("id")
    name = team.get("name") or "unknown"
    return club_team_id(raw, name) if raw is not None or name else None


def event_player(event: dict) -> tuple[str | None, str | None, str | None]:
    player = event.get("player") or {}
    raw = player.get("id")
    name = player.get("name")
    if not name:
        return None, None, None
    position = (event.get("position") or {}).get("name")
    return club_player_id(raw, name), name, position


def in_box(location: list | tuple | None) -> bool:
    if not location or len(location) < 2:
        return False
    x, y = float(location[0]), float(location[1])
    return x >= 102 and 18 <= y <= 62


def is_deep_progression(location: list | tuple | None) -> bool:
    if not location or len(location) < 2:
        return False
    return float(location[0]) >= 90


def stat_factory() -> dict[str, float]:
    return defaultdict(float)


def aggregate_events(events: list[dict], match_context: dict, imported_at: str) -> tuple[dict, dict, dict[str, str]]:
    team_stats: dict[str, dict] = defaultdict(stat_factory)
    player_stats: dict[str, dict] = defaultdict(stat_factory)
    player_names: dict[str, str] = {}
    player_positions: dict[str, str] = {}
    player_team: dict[str, str] = {}
    for event in events:
        team_id = event_team_id(event)
        if not team_id:
            continue
        event_type = (event.get("type") or {}).get("name") or ""
        player_id, player_name, position = event_player(event)
        if player_id and player_name:
            player_names[player_id] = player_name
            if position:
                player_positions[player_id] = position
            player_team[player_id] = team_id
        team_stats[team_id]["possession_events"] += 1
        if player_id:
            player_stats[player_id]["events"] += 1
            if event.get("under_pressure"):
                player_stats[player_id]["under_pressure_events"] += 1

        location = event.get("location")
        if in_box(location):
            team_stats[team_id]["touches_box"] += 1
            if player_id:
                player_stats[player_id]["touches_box"] += 1

        if event_type == "Shot":
            shot = event.get("shot") or {}
            outcome = (shot.get("outcome") or {}).get("name")
            statsbomb_xg = float(shot.get("statsbomb_xg") or 0.0)
            team_stats[team_id]["shots"] += 1
            team_stats[team_id]["xg"] += statsbomb_xg
            if outcome in {"Goal", "Saved", "Saved to Post"}:
                team_stats[team_id]["shots_on_target"] += 1
            if (shot.get("type") or {}).get("name") in {"Free Kick", "Corner", "Penalty"}:
                team_stats[team_id]["set_piece_shots"] += 1
            else:
                team_stats[team_id]["open_play_shots"] += 1
            if outcome == "Goal":
                team_stats[team_id]["goals"] += 1
            if player_id:
                player_stats[player_id]["shots"] += 1
                player_stats[player_id]["xg"] += statsbomb_xg
                if outcome == "Goal":
                    player_stats[player_id]["goals"] += 1
        elif event_type == "Pass":
            pass_data = event.get("pass") or {}
            outcome = (pass_data.get("outcome") or {}).get("name")
            team_stats[team_id]["passes"] += 1
            if outcome is None:
                team_stats[team_id]["completed_passes"] += 1
            if event.get("under_pressure"):
                team_stats[team_id]["passes_under_pressure"] += 1
            if (pass_data.get("type") or {}).get("name") == "Corner":
                team_stats[team_id]["corners"] += 1
            if pass_data.get("cross"):
                team_stats[team_id]["crosses"] += 1
            if is_deep_progression(pass_data.get("end_location")):
                team_stats[team_id]["deep_progressions"] += 1
            if (pass_data.get("shot_assist") or pass_data.get("goal_assist")) and player_id:
                player_stats[player_id]["key_passes"] += 1
            if pass_data.get("goal_assist") and player_id:
                player_stats[player_id]["assists"] += 1
            if player_id:
                player_stats[player_id]["passes"] += 1
                if outcome is None:
                    player_stats[player_id]["completed_passes"] += 1
        elif event_type == "Carry":
            carry = event.get("carry") or {}
            team_stats[team_id]["carries"] += 1
            if is_deep_progression(carry.get("end_location")):
                team_stats[team_id]["deep_progressions"] += 1
            if player_id:
                player_stats[player_id]["carries"] += 1
        elif event_type == "Dribble":
            team_stats[team_id]["dribbles"] += 1
            if player_id:
                player_stats[player_id]["dribbles"] += 1
        elif event_type == "Pressure":
            team_stats[team_id]["pressures"] += 1
            if event.get("counterpress"):
                team_stats[team_id]["counterpressures"] += 1
            if player_id:
                player_stats[player_id]["pressures"] += 1
                if event.get("counterpress"):
                    player_stats[player_id]["counterpressures"] += 1
        elif event_type in {"Duel", "50/50"}:
            team_stats[team_id]["duels"] += 1
            if player_id:
                player_stats[player_id]["duels"] += 1
        elif event_type == "Interception":
            team_stats[team_id]["interceptions"] += 1
            if player_id:
                player_stats[player_id]["interceptions"] += 1
        elif event_type == "Block":
            team_stats[team_id]["blocks"] += 1
            if player_id:
                player_stats[player_id]["blocks"] += 1
        elif event_type == "Clearance":
            team_stats[team_id]["clearances"] += 1
            if player_id:
                player_stats[player_id]["clearances"] += 1
        elif event_type == "Foul Committed":
            team_stats[team_id]["fouls_committed"] += 1
            if player_id:
                player_stats[player_id]["fouls_committed"] += 1
        elif event_type == "Foul Won":
            team_stats[team_id]["fouls_won"] += 1
            if player_id:
                player_stats[player_id]["fouls_won"] += 1

    home_id = match_context["home_team_id"]
    away_id = match_context["away_team_id"]
    team_stats[home_id]["goals"] = max(team_stats[home_id].get("goals", 0), 0)
    team_stats[away_id]["goals"] = max(team_stats[away_id].get("goals", 0), 0)
    return team_stats, player_stats, {"names": player_names, "positions": player_positions, "teams": player_team}


def player_minutes_from_events(events: list[dict]) -> dict[str, float]:
    max_minute: dict[str, float] = defaultdict(float)
    for event in events:
        player_id, player_name, _position = event_player(event)
        if not player_id or not player_name:
            continue
        minute = float(event.get("minute") or 0)
        second = float(event.get("second") or 0)
        event_minute = minute + second / 60.0
        if event_minute > max_minute[player_id]:
            max_minute[player_id] = event_minute
    return {player_id: clamp(value, 1.0, 130.0) for player_id, value in max_minute.items()}


def insert_lineups(conn, lineups: list[dict], match_context: dict, imported_at: str) -> int:
    count = 0
    for team_lineup in lineups:
        team = team_lineup.get("team") or {}
        raw_team_id = team.get("id") or team_lineup.get("team_id")
        team_name = team.get("name") or team_lineup.get("team_name") or "unknown"
        team_id = club_team_id(raw_team_id, team_name)
        formation = None
        lineup_id = f"lineup-{match_context['match_id']}-{team_id}"
        conn.execute(
            """
            INSERT OR REPLACE INTO club_lineups (
                club_lineup_id, club_match_id, club_team_id, formation, source_id, imported_at, notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                lineup_id,
                match_context["match_id"],
                team_id,
                formation,
                SOURCE_ID,
                imported_at,
                "StatsBomb Open Data lineup.",
            ),
        )
        for player in team_lineup.get("lineup", []):
            player_id = player.get("player_id")
            player_name = player.get("player_name") or "unknown"
            positions = player.get("positions") or []
            first_position = positions[0] if positions else {}
            position_name = first_position.get("position") if isinstance(first_position, dict) else None
            is_starter = 1 if first_position.get("start_reason") == "Starting XI" else 0
            cid = upsert_club_player(
                conn,
                player_id,
                player_name,
                player.get("player_nickname"),
                lookup_country(player),
                position_name,
                imported_at,
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO club_lineup_players (
                    club_lineup_player_id, club_lineup_id, club_player_id, club_team_id, player_name,
                    jersey_number, position, is_starter, minutes_played, source_id, notes
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"lineup-player-{lineup_id}-{cid}",
                    lineup_id,
                    cid,
                    team_id,
                    player_name,
                    player.get("jersey_number"),
                    position_name,
                    is_starter,
                    None,
                    SOURCE_ID,
                    "StatsBomb Open Data lineup player.",
                ),
            )
            count += 1
    return count


def insert_stats(
    conn,
    match_context: dict,
    team_stats: dict,
    player_stats: dict,
    player_meta: dict,
    minutes: dict,
    imported_at: str,
) -> tuple[int, int]:
    home_id = match_context["home_team_id"]
    away_id = match_context["away_team_id"]
    team_count = 0
    for team_id, stats in team_stats.items():
        opponent_id = away_id if team_id == home_id else home_id
        values = {key: int(stats.get(key, 0) or 0) for key in (
            "goals",
            "shots",
            "shots_on_target",
            "passes",
            "completed_passes",
            "passes_under_pressure",
            "carries",
            "dribbles",
            "pressures",
            "counterpressures",
            "duels",
            "interceptions",
            "blocks",
            "clearances",
            "fouls_committed",
            "fouls_won",
            "corners",
            "crosses",
            "deep_progressions",
            "touches_box",
            "set_piece_shots",
            "open_play_shots",
            "possession_events",
        )}
        conn.execute(
            """
            INSERT OR REPLACE INTO club_team_match_stats (
                stat_id, club_match_id, club_team_id, opponent_club_team_id, is_home, goals,
                shots, xg, shots_on_target, passes, completed_passes, passes_under_pressure,
                carries, dribbles, pressures, counterpressures, duels, interceptions, blocks,
                clearances, fouls_committed, fouls_won, corners, crosses, deep_progressions,
                touches_box, set_piece_shots, open_play_shots, possession_events, source_id, notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"club-team-stat-{match_context['match_id']}-{team_id}",
                match_context["match_id"],
                team_id,
                opponent_id,
                1 if team_id == home_id else 0,
                values["goals"],
                values["shots"],
                float(stats.get("xg", 0.0) or 0.0),
                values["shots_on_target"],
                values["passes"],
                values["completed_passes"],
                values["passes_under_pressure"],
                values["carries"],
                values["dribbles"],
                values["pressures"],
                values["counterpressures"],
                values["duels"],
                values["interceptions"],
                values["blocks"],
                values["clearances"],
                values["fouls_committed"],
                values["fouls_won"],
                values["corners"],
                values["crosses"],
                values["deep_progressions"],
                values["touches_box"],
                values["set_piece_shots"],
                values["open_play_shots"],
                values["possession_events"],
                SOURCE_ID,
                "Aggregated from StatsBomb Open Data events.",
            ),
        )
        team_count += 1

    player_count = 0
    for player_id, stats in player_stats.items():
        name = player_meta["names"].get(player_id)
        if not name:
            continue
        team_id = player_meta["teams"].get(player_id)
        position = player_meta["positions"].get(player_id)
        raw_player_id = player_id.replace(f"{PROVIDER}-player-", "")
        upsert_club_player(conn, raw_player_id, name, None, None, position, imported_at)
        values = {key: int(stats.get(key, 0) or 0) for key in (
            "goals",
            "shots",
            "passes",
            "completed_passes",
            "key_passes",
            "assists",
            "carries",
            "dribbles",
            "pressures",
            "counterpressures",
            "duels",
            "interceptions",
            "blocks",
            "clearances",
            "fouls_committed",
            "fouls_won",
            "under_pressure_events",
            "touches_box",
        )}
        conn.execute(
            """
            INSERT OR REPLACE INTO club_player_match_stats (
                stat_id, club_match_id, club_team_id, club_player_id, player_name, position,
                minutes_played, goals, shots, xg, passes, completed_passes, key_passes,
                assists, carries, dribbles, pressures, counterpressures, duels,
                interceptions, blocks, clearances, fouls_committed, fouls_won,
                under_pressure_events, touches_box, source_id, notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"club-player-stat-{match_context['match_id']}-{player_id}",
                match_context["match_id"],
                team_id,
                player_id,
                name,
                position,
                float(minutes.get(player_id, 0.0) or 0.0),
                values["goals"],
                values["shots"],
                float(stats.get("xg", 0.0) or 0.0),
                values["passes"],
                values["completed_passes"],
                values["key_passes"],
                values["assists"],
                values["carries"],
                values["dribbles"],
                values["pressures"],
                values["counterpressures"],
                values["duels"],
                values["interceptions"],
                values["blocks"],
                values["clearances"],
                values["fouls_committed"],
                values["fouls_won"],
                values["under_pressure_events"],
                values["touches_box"],
                SOURCE_ID,
                "Aggregated from StatsBomb Open Data events.",
            ),
        )
        player_count += 1
    return team_count, player_count


def per90(value: float, minutes: float) -> float:
    if minutes <= 0:
        return 0.0
    return value * 90.0 / minutes


def score_from_rate(rate: float, low: float, high: float) -> float:
    if high <= low:
        return 50.0
    return clamp(100.0 * (rate - low) / (high - low), 0.0, 100.0)


def build_player_feature_snapshots(conn, snapshot_date: str, min_minutes: float, imported_at: str) -> int:
    rows = conn.execute(
        """
        SELECT
            club_player_id,
            MAX(player_name) AS player_name,
            COUNT(DISTINCT club_match_id) AS sample_matches,
            SUM(COALESCE(minutes_played, 0)) AS minutes_played,
            SUM(COALESCE(goals, 0)) AS goals,
            SUM(COALESCE(shots, 0)) AS shots,
            SUM(COALESCE(xg, 0)) AS xg,
            SUM(COALESCE(passes, 0)) AS passes,
            SUM(COALESCE(completed_passes, 0)) AS completed_passes,
            SUM(COALESCE(key_passes, 0)) AS key_passes,
            SUM(COALESCE(carries, 0)) AS carries,
            SUM(COALESCE(dribbles, 0)) AS dribbles,
            SUM(COALESCE(pressures, 0)) AS pressures,
            SUM(COALESCE(counterpressures, 0)) AS counterpressures,
            SUM(COALESCE(duels, 0)) AS duels,
            SUM(COALESCE(interceptions, 0)) AS interceptions,
            SUM(COALESCE(blocks, 0)) AS blocks,
            SUM(COALESCE(clearances, 0)) AS clearances,
            SUM(COALESCE(touches_box, 0)) AS touches_box
        FROM club_player_match_stats
        GROUP BY club_player_id
        HAVING minutes_played >= ?
        """,
        (min_minutes,),
    ).fetchall()
    count = 0
    for row in rows:
        minutes = float(row["minutes_played"] or 0.0)
        goals_per90 = per90(float(row["goals"] or 0.0), minutes)
        xg_per90 = per90(float(row["xg"] or 0.0), minutes)
        shots_per90 = per90(float(row["shots"] or 0.0), minutes)
        passes_per90 = per90(float(row["passes"] or 0.0), minutes)
        key_passes_per90 = per90(float(row["key_passes"] or 0.0), minutes)
        pressures_per90 = per90(float(row["pressures"] or 0.0), minutes)
        carries_per90 = per90(float(row["carries"] or 0.0), minutes)
        dribbles_per90 = per90(float(row["dribbles"] or 0.0), minutes)
        duels_per90 = per90(float(row["duels"] or 0.0), minutes)
        defensive_actions = (
            float(row["interceptions"] or 0.0)
            + float(row["blocks"] or 0.0)
            + float(row["clearances"] or 0.0)
        )
        def_actions_per90 = per90(defensive_actions, minutes)
        touches_box_per90 = per90(float(row["touches_box"] or 0.0), minutes)
        pass_completion = (
            float(row["completed_passes"] or 0.0) / float(row["passes"] or 1.0)
            if float(row["passes"] or 0.0) > 0
            else 0.0
        )
        attacking_score = clamp(
            0.40 * score_from_rate(xg_per90, 0.0, 0.65)
            + 0.25 * score_from_rate(shots_per90, 0.0, 4.5)
            + 0.20 * score_from_rate(key_passes_per90, 0.0, 3.0)
            + 0.15 * score_from_rate(touches_box_per90, 0.0, 8.0)
        )
        possession_score = clamp(
            0.40 * score_from_rate(passes_per90, 10.0, 85.0)
            + 0.30 * pass_completion * 100.0
            + 0.20 * score_from_rate(carries_per90, 3.0, 45.0)
            + 0.10 * score_from_rate(dribbles_per90, 0.0, 6.0)
        )
        defensive_score = clamp(
            0.45 * score_from_rate(def_actions_per90, 0.0, 12.0)
            + 0.35 * score_from_rate(duels_per90, 0.0, 18.0)
            + 0.20 * score_from_rate(pressures_per90, 0.0, 28.0)
        )
        transition_score = clamp(
            0.45 * score_from_rate(carries_per90, 3.0, 45.0)
            + 0.30 * score_from_rate(pressures_per90, 0.0, 28.0)
            + 0.25 * score_from_rate(dribbles_per90, 0.0, 6.0)
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO club_player_feature_snapshots (
                snapshot_id, club_player_id, provider, snapshot_date, sample_matches, minutes_played,
                goals_per90, xg_per90, shots_per90, passes_per90, pass_completion_pct,
                key_passes_per90, pressures_per90, carries_per90, dribbles_per90, duels_per90,
                def_actions_per90, touches_box_per90, attacking_score, possession_score,
                defensive_score, transition_score, source_id, notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"club-player-feature-{row['club_player_id']}-{snapshot_date}",
                row["club_player_id"],
                PROVIDER,
                snapshot_date,
                int(row["sample_matches"] or 0),
                minutes,
                goals_per90,
                xg_per90,
                shots_per90,
                passes_per90,
                pass_completion * 100.0,
                key_passes_per90,
                pressures_per90,
                carries_per90,
                dribbles_per90,
                duels_per90,
                def_actions_per90,
                touches_box_per90,
                attacking_score,
                possession_score,
                defensive_score,
                transition_score,
                SOURCE_ID,
                f"Derived from StatsBomb Open Data club match events; imported_at={imported_at}.",
            ),
        )
        count += 1
    return count


def select_competitions(competitions: list[dict], args) -> list[dict]:
    selected = competitions
    if args.competition_id is not None:
        selected = [row for row in selected if int(row["competition_id"]) == args.competition_id]
    if args.season_id is not None:
        selected = [row for row in selected if int(row["season_id"]) == args.season_id]
    if args.competition_name:
        needle = args.competition_name.lower()
        selected = [row for row in selected if needle in (row.get("competition_name") or "").lower()]
    if args.season_name:
        selected = [row for row in selected if args.season_name.lower() in (row.get("season_name") or "").lower()]
    if args.male_only:
        selected = [row for row in selected if row.get("competition_gender") == "male"]
    if args.club_only:
        selected = [row for row in selected if not row.get("competition_international")]
    return selected[: args.max_competitions] if args.max_competitions else selected


def import_statsbomb(args) -> dict:
    imported_at = now_utc()
    cache_dir = Path(args.raw_dir)
    competitions = fetch_json(
        f"{BASE_URL}/competitions.json",
        cache_dir / "competitions.json",
        args.refresh,
    )
    selected_competitions = select_competitions(competitions, args)
    conn = connect(args.db)
    stats = {
        "competitions": 0,
        "matches": 0,
        "lineups": 0,
        "team_match_stats": 0,
        "player_match_stats": 0,
        "player_feature_snapshots": 0,
        "skipped_events": 0,
    }
    try:
        ensure_schema(conn)
        for competition in selected_competitions:
            competition_db_id = insert_competition(conn, competition, imported_at)
            stats["competitions"] += 1
            matches_url = f"{BASE_URL}/matches/{competition['competition_id']}/{competition['season_id']}.json"
            matches_cache = cache_dir / "matches" / str(competition["competition_id"]) / f"{competition['season_id']}.json"
            matches = fetch_json(matches_url, matches_cache, args.refresh)
            if args.max_matches:
                matches = matches[: args.max_matches]
            for match in matches:
                match_context = insert_match(conn, match, competition, competition_db_id, imported_at)
                stats["matches"] += 1
                match_id = match["match_id"]
                try:
                    lineups = fetch_json(
                        f"{BASE_URL}/lineups/{match_id}.json",
                        cache_dir / "lineups" / f"{match_id}.json",
                        args.refresh,
                    )
                    stats["lineups"] += insert_lineups(conn, lineups, match_context, imported_at)
                except Exception as exc:  # noqa: BLE001
                    stats["skipped_events"] += 1
                    print(f"Warning: failed lineups for {match_id}: {exc}")
                try:
                    events = fetch_json(
                        f"{BASE_URL}/events/{match_id}.json",
                        cache_dir / "events" / f"{match_id}.json",
                        args.refresh,
                    )
                except Exception as exc:  # noqa: BLE001
                    stats["skipped_events"] += 1
                    print(f"Warning: failed events for {match_id}: {exc}")
                    continue
                team_stats, player_stats, player_meta = aggregate_events(events, match_context, imported_at)
                minutes = player_minutes_from_events(events)
                team_count, player_count = insert_stats(
                    conn,
                    match_context,
                    team_stats,
                    player_stats,
                    player_meta,
                    minutes,
                    imported_at,
                )
                stats["team_match_stats"] += team_count
                stats["player_match_stats"] += player_count
                conn.commit()
        stats["player_feature_snapshots"] = build_player_feature_snapshots(
            conn,
            args.snapshot_date,
            args.min_feature_minutes,
            imported_at,
        )
        conn.commit()
        return stats
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, help="SQLite database path.")
    parser.add_argument("--raw-dir", default="data/raw/statsbomb-open-data", help="Cache directory for raw JSON.")
    parser.add_argument("--competition-id", type=int, help="StatsBomb competition_id.")
    parser.add_argument("--season-id", type=int, help="StatsBomb season_id.")
    parser.add_argument("--competition-name", help="Case-insensitive competition-name filter.")
    parser.add_argument("--season-name", help="Case-insensitive season-name filter.")
    parser.add_argument("--max-competitions", type=int, default=1, help="Maximum competition-season rows to import.")
    parser.add_argument("--max-matches", type=int, default=20, help="Maximum matches per competition-season.")
    parser.add_argument("--male-only", action="store_true", help="Import only men's competitions.")
    parser.add_argument("--club-only", action="store_true", help="Import only club competitions.")
    parser.add_argument("--refresh", action="store_true", help="Refetch raw JSON even if cached.")
    parser.add_argument("--snapshot-date", default="2026-06-21", help="Feature snapshot date.")
    parser.add_argument("--min-feature-minutes", type=float, default=90.0)
    args = parser.parse_args()
    result = import_statsbomb(args)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
