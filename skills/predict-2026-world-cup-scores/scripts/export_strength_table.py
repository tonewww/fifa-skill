#!/usr/bin/env python3
"""Export the latest team strength table as CSV or Markdown."""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from common import connect


EXPORT_COLUMNS = [
    "rank",
    "team_id",
    "team_name",
    "rating_date",
    "overall_rating",
    "attack_rating",
    "defense_rating",
    "possession_rating",
    "transition_rating",
    "set_piece_rating",
    "goalkeeper_rating",
    "depth_rating",
    "form_rating",
    "fitness_rating",
    "uncertainty",
    "source_count",
]


def rows_for_date(conn, rating_date: str | None) -> list[dict]:
    if rating_date:
        date_clause = "s.rating_date = ?"
        params = (rating_date,)
    else:
        date_clause = "s.rating_date = (SELECT MAX(rating_date) FROM team_strength_snapshots)"
        params = ()
    raw_rows = conn.execute(
        f"""
        SELECT
            t.team_id,
            t.name AS team_name,
            s.rating_date,
            s.overall_rating,
            s.attack_rating,
            s.defense_rating,
            s.possession_rating,
            s.transition_rating,
            s.set_piece_rating,
            s.goalkeeper_rating,
            s.depth_rating,
            s.form_rating,
            s.fitness_rating,
            s.uncertainty,
            s.source_count
        FROM team_strength_snapshots s
        JOIN teams t ON t.team_id = s.team_id
        WHERE {date_clause}
        ORDER BY s.overall_rating DESC, s.uncertainty ASC, t.name ASC
        """,
        params,
    ).fetchall()
    rows = []
    for rank, row in enumerate(raw_rows, start=1):
        item = {"rank": rank}
        item.update(dict(row))
        rows.append(item)
    return rows


def write_markdown(rows: list[dict], handle) -> None:
    handle.write("| # | Team | Overall | Atk | Def | Pos | Trans | Set | GK | Form | Unc. |\n")
    handle.write("|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|\n")
    for row in rows:
        handle.write(
            "| {rank} | {team_name} | {overall_rating:.1f} | {attack_rating:.1f} | "
            "{defense_rating:.1f} | {possession_rating:.1f} | {transition_rating:.1f} | "
            "{set_piece_rating:.1f} | {goalkeeper_rating:.1f} | {form_rating:.1f} | "
            "{uncertainty:.1f} |\n".format(**row)
        )


def write_csv(rows: list[dict], handle) -> None:
    writer = csv.DictWriter(handle, fieldnames=EXPORT_COLUMNS)
    writer.writeheader()
    writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, help="SQLite database path.")
    parser.add_argument("--rating-date", help="Specific snapshot date, YYYY-MM-DD. Defaults to latest.")
    parser.add_argument("--format", choices=["csv", "markdown"], default="markdown")
    parser.add_argument("--out", help="Output file. Defaults to stdout.")
    args = parser.parse_args()

    conn = connect(args.db)
    try:
        rows = rows_for_date(conn, args.rating_date)
    finally:
        conn.close()

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", newline="", encoding="utf-8") as handle:
            if args.format == "csv":
                write_csv(rows, handle)
            else:
                write_markdown(rows, handle)
        print(f"Exported {len(rows)} rows to {out_path}")
    else:
        if args.format == "csv":
            write_csv(rows, sys.stdout)
        else:
            write_markdown(rows, sys.stdout)


if __name__ == "__main__":
    main()
