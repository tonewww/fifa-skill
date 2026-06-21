#!/usr/bin/env python3
"""Import normalized CSV data into the World Cup predictor database."""

from __future__ import annotations

import argparse
import csv
import sqlite3
from pathlib import Path

from common import TABLE_COLUMNS, coerce_value, connect, now_utc, slugify, table_exists


GENERATED_ID_COLUMNS = {
    "sources": "source_id",
    "teams": "team_id",
    "players": "player_id",
    "fixtures": "match_id",
    "team_results": "result_id",
    "fifa_rankings": "ranking_id",
    "player_ratings": "rating_id",
    "injuries": "injury_id",
    "lineups": "lineup_id",
    "lineup_players": "lineup_player_id",
    "tactical_plans": "plan_id",
    "formation_matchup_stats": "matchup_id",
    "model_parameters": "parameter_id",
    "backtest_runs": "run_id",
    "backtest_predictions": "backtest_prediction_id",
    "team_style_profiles": "profile_id",
    "team_strength_snapshots": "snapshot_id",
    "matchup_adjustments": "adjustment_id",
    "predictions": "prediction_id",
    "unified_players": "unified_player_id",
    "player_identity_links": "link_id",
    "player_feature_snapshots": "snapshot_id",
}


def generate_id(table: str, row: dict[str, str], index: int) -> str:
    if table == "sources":
        return slugify(row.get("name") or row.get("url") or f"source-{index}")
    if table == "teams":
        return str(row.get("fifa_code") or slugify(row.get("name") or index)).upper()
    if table == "players":
        return "-".join(
            [
                str(row.get("team_id") or row.get("fifa_code") or "team").upper(),
                slugify(row.get("name") or row.get("display_name") or f"player-{index}"),
            ]
        )
    if table == "unified_players":
        return f"player-{slugify(row.get('canonical_name') or row.get('display_name') or index)}"
    if table == "player_identity_links":
        raw = "|".join(
            [
                row.get("source_table", ""),
                row.get("source_player_id", ""),
                row.get("provider", ""),
            ]
        )
        return f"identity-{slugify(raw or index)}"
    if table == "player_feature_snapshots":
        raw = "|".join(
            [
                row.get("provider", ""),
                row.get("snapshot_date", ""),
                row.get("unified_player_id", ""),
                row.get("player_id", ""),
            ]
        )
        return f"pfeature-{slugify(raw or index)}"
    if table == "fixtures":
        raw = "|".join(
            [
                row.get("match_date", ""),
                row.get("team_a_id", ""),
                row.get("team_b_id", ""),
                row.get("stage", ""),
            ]
        )
        return f"fixture-{slugify(raw or index)}"
    if table == "team_results":
        raw = "|".join(
            [
                row.get("match_date", ""),
                row.get("team_id", ""),
                row.get("opponent_team_id", ""),
                row.get("competition", ""),
            ]
        )
        return f"result-{slugify(raw or index)}"
    if table == "fifa_rankings":
        raw = "|".join(
            [
                row.get("ranking_date", ""),
                row.get("ranking_type", ""),
                row.get("team_id", ""),
                row.get("fifa_code", ""),
            ]
        )
        return f"ranking-{slugify(raw or index)}"
    if table == "player_ratings":
        raw = "|".join(
            [
                row.get("provider", ""),
                row.get("rating_date", ""),
                row.get("player_id", ""),
                row.get("team_id", ""),
            ]
        )
        return f"prating-{slugify(raw or index)}"
    if table == "injuries":
        raw = "|".join(
            [
                row.get("verified_at", ""),
                row.get("player_id", ""),
                row.get("team_id", ""),
                row.get("status", ""),
            ]
        )
        return f"injury-{slugify(raw or index)}"
    if table == "lineups":
        raw = "|".join(
            [
                row.get("match_id", ""),
                row.get("team_id", ""),
                row.get("opponent_team_id", ""),
                row.get("lineup_type", ""),
                row.get("as_of", ""),
            ]
        )
        return f"lineup-{slugify(raw or index)}"
    if table == "lineup_players":
        raw = "|".join(
            [
                row.get("lineup_id", ""),
                row.get("player_id", ""),
                row.get("role", ""),
            ]
        )
        return f"lineup-player-{slugify(raw or index)}"
    if table == "tactical_plans":
        raw = "|".join(
            [
                row.get("as_of_date", ""),
                row.get("team_id", ""),
                row.get("opponent_team_id", ""),
                row.get("formation", ""),
            ]
        )
        return f"tactic-{slugify(raw or index)}"
    if table == "formation_matchup_stats":
        return f"formation-{slugify(row.get('formation_a'))}-vs-{slugify(row.get('formation_b'))}"
    if table == "model_parameters":
        raw = "|".join([row.get("model_version", ""), row.get("as_of_date", "")])
        return f"params-{slugify(raw or index)}"
    if table == "backtest_runs":
        raw = "|".join([row.get("model_version", ""), row.get("run_at", "")])
        return f"backtest-run-{slugify(raw or index)}"
    if table == "backtest_predictions":
        raw = "|".join(
            [
                row.get("run_id", ""),
                row.get("match_id", ""),
                row.get("team_a_id", ""),
                row.get("team_b_id", ""),
            ]
        )
        return f"backtest-pred-{slugify(raw or index)}"
    if table == "team_style_profiles":
        return f"style-{slugify(row.get('team_id'))}-{slugify(row.get('profile_date') or now_utc())}"
    if table == "team_strength_snapshots":
        return f"strength-{slugify(row.get('team_id'))}-{slugify(row.get('rating_date') or now_utc())}"
    if table == "matchup_adjustments":
        raw = "|".join(
            [
                row.get("as_of_date", ""),
                row.get("team_a_id", ""),
                row.get("team_b_id", ""),
                row.get("affected_team_id", ""),
                row.get("category", ""),
            ]
        )
        return f"adj-{slugify(raw or index)}"
    return f"{table}-{index}"


def import_csv(db_path: Path, table: str, csv_path: Path, replace: bool) -> int:
    if table not in TABLE_COLUMNS:
        known = ", ".join(sorted(TABLE_COLUMNS))
        raise SystemExit(f"Unknown table '{table}'. Known tables: {known}")

    conn = connect(db_path)
    try:
        if not table_exists(conn, table):
            raise SystemExit(f"Table '{table}' does not exist. Run init_database.py first.")

        columns = TABLE_COLUMNS[table]
        id_column = GENERATED_ID_COLUMNS.get(table)
        rows = []
        with csv_path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            missing = [column for column in reader.fieldnames or [] if column not in columns]
            if missing:
                raise SystemExit(f"{csv_path} has unsupported columns for {table}: {', '.join(missing)}")
            for index, raw_row in enumerate(reader, start=1):
                row = {column: raw_row.get(column) for column in columns}
                if id_column and not row.get(id_column):
                    row[id_column] = generate_id(table, row, index)
                rows.append([coerce_value(column, row.get(column)) for column in columns])

        if replace:
            conn.execute(f"DELETE FROM {table}")
        placeholders = ", ".join(["?"] * len(columns))
        col_sql = ", ".join(columns)
        update_sql = ", ".join([f"{column} = excluded.{column}" for column in columns[1:]])
        sql = f"""
            INSERT INTO {table} ({col_sql})
            VALUES ({placeholders})
            ON CONFLICT({columns[0]}) DO UPDATE SET {update_sql}
        """
        conn.executemany(sql, rows)
        conn.commit()
        return len(rows)
    except sqlite3.IntegrityError as exc:
        raise SystemExit(f"Import failed integrity check: {exc}") from exc
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, help="SQLite database path.")
    parser.add_argument("--table", required=True, help="Target table name.")
    parser.add_argument("--csv", required=True, help="Normalized CSV file path.")
    parser.add_argument("--replace", action="store_true", help="Delete existing rows in the table first.")
    args = parser.parse_args()

    count = import_csv(Path(args.db), args.table, Path(args.csv), args.replace)
    print(f"Imported {count} rows into {args.table}")


if __name__ == "__main__":
    main()
