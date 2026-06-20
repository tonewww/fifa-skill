#!/usr/bin/env python3
"""Extract official 2026 World Cup squads from FIFA's SquadLists PDF."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import re
import sys
from pathlib import Path

from common import TABLE_COLUMNS, clamp, coerce_value, connect, now_utc, slugify

try:
    import pdfplumber
except ImportError as exc:  # pragma: no cover - environment guard
    raise SystemExit("pdfplumber is required. Use the bundled Codex Python runtime or install pdfplumber.") from exc


HOST_CODES = {"CAN", "MEX", "USA"}


CONFEDERATIONS = {
    "ALG": "CAF",
    "ARG": "CONMEBOL",
    "AUS": "AFC",
    "AUT": "UEFA",
    "BEL": "UEFA",
    "BIH": "UEFA",
    "BRA": "CONMEBOL",
    "CAN": "CONCACAF",
    "CIV": "CAF",
    "COD": "CAF",
    "COL": "CONMEBOL",
    "CPV": "CAF",
    "CRO": "UEFA",
    "CUW": "CONCACAF",
    "CZE": "UEFA",
    "ECU": "CONMEBOL",
    "EGY": "CAF",
    "ENG": "UEFA",
    "ESP": "UEFA",
    "FRA": "UEFA",
    "GER": "UEFA",
    "GHA": "CAF",
    "HAI": "CONCACAF",
    "IRN": "AFC",
    "IRQ": "AFC",
    "JOR": "AFC",
    "JPN": "AFC",
    "KOR": "AFC",
    "KSA": "AFC",
    "MAR": "CAF",
    "MEX": "CONCACAF",
    "NED": "UEFA",
    "NOR": "UEFA",
    "NZL": "OFC",
    "PAN": "CONCACAF",
    "PAR": "CONMEBOL",
    "POR": "UEFA",
    "QAT": "AFC",
    "RSA": "CAF",
    "SCO": "UEFA",
    "SEN": "CAF",
    "SUI": "UEFA",
    "SWE": "UEFA",
    "TUN": "CAF",
    "TUR": "UEFA",
    "URU": "CONMEBOL",
    "USA": "CONCACAF",
    "UZB": "AFC",
}


def clean_text(value: object) -> str:
    if value is None:
        return ""
    text = str(value).replace("\x00", "fi")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def parse_date(value: str) -> tuple[str | None, int | None]:
    value = clean_text(value)
    if not value:
        return None, None
    try:
        date = dt.datetime.strptime(value, "%d/%m/%Y").date()
    except ValueError:
        return None, None
    reference = dt.date(2026, 6, 11)
    age = reference.year - date.year - ((reference.month, reference.day) < (date.month, date.day))
    return date.isoformat(), age


def parse_int(value: str) -> int | None:
    value = clean_text(value)
    if not value:
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def player_base_ratings(position: str, caps: int | None, goals: int | None, height: int | None, age: int | None) -> dict[str, float]:
    caps = caps or 0
    goals = goals or 0
    height = height or 180
    age = age or 27

    experience = min(caps, 120) / 120.0
    scoring = min(goals, 60) / 60.0
    goals_per_cap = goals / max(caps, 1)
    prime = max(0.0, 1.0 - abs(age - 28) / 14.0)
    aerial = clamp(50 + (height - 180) * 1.2, 35, 75)
    fitness = clamp(62 + prime * 18 - max(age - 33, 0) * 1.5, 35, 82)

    if position == "GK":
        goalkeeping = clamp(51 + experience * 25 + (height - 185) * 0.55 + prime * 8, 42, 88)
        overall = clamp(0.72 * goalkeeping + 0.28 * (52 + experience * 20), 42, 86)
        return {
            "rating_overall": overall,
            "rating_attack": 18.0,
            "rating_defense": clamp(48 + experience * 16, 40, 75),
            "rating_possession": clamp(45 + experience * 16, 38, 75),
            "rating_transition": clamp(44 + experience * 12, 38, 72),
            "rating_set_piece": 28.0,
            "rating_goalkeeping": goalkeeping,
            "rating_fitness": fitness,
        }

    if position == "DF":
        defense = clamp(50 + experience * 25 + (height - 180) * 0.45 + prime * 5, 42, 88)
        attack = clamp(39 + experience * 8 + min(goals, 12) * 1.0, 32, 70)
        possession = clamp(45 + experience * 18 + prime * 6, 38, 78)
        transition = clamp(45 + experience * 15 + prime * 7, 38, 78)
        set_piece = clamp(44 + aerial * 0.28 + min(goals, 15) * 0.8, 35, 80)
    elif position == "MF":
        possession = clamp(50 + experience * 24 + prime * 7, 42, 88)
        defense = clamp(46 + experience * 18 + prime * 5, 38, 82)
        attack = clamp(44 + experience * 15 + scoring * 18 + goals_per_cap * 18, 36, 84)
        transition = clamp(48 + experience * 18 + prime * 7, 40, 84)
        set_piece = clamp(45 + experience * 12 + scoring * 10, 35, 78)
    else:
        attack = clamp(49 + experience * 16 + scoring * 24 + goals_per_cap * 24 + prime * 5, 40, 90)
        defense = clamp(35 + experience * 8 + prime * 4, 30, 65)
        possession = clamp(43 + experience * 14 + prime * 6, 35, 82)
        transition = clamp(50 + experience * 14 + scoring * 12 + prime * 7, 40, 88)
        set_piece = clamp(43 + scoring * 18 + aerial * 0.22, 35, 82)

    overall = clamp(
        {
            "DF": 0.45 * defense + 0.20 * possession + 0.15 * transition + 0.10 * set_piece + 0.10 * attack,
            "MF": 0.35 * possession + 0.22 * transition + 0.18 * attack + 0.15 * defense + 0.10 * set_piece,
            "FW": 0.45 * attack + 0.22 * transition + 0.15 * possession + 0.10 * set_piece + 0.08 * defense,
        }[position]
    )
    return {
        "rating_overall": overall,
        "rating_attack": attack,
        "rating_defense": defense,
        "rating_possession": possession,
        "rating_transition": transition,
        "rating_set_piece": set_piece,
        "rating_goalkeeping": None,
        "rating_fitness": fitness,
    }


def expected_minutes(position: str, shirt_number: int | None, caps: int | None, goals: int | None) -> float:
    shirt_number = shirt_number or 99
    caps = caps or 0
    goals = goals or 0
    by_shirt = 44.0
    if shirt_number <= 11:
        by_shirt = 70.0
    elif shirt_number <= 18:
        by_shirt = 46.0
    elif shirt_number <= 23:
        by_shirt = 30.0
    cap_bonus = min(caps, 100) * 0.22
    goal_bonus = min(goals, 40) * (0.18 if position != "GK" else 0.0)
    return round(clamp(by_shirt + cap_bonus + goal_bonus, 8.0, 92.0), 2)


def parse_pdf(pdf_path: Path, source_id: str, verified_at: str) -> tuple[list[dict], list[dict], list[dict]]:
    teams: list[dict] = []
    players: list[dict] = []
    styles: list[dict] = []

    with pdfplumber.open(pdf_path) as pdf:
        for page_index, page in enumerate(pdf.pages, start=1):
            text = page.extract_text(x_tolerance=1, y_tolerance=3) or ""
            match = re.search(r"^(.+?) \(([A-Z]{3})\)$", text, re.M)
            if not match:
                raise SystemExit(f"Could not parse team header on page {page_index}")
            team_name = clean_text(match.group(1))
            team_id = clean_text(match.group(2)).upper()
            tables = page.extract_tables()
            if not tables:
                raise SystemExit(f"Could not parse squad table on page {page_index} ({team_name})")
            table = tables[0]
            coach = ""
            team_players: list[dict] = []
            for raw_row in table[1:]:
                cells = [clean_text(cell) for cell in raw_row if clean_text(cell)]
                if not cells:
                    continue
                if cells[0] == "Head coach":
                    coach = cells[1] if len(cells) > 1 else ""
                    continue
                if not cells[0].isdigit():
                    continue
                if len(cells) < 11:
                    raise SystemExit(f"Unexpected player row on page {page_index}: {cells}")

                shirt_number = parse_int(cells[0])
                position = cells[1]
                player_name = cells[2]
                first_names = cells[3]
                last_names = cells[4]
                name_on_shirt = cells[5]
                birth_date, age = parse_date(cells[6])
                club = cells[7]
                height_cm = parse_int(cells[8])
                caps = parse_int(cells[9])
                goals = parse_int(cells[10])
                ratings = player_base_ratings(position, caps, goals, height_cm, age)
                minutes_expected = expected_minutes(position, shirt_number, caps, goals)
                player_id = f"{team_id}-{slugify(player_name)}"
                player = {
                    "player_id": player_id,
                    "team_id": team_id,
                    "fifa_code": team_id,
                    "name": player_name,
                    "display_name": name_on_shirt or player_name,
                    "shirt_number": shirt_number,
                    "position": position,
                    "club": club,
                    "league": "",
                    "birth_date": birth_date,
                    "age": age,
                    "caps": caps,
                    "goals": goals,
                    "dominant_foot": "",
                    "height_cm": height_cm,
                    "market_value_eur": "",
                    "rating_overall": round(ratings["rating_overall"], 2),
                    "rating_attack": round(ratings["rating_attack"], 2),
                    "rating_defense": round(ratings["rating_defense"], 2),
                    "rating_possession": round(ratings["rating_possession"], 2),
                    "rating_transition": round(ratings["rating_transition"], 2),
                    "rating_set_piece": round(ratings["rating_set_piece"], 2),
                    "rating_goalkeeping": "" if ratings["rating_goalkeeping"] is None else round(ratings["rating_goalkeeping"], 2),
                    "rating_fitness": round(ratings["rating_fitness"], 2),
                    "status": "available",
                    "minutes_expected": minutes_expected,
                    "last_verified_at": verified_at,
                    "source_id": source_id,
                    "notes": f"Official FIFA SquadLists PDF page {page_index}; baseline ratings from caps/goals/age/height/position.",
                    "_first_names": first_names,
                    "_last_names": last_names,
                }
                team_players.append(player)

            if len(team_players) != 26:
                raise SystemExit(f"Expected 26 players for {team_name}, found {len(team_players)}")

            teams.append(
                {
                    "team_id": team_id,
                    "fifa_code": team_id,
                    "name": team_name,
                    "short_name": team_name,
                    "confederation": CONFEDERATIONS.get(team_id, ""),
                    "group_name": "",
                    "is_host": 1 if team_id in HOST_CODES else 0,
                    "qualification_method": "official-squad-list",
                    "seed_pot": "",
                    "coach": coach,
                    "fifa_rank": "",
                    "fifa_points": "",
                    "squad_status": "final",
                    "last_verified_at": verified_at,
                    "source_id": source_id,
                    "notes": f"Parsed from official FIFA SquadLists PDF page {page_index}.",
                }
            )
            for player in team_players:
                player.pop("_first_names", None)
                player.pop("_last_names", None)
            players.extend(team_players)
            styles.append(style_from_players(team_id, team_name, team_players, verified_at, source_id))

    return teams, players, styles


def avg(items: list[float], default: float = 50.0) -> float:
    return sum(items) / len(items) if items else default


def style_from_players(team_id: str, team_name: str, team_players: list[dict], verified_at: str, source_id: str) -> dict:
    by_pos = {
        "GK": [p for p in team_players if p["position"] == "GK"],
        "DF": [p for p in team_players if p["position"] == "DF"],
        "MF": [p for p in team_players if p["position"] == "MF"],
        "FW": [p for p in team_players if p["position"] == "FW"],
    }
    avg_caps = avg([p["caps"] or 0 for p in team_players], 20.0)
    avg_height = avg([p["height_cm"] or 180 for p in team_players], 180.0)
    fw_attack = avg([float(p["rating_attack"]) for p in by_pos["FW"]], 50.0)
    mf_possession = avg([float(p["rating_possession"]) for p in by_pos["MF"]], 50.0)
    df_defense = avg([float(p["rating_defense"]) for p in by_pos["DF"]], 50.0)
    gk_rating = avg([float(p["rating_goalkeeping"]) for p in by_pos["GK"] if p["rating_goalkeeping"] != ""], 50.0)
    aerial = clamp(48 + (avg_height - 180) * 1.6, 35, 80)
    experience = clamp(45 + min(avg_caps, 80) * 0.45, 40, 82)

    return {
        "profile_id": f"style-{team_id.lower()}-{verified_at[:10]}",
        "team_id": team_id,
        "profile_date": verified_at[:10],
        "formation_primary": "",
        "tempo": round(clamp(50 + fw_attack * 0.15 + mf_possession * 0.10 - 12), 2),
        "press_intensity": round(clamp(48 + avg([float(p["rating_fitness"]) for p in team_players], 60.0) * 0.18), 2),
        "defensive_line": round(clamp(48 + df_defense * 0.12 + gk_rating * 0.08 - 8), 2),
        "buildup_quality": round(clamp(0.55 * mf_possession + 0.25 * df_defense + 0.20 * gk_rating), 2),
        "transition_attack": round(clamp(0.55 * fw_attack + 0.25 * mf_possession + 0.20 * experience), 2),
        "transition_defense": round(clamp(0.55 * df_defense + 0.25 * mf_possession + 0.20 * experience), 2),
        "wing_play": round(clamp(0.70 * fw_attack + 0.30 * mf_possession), 2),
        "central_progression": round(clamp(0.70 * mf_possession + 0.30 * fw_attack), 2),
        "set_piece_attack": round(clamp(0.45 * avg([float(p["rating_set_piece"]) for p in team_players], 50.0) + 0.55 * aerial), 2),
        "set_piece_defense": round(clamp(0.55 * df_defense + 0.45 * aerial), 2),
        "aerial_strength": round(aerial, 2),
        "low_block_attack": round(clamp(0.60 * fw_attack + 0.40 * mf_possession), 2),
        "low_block_defense": round(clamp(0.72 * df_defense + 0.28 * gk_rating), 2),
        "keeper_sweeper": round(clamp(0.80 * gk_rating + 0.20 * df_defense), 2),
        "keeper_shot_stopping": round(gk_rating, 2),
        "injury_load": 0,
        "cohesion": round(experience, 2),
        "travel_fatigue": 0,
        "source_id": source_id,
        "notes": f"Baseline style profile for {team_name} from official squad composition; replace with tactical/statistical data when available.",
    }


def write_csv(path: Path, table: str, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = TABLE_COLUMNS[table]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def import_rows(db_path: Path, table: str, rows: list[dict], replace: bool) -> None:
    columns = TABLE_COLUMNS[table]
    conn = connect(db_path)
    try:
        if replace:
            conn.execute(f"DELETE FROM {table}")
        placeholders = ", ".join(["?"] * len(columns))
        update_sql = ", ".join([f"{column} = excluded.{column}" for column in columns[1:]])
        sql = f"""
            INSERT INTO {table} ({", ".join(columns)})
            VALUES ({placeholders})
            ON CONFLICT({columns[0]}) DO UPDATE SET {update_sql}
        """
        conn.executemany(sql, [[coerce_value(column, row.get(column, "")) for column in columns] for row in rows])
        conn.commit()
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True, help="FIFA SquadLists PDF path.")
    parser.add_argument("--db", help="Optional SQLite database to update.")
    parser.add_argument("--out-dir", default="data/normalized", help="Directory for generated CSV files.")
    parser.add_argument("--source-id", default="fifa_squad_lists_pdf")
    parser.add_argument("--replace", action="store_true", help="Replace existing teams/players/style rows before import.")
    args = parser.parse_args()

    verified_at = now_utc()
    teams, players, styles = parse_pdf(Path(args.pdf), args.source_id, verified_at)
    out_dir = Path(args.out_dir)
    write_csv(out_dir / "teams.csv", "teams", teams)
    write_csv(out_dir / "players.csv", "players", players)
    write_csv(out_dir / "team_style_profiles.csv", "team_style_profiles", styles)

    if args.db:
        import_rows(Path(args.db), "teams", teams, args.replace)
        import_rows(Path(args.db), "players", players, args.replace)
        import_rows(Path(args.db), "team_style_profiles", styles, args.replace)

    print(f"Parsed {len(teams)} teams and {len(players)} players from {args.pdf}")
    print(f"Wrote normalized CSV files to {out_dir}")
    if args.db:
        print(f"Imported teams, players, and baseline style profiles into {args.db}")


if __name__ == "__main__":
    main()
