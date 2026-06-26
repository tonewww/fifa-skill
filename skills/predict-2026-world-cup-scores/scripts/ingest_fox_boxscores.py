#!/usr/bin/env python3
"""Ingest FOX Sports World Cup score pages and boxscores.

FOX Nuxt payloads store many values as array-index references. Always resolve
indices before reading team names or scores.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import unicodedata
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from common import connect, ensure_parent, now_utc, slugify


FOX_BASE = "https://www.foxsports.com"
SOURCE_PREFIX = "fox_worldcup_boxscores"

ALIASES = {
    "bosnia and herzegovina": "BIH",
    "bosnia herzegovina": "BIH",
    "curacao": "CUW",
    "czech republic": "CZE",
    "czechia": "CZE",
    "dr congo": "COD",
    "congo dr": "COD",
    "democratic republic of congo": "COD",
    "ivory coast": "CIV",
    "cote divoire": "CIV",
    "cote d ivoire": "CIV",
    "korea republic": "KOR",
    "south korea": "KOR",
    "turkiye": "TUR",
    "turkey": "TUR",
    "united states": "USA",
    "usa": "USA",
}


def normalize_name(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(char for char in text if not unicodedata.combining(char))
    keep = [char.lower() if char.isalnum() else " " for char in text]
    return " ".join("".join(keep).split())


def team_name_map(conn) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for row in conn.execute("SELECT team_id, fifa_code, name, short_name FROM teams"):
        for key in (row["team_id"], row["fifa_code"], row["name"], row["short_name"]):
            normalized = normalize_name(key)
            if normalized:
                mapping[normalized] = row["team_id"]
    for alias, team_id in ALIASES.items():
        if conn.execute("SELECT 1 FROM teams WHERE team_id = ?", (team_id,)).fetchone():
            mapping[normalize_name(alias)] = team_id
    return mapping


def fetch_url(url: str, timeout: int = 35) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        data = response.read()
    return data.decode("utf-8", errors="replace")


def read_source(value: str) -> tuple[str, str]:
    path = Path(value)
    if path.exists():
        return path.read_text(encoding="utf-8", errors="replace"), path.resolve().as_uri()
    return fetch_url(value), value


def archive_text(text: str, out_dir: Path, filename: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / filename
    path.write_text(text, encoding="utf-8")
    return path


def resolve(payload: list, value):
    if isinstance(value, int) and 0 <= value < len(payload):
        return payload[value]
    return value


def nuxt_payload(text: str) -> list | None:
    match = re.search(
        r'<script type="application/json" data-nuxt-data="nuxt-app" data-ssr="true" id="__NUXT_DATA__">(.*?)</script>',
        text,
        re.S,
    )
    if not match:
        return None
    return json.loads(html.unescape(match.group(1)))


def parse_numeric(value: str | None) -> float | None:
    if value is None:
        return None
    cleaned = re.sub(r"[^0-9.\-]", "", str(value))
    if cleaned in {"", "-", "."}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_int(value: str | None) -> int | None:
    parsed = parse_numeric(value)
    return None if parsed is None else int(round(parsed))


def parse_json_ld_event(text: str) -> dict:
    for match in re.finditer(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', text, re.S):
        try:
            data = json.loads(html.unescape(match.group(1)))
        except json.JSONDecodeError:
            continue
        event = data.get("broadcastOfEvent") if isinstance(data, dict) else None
        if isinstance(event, dict) and event.get("@type") == "SportsEvent":
            return event
    return {}


def parse_meta_title(text: str) -> str | None:
    match = re.search(r'<meta name="og:title" content="([^"]+)"', text)
    return html.unescape(match.group(1)) if match else None


def parse_canonical_url(text: str) -> str | None:
    match = re.search(r'<link rel="canonical" href="([^"]+)"', text)
    if match:
        return html.unescape(match.group(1))
    match = re.search(r'<meta name="og:url" content="([^"]+)"', text)
    return html.unescape(match.group(1)) if match else None


def best_event_url(text: str, event_id: str | None, teams: list[dict], fallback: str) -> str:
    if not event_id:
        return fallback
    hrefs = re.findall(r'href="([^"]*game-boxscore-' + re.escape(event_id) + r'[^"]*)"', text)
    if not hrefs:
        return fallback
    home_slug = slugify(teams[0]["name"])
    away_slug = slugify(teams[1]["name"])
    best_url = fallback
    best_score = -1
    for href in hrefs:
        candidate = urljoin(FOX_BASE, html.unescape(href))
        score = 0
        if home_slug in candidate:
            score += 2
        if away_slug in candidate:
            score += 2
        if "?tab=boxscore" in candidate:
            score += 1
        if score > best_score:
            best_url = candidate
            best_score = score
    return best_url


def parse_stats(text: str) -> dict:
    start = text.find("MATCH STATS")
    if start < 0:
        return {}
    end = text.find("LINEUP", start)
    if end < 0:
        end = text.find("__NUXT_DATA__", start)
    block = text[start:end if end > start else len(text)]
    rows = re.findall(
        r'<div class="matchup-comparison-data[^>]* left">\s*([^<]+?)\s*(?:<!---->)?</div>\s*'
        r'<span class="matchup-comparison-text[^>]* center">([^<]+)</span>\s*'
        r'<div class="matchup-comparison-data[^>]* right">\s*([^<]+?)\s*(?:<!---->)?</div>',
        block,
        re.S,
    )
    stats = {}
    for left, label, right in rows:
        key = html.unescape(label).strip().upper()
        stats[key] = (html.unescape(left).strip(), html.unescape(right).strip())
    return stats


def parse_boxscore(text: str, source_url: str, team_map: dict[str, str]) -> dict | None:
    payload = nuxt_payload(text)
    if not payload:
        return None
    canonical_url = parse_canonical_url(text) or source_url
    event_id_match = re.search(r"game-boxscore-(\d+)", canonical_url)
    event_id = event_id_match.group(1) if event_id_match else None
    teams = []
    for item in payload:
        if isinstance(item, dict) and "score" in item and "longName" in item:
            name = str(resolve(payload, item.get("longName")) or "").strip()
            score = parse_int(str(resolve(payload, item.get("score"))))
            if name and score is not None:
                teams.append({"name": name, "score": score})
    if len(teams) < 2:
        return None
    teams = teams[:2]
    event = parse_json_ld_event(text)
    location = event.get("location") if isinstance(event.get("location"), dict) else {}
    address = location.get("address") if isinstance(location.get("address"), dict) else {}
    start_date = str(event.get("startDate") or "")
    match_date = start_date[:10] if re.match(r"\d{4}-\d{2}-\d{2}", start_date) else None
    if not match_date:
        title = parse_meta_title(text) or ""
        date_match = re.search(r"(June|Jun)\s+(\d{1,2}),\s+2026", title)
        if date_match:
            match_date = f"2026-06-{int(date_match.group(2)):02d}"
    home_id = team_map.get(normalize_name(teams[0]["name"]))
    away_id = team_map.get(normalize_name(teams[1]["name"]))
    if not home_id or not away_id:
        raise ValueError(f"Could not map FOX teams: {teams[0]['name']} vs {teams[1]['name']}")
    canonical_url = best_event_url(text, event_id, teams, canonical_url)
    stats = parse_stats(text)
    xg = stats.get("EXPECTED GOALS (XG)", (None, None))
    shots = stats.get("TOTAL SHOTS", (None, None))
    shots_on_target = stats.get("SHOTS ON GOAL", (None, None))
    possession = stats.get("POSSESSION (%)", (None, None))
    return {
        "match_id": f"fox-{match_date or 'unknown'}-{home_id.lower()}-{away_id.lower()}",
        "match_date": match_date,
        "stage": None,
        "group_name": None,
        "venue": location.get("name"),
        "city": address.get("addressLocality"),
        "country": None,
        "home_name": teams[0]["name"],
        "away_name": teams[1]["name"],
        "home_id": home_id,
        "away_id": away_id,
        "score_a": teams[0]["score"],
        "score_b": teams[1]["score"],
        "xg_a": parse_numeric(xg[0]),
        "xg_b": parse_numeric(xg[1]),
        "shots_a": parse_int(shots[0]),
        "shots_b": parse_int(shots[1]),
        "shots_on_target_a": parse_int(shots_on_target[0]),
        "shots_on_target_b": parse_int(shots_on_target[1]),
        "possession_a": parse_numeric(possession[0]),
        "possession_b": parse_numeric(possession[1]),
        "source_url": canonical_url,
        "event_id": event_id,
        "stats_available": bool(stats),
    }


def clean_html_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", "", value)
    return html.unescape(value).strip()


def parse_score_page(text: str, source_url: str, team_map: dict[str, str]) -> list[dict]:
    rows = []
    anchors = re.findall(
        r'<a href="([^"]*game-boxscore-\d+[^"]*)" class="score-chip final">(.*?)</a>',
        text,
        re.S,
    )
    for href, block in anchors:
        group_match = re.search(r"<span>(GROUP [A-Z])</span>", block)
        teams = []
        for team_block in re.findall(r'<div class="[^"]*score-team-row[^"]*">(.*?)</div></div>', block, re.S):
            name_match = re.search(r'title="([^"]+)">', team_block)
            score_match = re.search(r'<div class="score-team-score"><span class="scores-text">.*?([0-9]+)\s*(?:<!--\]-->)?</span>', team_block, re.S)
            if not name_match or not score_match:
                continue
            name = html.unescape(name_match.group(1))
            score = parse_int(score_match.group(1))
            if score is not None:
                teams.append({"name": name, "score": score})
        if len(teams) < 2:
            continue
        home_id = team_map.get(normalize_name(teams[0]["name"]))
        away_id = team_map.get(normalize_name(teams[1]["name"]))
        if not home_id or not away_id:
            continue
        event_url = urljoin(FOX_BASE, html.unescape(href))
        event_id_match = re.search(r"game-boxscore-(\d+)", event_url)
        event_id = event_id_match.group(1) if event_id_match else slugify(event_url)
        date_match = re.search(r"jun-(\d{1,2})-2026", event_url)
        match_date = f"2026-06-{int(date_match.group(1)):02d}" if date_match else None
        rows.append(
            {
                "match_id": f"fox-{match_date or 'unknown'}-{home_id.lower()}-{away_id.lower()}",
                "match_date": match_date,
                "stage": "Group Stage",
                "group_name": clean_html_text(group_match.group(1)) if group_match else None,
                "venue": None,
                "city": None,
                "country": None,
                "home_name": teams[0]["name"],
                "away_name": teams[1]["name"],
                "home_id": home_id,
                "away_id": away_id,
                "score_a": teams[0]["score"],
                "score_b": teams[1]["score"],
                "xg_a": None,
                "xg_b": None,
                "shots_a": None,
                "shots_b": None,
                "shots_on_target_a": None,
                "shots_on_target_b": None,
                "possession_a": None,
                "possession_b": None,
                "source_url": event_url,
                "event_id": event_id,
                "stats_available": False,
            }
        )
    return rows


def upsert_source(conn, source_id: str, name: str, url: str, notes: str) -> None:
    conn.execute(
        """
        INSERT INTO sources (
            source_id, name, url, category, publisher, retrieved_at,
            freshness_days, license_note, reliability, notes
        )
        VALUES (?, ?, ?, 'match_results', 'FOX Sports', ?, 2, ?, 'media-boxscore', ?)
        ON CONFLICT(source_id) DO UPDATE SET
            url = excluded.url,
            retrieved_at = excluded.retrieved_at,
            notes = excluded.notes
        """,
        (
            source_id,
            name,
            url,
            now_utc(),
            "Public scoreboard and boxscore pages; archive locally and verify against FIFA match reports when available.",
            notes,
        ),
    )


def upsert_match(conn, row: dict, source_id: str) -> None:
    notes = (
        f"FOX Sports URL: {row['source_url']}; "
        "scores parsed from boxscore Nuxt payload with index resolution"
        if row.get("stats_available")
        else f"FOX Sports URL: {row['source_url']}; score parsed from final score chip; stats unavailable."
    )
    conn.execute(
        """
        INSERT INTO fixtures (
            match_id, competition, stage, group_name, match_date, venue, city, country,
            team_a_id, team_b_id, score_a, score_b, status, source_id, notes
        )
        VALUES (?, 'FIFA World Cup 2026', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'final', ?, ?)
        ON CONFLICT(match_id) DO UPDATE SET
            competition = excluded.competition,
            stage = COALESCE(excluded.stage, fixtures.stage),
            group_name = COALESCE(excluded.group_name, fixtures.group_name),
            match_date = COALESCE(excluded.match_date, fixtures.match_date),
            venue = COALESCE(excluded.venue, fixtures.venue),
            city = COALESCE(excluded.city, fixtures.city),
            country = COALESCE(excluded.country, fixtures.country),
            team_a_id = excluded.team_a_id,
            team_b_id = excluded.team_b_id,
            score_a = excluded.score_a,
            score_b = excluded.score_b,
            status = excluded.status,
            source_id = excluded.source_id,
            notes = excluded.notes
        """,
        (
            row["match_id"],
            row.get("stage") or "Group Stage",
            row.get("group_name"),
            row.get("match_date"),
            row.get("venue"),
            row.get("city"),
            row.get("country"),
            row["home_id"],
            row["away_id"],
            row["score_a"],
            row["score_b"],
            source_id,
            notes,
        ),
    )
    result_rows = [
        (
            f"{row['match_id']}-{row['home_id'].lower()}",
            row["home_id"],
            row["away_id"],
            "neutral",
            row["score_a"],
            row["score_b"],
            row.get("xg_a"),
            row.get("xg_b"),
            row.get("shots_a"),
            row.get("shots_on_target_a"),
            row.get("possession_a"),
        ),
        (
            f"{row['match_id']}-{row['away_id'].lower()}",
            row["away_id"],
            row["home_id"],
            "neutral",
            row["score_b"],
            row["score_a"],
            row.get("xg_b"),
            row.get("xg_a"),
            row.get("shots_b"),
            row.get("shots_on_target_b"),
            row.get("possession_b"),
        ),
    ]
    for result in result_rows:
        conn.execute(
            """
            INSERT INTO team_results (
                result_id, match_date, team_id, opponent_team_id, venue_type,
                competition, is_neutral, goals_for, goals_against, xg_for,
                xg_against, shots, shots_on_target, possession, source_id, notes
            )
            VALUES (?, ?, ?, ?, ?, 'FIFA World Cup 2026', 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(result_id) DO UPDATE SET
                match_date = excluded.match_date,
                opponent_team_id = excluded.opponent_team_id,
                venue_type = excluded.venue_type,
                competition = excluded.competition,
                is_neutral = excluded.is_neutral,
                goals_for = excluded.goals_for,
                goals_against = excluded.goals_against,
                xg_for = excluded.xg_for,
                xg_against = excluded.xg_against,
                shots = excluded.shots,
                shots_on_target = excluded.shots_on_target,
                possession = excluded.possession,
                source_id = excluded.source_id,
                notes = excluded.notes
            """,
            (
                result[0],
                row.get("match_date"),
                result[1],
                result[2],
                result[3],
                result[4],
                result[5],
                result[6],
                result[7],
                result[8],
                result[9],
                result[10],
                source_id,
                notes,
            ),
        )


def merge_rows(existing: dict[str, dict], row: dict) -> None:
    merge_key = row.get("event_id") or row["match_id"]
    current = existing.get(merge_key)
    if not current:
        existing[merge_key] = row
        return
    if row.get("stats_available") and not current.get("stats_available"):
        row = {**current, **{key: value for key, value in row.items() if value is not None}}
        row["stats_available"] = True
        existing[merge_key] = row
        return
    for key, value in row.items():
        if current.get(key) is None and value is not None:
            current[key] = value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/worldcup2026.sqlite")
    parser.add_argument("--date", required=True, help="Source/score date, e.g. 2026-06-24.")
    parser.add_argument("--score-url", action="append", default=[], help="FOX scores page URL or local HTML.")
    parser.add_argument("--boxscore-url", action="append", default=[], help="FOX boxscore URL or local HTML.")
    parser.add_argument("--out-dir", default="data/raw", help="Raw archive root.")
    args = parser.parse_args()

    conn = connect(args.db)
    mapping = team_name_map(conn)
    archive_dir = Path(args.out_dir) / args.date
    source_id = f"{SOURCE_PREFIX}_{args.date.replace('-', '_')}"
    upsert_source(
        conn,
        source_id,
        f"FOX Sports 2026 World Cup boxscores, {args.date}",
        args.score_url[0] if args.score_url else (args.boxscore_url[0] if args.boxscore_url else ""),
        "Used for final scores and available boxscore stats in latest-results calibration.",
    )

    by_id: dict[str, dict] = {}
    archived = []
    for index, source in enumerate(args.score_url, start=1):
        text, canonical = read_source(source)
        filename = f"fox-scores-{args.date}-{index}.html"
        path = archive_text(text, archive_dir, filename)
        archived.append(str(path))
        for row in parse_score_page(text, canonical, mapping):
            merge_rows(by_id, row)

    for index, source in enumerate(args.boxscore_url, start=1):
        text, canonical = read_source(source)
        parsed = parse_boxscore(text, canonical, mapping)
        if not parsed:
            continue
        filename = f"fox-{slugify(parsed['home_name'])}-{slugify(parsed['away_name'])}-{args.date}-{index}.html"
        path = archive_text(text, archive_dir, filename)
        archived.append(str(path))
        merge_rows(by_id, parsed)

    for row in by_id.values():
        upsert_match(conn, row, source_id)
    conn.commit()
    conn.close()
    print(
        json.dumps(
            {
                "source_id": source_id,
                "matches": sorted(by_id.values(), key=lambda row: (row.get("match_date") or "", row["match_id"])),
                "archived": archived,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
