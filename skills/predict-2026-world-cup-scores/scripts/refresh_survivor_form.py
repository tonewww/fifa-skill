#!/usr/bin/env python3
"""Apply bounded World Cup form corrections for teams still alive in the bracket."""

from __future__ import annotations

import argparse
import json
import unicodedata
from pathlib import Path

from common import clamp, connect
from ingest_results_json import team_name_map, resolve_team


def normalize_name(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(char for char in text if not unicodedata.combining(char))
    keep = [char.lower() if char.isalnum() else " " for char in text]
    return " ".join("".join(keep).split())


def result_files(results_dir: Path, until: str) -> list[Path]:
    files = []
    for path in sorted(results_dir.glob("*.json")):
        if path.stem <= until:
            files.append(path)
    return files


def result_winner(item: dict, mapping: dict[str, str]) -> str | None:
    if item.get("advanced_team"):
        return resolve_team(str(item["advanced_team"]), mapping)
    home_score = int(item.get("home_score", 0))
    away_score = int(item.get("away_score", 0))
    if home_score > away_score:
        return resolve_team(str(item["home_team"]), mapping)
    if away_score > home_score:
        return resolve_team(str(item["away_team"]), mapping)
    return None


def active_teams_from_results(results_dir: Path, until: str, mapping: dict[str, str]) -> set[str]:
    active: set[str] = set()
    for path in result_files(results_dir, until):
        try:
            items = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for item in items:
            stage = str(item.get("stage") or "").lower()
            if "round" not in stage and "knockout" not in stage:
                continue
            winner = result_winner(item, mapping)
            if winner:
                active.add(winner)
    return active


def active_teams_from_upcoming(path: Path, mapping: dict[str, str]) -> set[str]:
    if not path.exists():
        return set()
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and "matches" in raw:
        items = raw["matches"]
    elif isinstance(raw, list):
        items = raw
    else:
        items = [raw]
    active: set[str] = set()
    for item in items:
        active.add(resolve_team(str(item["home_team"]), mapping))
        active.add(resolve_team(str(item["away_team"]), mapping))
    return active


def tournament_stats(conn, team_id: str, since: str, until: str) -> dict:
    rows = conn.execute(
        """
        SELECT *
        FROM team_results
        WHERE team_id = ?
          AND match_date >= ?
          AND match_date <= ?
          AND lower(coalesce(competition, '')) LIKE '%world cup%'
        ORDER BY match_date DESC, result_id DESC
        """,
        (team_id, since, until),
    ).fetchall()
    rows = [dict(row) for row in rows]
    if not rows:
        return {
            "matches": 0,
            "goals_for": 0.0,
            "goals_against": 0.0,
            "goal_diff": 0.0,
            "points_per_match": 1.0,
            "clean_sheet_rate": 0.0,
            "failed_score_rate": 0.0,
        }
    points = 0
    clean = 0
    failed = 0
    goals_for = 0
    goals_against = 0
    for row in rows:
        gf = int(row.get("goals_for") or 0)
        ga = int(row.get("goals_against") or 0)
        goals_for += gf
        goals_against += ga
        points += 3 if gf > ga else 1 if gf == ga else 0
        clean += 1 if ga == 0 else 0
        failed += 1 if gf == 0 else 0
    sample = len(rows)
    return {
        "matches": sample,
        "goals_for": goals_for / sample,
        "goals_against": goals_against / sample,
        "goal_diff": (goals_for - goals_against) / sample,
        "points_per_match": points / sample,
        "clean_sheet_rate": clean / sample,
        "failed_score_rate": failed / sample,
    }


def correction_from_stats(stats: dict) -> dict:
    sample = int(stats["matches"])
    if sample <= 0:
        return {"form_delta": 0.0, "attack_delta": 0.0, "defense_delta": 0.0, "overall_delta": 0.0, "uncertainty_delta": 0.0}
    sample_weight = min(sample / 5.0, 1.0)
    form_delta = (
        (float(stats["points_per_match"]) - 1.35) * 2.1
        + float(stats["goal_diff"]) * 3.4
        + (float(stats["clean_sheet_rate"]) - 0.28) * 1.4
        - max(float(stats["failed_score_rate"]) - 0.22, 0.0) * 1.6
    ) * sample_weight
    attack_delta = (float(stats["goals_for"]) - 1.35) * 1.65 * sample_weight
    defense_delta = (1.15 - float(stats["goals_against"])) * 1.85 * sample_weight
    form_delta = clamp(form_delta, -4.0, 5.0)
    attack_delta = clamp(attack_delta, -2.5, 3.0)
    defense_delta = clamp(defense_delta, -2.5, 3.0)
    overall_delta = clamp(0.42 * form_delta + 0.18 * attack_delta + 0.18 * defense_delta, -2.2, 2.8)
    uncertainty_delta = -min(sample, 5) * 0.45
    return {
        "form_delta": form_delta,
        "attack_delta": attack_delta,
        "defense_delta": defense_delta,
        "overall_delta": overall_delta,
        "uncertainty_delta": uncertainty_delta,
    }


def apply_corrections(db_path: Path, rating_date: str, results_dir: Path, upcoming_json: Path, since: str) -> dict:
    conn = connect(db_path)
    try:
        mapping = team_name_map(conn)
        # An upcoming slate is the authoritative active-bracket set. Historical
        # knockout winners include teams that were eliminated in later rounds.
        active = active_teams_from_upcoming(upcoming_json, mapping)
        if not active:
            active = active_teams_from_results(results_dir, rating_date, mapping)
        rows = []
        for team_id in sorted(active):
            stats = tournament_stats(conn, team_id, since, rating_date)
            deltas = correction_from_stats(stats)
            snapshot_id = f"strength-{team_id.lower()}-{rating_date}"
            snapshot = conn.execute(
                "SELECT * FROM team_strength_snapshots WHERE snapshot_id = ?",
                (snapshot_id,),
            ).fetchone()
            if snapshot is None:
                continue
            notes = snapshot["notes"] or ""
            marker = " Survivor World Cup form correction:"
            notes = notes.split(marker, 1)[0].rstrip()
            notes = (
                notes
                + marker
                + f" matches={stats['matches']}, gf={stats['goals_for']:.2f}, ga={stats['goals_against']:.2f}, "
                + f"ppg={stats['points_per_match']:.2f}, fd={deltas['form_delta']:+.2f}, "
                + f"ad={deltas['attack_delta']:+.2f}, dd={deltas['defense_delta']:+.2f}."
            )
            conn.execute(
                """
                UPDATE team_strength_snapshots
                SET form_rating = ?,
                    attack_rating = ?,
                    defense_rating = ?,
                    overall_rating = ?,
                    uncertainty = ?,
                    notes = ?
                WHERE snapshot_id = ?
                """,
                (
                    round(clamp(float(snapshot["form_rating"]) + deltas["form_delta"]), 2),
                    round(clamp(float(snapshot["attack_rating"]) + deltas["attack_delta"]), 2),
                    round(clamp(float(snapshot["defense_rating"]) + deltas["defense_delta"]), 2),
                    round(clamp(float(snapshot["overall_rating"]) + deltas["overall_delta"]), 2),
                    round(clamp(float(snapshot["uncertainty"]) + deltas["uncertainty_delta"], 4.0, 35.0), 2),
                    notes,
                    snapshot_id,
                ),
            )
            rows.append({"team_id": team_id, **stats, **deltas})
        conn.commit()
        return {"rating_date": rating_date, "active_teams": len(active), "updated": len(rows), "sample": rows[:16]}
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--rating-date", required=True)
    parser.add_argument("--results-dir", default="data/results")
    parser.add_argument("--upcoming-json", required=True)
    parser.add_argument("--since", default="2026-06-20")
    args = parser.parse_args()
    result = apply_corrections(Path(args.db), args.rating_date, Path(args.results_dir), Path(args.upcoming_json), args.since)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
