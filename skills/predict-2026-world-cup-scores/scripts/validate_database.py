#!/usr/bin/env python3
"""Validate database completeness and freshness for World Cup predictions."""

from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

from common import TABLE_COLUMNS, connect, table_exists


def parse_date(value: str | None) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return dt.date.fromisoformat(value[:10])
        except ValueError:
            return None


def validate(db_path: Path, strict: bool) -> int:
    conn = connect(db_path)
    issues: list[tuple[str, str]] = []
    warnings: list[str] = []
    try:
        for table in TABLE_COLUMNS:
            if not table_exists(conn, table):
                issues.append(("schema", f"Missing table: {table}"))

        if issues:
            return print_report(issues, warnings)

        team_count = conn.execute("SELECT COUNT(*) AS c FROM teams").fetchone()["c"]
        if team_count != 48:
            target = "issues" if strict else "warnings"
            message = f"Expected 48 World Cup teams, found {team_count}."
            (issues if target == "issues" else warnings).append(message)

        teams_without_code = conn.execute(
            "SELECT name FROM teams WHERE fifa_code IS NULL OR trim(fifa_code) = ''"
        ).fetchall()
        for row in teams_without_code:
            issues.append(("teams", f"Team missing FIFA code: {row['name']}"))

        player_counts = conn.execute(
            """
            SELECT t.team_id, t.name, COUNT(p.player_id) AS player_count
            FROM teams t
            LEFT JOIN players p ON p.team_id = t.team_id
            GROUP BY t.team_id, t.name
            ORDER BY player_count ASC, t.name
            """
        ).fetchall()
        for row in player_counts:
            count = row["player_count"]
            if count == 0:
                issues.append(("players", f"{row['name']} has no players."))
            elif count < 23:
                warnings.append(f"{row['name']} has only {count} players.")
            elif count != 26:
                warnings.append(f"{row['name']} has {count} players; verify final squad size.")

        missing_styles = conn.execute(
            """
            SELECT t.name
            FROM teams t
            LEFT JOIN team_style_profiles s ON s.team_id = t.team_id
            WHERE s.profile_id IS NULL
            ORDER BY t.name
            """
        ).fetchall()
        for row in missing_styles:
            warnings.append(f"Missing style profile: {row['name']}")

        missing_strength = conn.execute(
            """
            SELECT t.name
            FROM teams t
            LEFT JOIN team_strength_snapshots s ON s.team_id = t.team_id
            WHERE s.snapshot_id IS NULL
            ORDER BY t.name
            """
        ).fetchall()
        for row in missing_strength:
            warnings.append(f"Missing strength snapshot: {row['name']}")

        stale_sources = conn.execute(
            """
            SELECT source_id, name, retrieved_at, freshness_days
            FROM sources
            WHERE retrieved_at IS NOT NULL AND freshness_days IS NOT NULL
            """
        ).fetchall()
        today = dt.date.today()
        for row in stale_sources:
            retrieved = parse_date(row["retrieved_at"])
            if retrieved is None:
                warnings.append(f"Could not parse retrieved_at for source {row['source_id']}.")
                continue
            if (today - retrieved).days > int(row["freshness_days"]):
                warnings.append(
                    f"Source may be stale: {row['name']} retrieved {retrieved}, "
                    f"freshness window {row['freshness_days']} days."
                )

        player_rating_gaps = conn.execute(
            """
            SELECT t.name, COUNT(*) AS missing_count
            FROM players p
            JOIN teams t ON t.team_id = p.team_id
            WHERE p.rating_overall IS NULL
            GROUP BY t.name
            ORDER BY missing_count DESC
            """
        ).fetchall()
        for row in player_rating_gaps:
            warnings.append(f"{row['name']} has {row['missing_count']} players without overall rating.")

        missing_rankings = conn.execute(
            """
            SELECT t.name
            FROM teams t
            LEFT JOIN fifa_rankings r ON r.team_id = t.team_id
            WHERE r.ranking_id IS NULL
              AND t.fifa_rank IS NULL
            ORDER BY t.name
            """
        ).fetchall()
        if missing_rankings:
            warnings.append(
                f"Missing FIFA ranking rows for {len(missing_rankings)} teams; import fifa_rankings before enhanced predictions."
            )

        teams_without_recent_results = conn.execute(
            """
            SELECT t.name, COUNT(r.result_id) AS result_count
            FROM teams t
            LEFT JOIN team_results r ON r.team_id = t.team_id
            GROUP BY t.team_id, t.name
            HAVING result_count < 6
            ORDER BY result_count ASC, t.name
            """
        ).fetchall()
        if teams_without_recent_results:
            warnings.append(
                f"{len(teams_without_recent_results)} teams have fewer than 6 recent result rows; form/xG confidence is limited."
            )

        teams_without_xg = conn.execute(
            """
            SELECT t.name
            FROM teams t
            LEFT JOIN team_results r
              ON r.team_id = t.team_id
             AND r.xg_for IS NOT NULL
             AND r.xg_against IS NOT NULL
            WHERE r.result_id IS NULL
            ORDER BY t.name
            """
        ).fetchall()
        if teams_without_xg:
            warnings.append(f"{len(teams_without_xg)} teams have no xG-backed recent result rows.")

        provider_rating_count = conn.execute("SELECT COUNT(*) AS c FROM player_ratings").fetchone()["c"]
        if provider_rating_count == 0:
            warnings.append("No third-party/provider player_ratings imported; predictions use baseline heuristic player ratings.")

        injury_count = conn.execute("SELECT COUNT(*) AS c FROM injuries").fetchone()["c"]
        if injury_count == 0:
            warnings.append("No injury/availability feed imported; matchday predictions should refresh injuries and suspensions.")

        lineup_count = conn.execute("SELECT COUNT(*) AS c FROM lineups").fetchone()["c"]
        if lineup_count == 0:
            warnings.append("No expected or official lineups imported; lineup uncertainty remains high.")

        tactical_plan_count = conn.execute("SELECT COUNT(*) AS c FROM tactical_plans").fetchone()["c"]
        if tactical_plan_count == 0:
            warnings.append("No tactical_plans imported; tactical arrangement report falls back to style profiles.")

        formation_stat_count = conn.execute("SELECT COUNT(*) AS c FROM formation_matchup_stats").fetchone()["c"]
        if formation_stat_count == 0:
            warnings.append("No formation_matchup_stats available; run analyze_formation_matchups.py after importing completed matches.")

        model_parameter_count = conn.execute("SELECT COUNT(*) AS c FROM model_parameters").fetchone()["c"]
        if model_parameter_count == 0:
            warnings.append("No model_parameters rows stored; predict_match.py will use built-in v0.2 defaults.")

        backtest_count = conn.execute("SELECT COUNT(*) AS c FROM backtest_runs").fetchone()["c"]
        if backtest_count == 0:
            warnings.append("No backtest_runs stored; run backtest_model.py when completed fixtures are available.")

        return print_report(issues, warnings)
    finally:
        conn.close()


def print_report(issues: list[tuple[str, str]] | list[str], warnings: list[str]) -> int:
    if issues:
        print("FAILED")
        for issue in issues:
            if isinstance(issue, tuple):
                print(f"[issue:{issue[0]}] {issue[1]}")
            else:
                print(f"[issue] {issue}")
    else:
        print("PASSED")
    for warning in warnings:
        print(f"[warning] {warning}")
    return 1 if issues else 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, help="SQLite database path.")
    parser.add_argument("--strict", action="store_true", help="Treat incomplete team coverage as an error.")
    args = parser.parse_args()
    raise SystemExit(validate(Path(args.db), args.strict))


if __name__ == "__main__":
    main()
