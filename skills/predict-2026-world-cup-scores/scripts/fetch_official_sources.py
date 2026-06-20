#!/usr/bin/env python3
"""Archive official FIFA source files/pages for later normalization."""

from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from common import connect, ensure_parent, now_utc, slugify


DEFAULT_SOURCES = [
    (
        "fifa_tournament_hub",
        "FIFA World Cup 2026 tournament hub",
        "https://www.fifa.com/en/tournaments/mens/worldcup/canadamexicousa2026",
        "html",
    ),
    (
        "fifa_teams",
        "FIFA World Cup 2026 teams",
        "https://www.fifa.com/en/tournaments/mens/worldcup/canadamexicousa2026/teams",
        "html",
    ),
    (
        "fifa_scores_fixtures",
        "FIFA World Cup 2026 scores and fixtures",
        "https://www.fifa.com/en/tournaments/mens/worldcup/canadamexicousa2026/scores-fixtures",
        "html",
    ),
    (
        "fifa_squad_lists_pdf",
        "FIFA World Cup 2026 official squad lists PDF",
        "https://fdp.fifa.org/assetspublic/ce281/pdf/SquadLists-English.pdf",
        "pdf",
    ),
    (
        "fifa_squads_confirmed_article",
        "FIFA World Cup 2026 squads confirmed article",
        "https://www.fifa.com/en/tournaments/mens/worldcup/canadamexicousa2026/articles/fifa-world-cup-2026-squads-confirmed",
        "html",
    ),
    (
        "fifa_mens_ranking",
        "FIFA/Coca-Cola Men's World Ranking",
        "https://inside.fifa.com/fifa-world-ranking/men",
        "html",
    ),
]


def filename_for(source_id: str, url: str, kind: str) -> str:
    parsed = urlparse(url)
    name = Path(parsed.path).name
    if "." in name and len(name) < 80:
        suffix = Path(name).suffix
    else:
        suffix = f".{kind}"
    return f"{slugify(source_id)}{suffix}"


def fetch(url: str, timeout: int) -> tuple[bytes, dict[str, str], str]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "CodexWorldCupSkill/0.1 (+local research; contact user)",
            "Accept": "*/*",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read()
        final_url = response.geturl()
        headers = {key: value for key, value in response.headers.items()}
    return data, headers, final_url


def upsert_source(conn: sqlite3.Connection, source_id: str, name: str, url: str, retrieved_at: str, notes: str) -> None:
    conn.execute(
        """
        INSERT INTO sources
            (source_id, name, url, category, publisher, retrieved_at, freshness_days, reliability, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_id) DO UPDATE SET
            name = excluded.name,
            url = excluded.url,
            retrieved_at = excluded.retrieved_at,
            notes = excluded.notes
        """,
        (source_id, name, url, "official_archive", "FIFA", retrieved_at, 1, "official", notes),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default="data/raw", help="Directory to write dated source archives.")
    parser.add_argument("--db", help="Optional database path for updating the sources table.")
    parser.add_argument("--timeout", type=int, default=30, help="HTTP timeout in seconds.")
    parser.add_argument("--source", action="append", help="Limit to one source_id. Can be repeated.")
    args = parser.parse_args()

    date_dir = Path(args.out_dir) / dt.date.today().isoformat()
    date_dir.mkdir(parents=True, exist_ok=True)
    selected = set(args.source or [])
    sources = [source for source in DEFAULT_SOURCES if not selected or source[0] in selected]
    if selected and not sources:
        known = ", ".join(source[0] for source in DEFAULT_SOURCES)
        raise SystemExit(f"No matching source selected. Known sources: {known}")

    conn = connect(args.db) if args.db else None
    try:
        for source_id, name, url, kind in sources:
            try:
                data, headers, final_url = fetch(url, args.timeout)
            except (urllib.error.URLError, TimeoutError) as exc:
                print(f"[failed] {source_id}: {exc}")
                continue

            target = ensure_parent(date_dir / filename_for(source_id, final_url, kind))
            target.write_bytes(data)
            meta_path = target.with_suffix(target.suffix + ".meta.txt")
            meta_lines = [
                f"source_id: {source_id}",
                f"name: {name}",
                f"url: {url}",
                f"final_url: {final_url}",
                f"retrieved_at: {now_utc()}",
                f"bytes: {len(data)}",
            ]
            for header in ["Last-Modified", "ETag", "Content-Type", "Date", "Cache-Control"]:
                if header in headers:
                    meta_lines.append(f"{header}: {headers[header]}")
            meta_path.write_text("\n".join(meta_lines) + "\n", encoding="utf-8")
            if conn is not None:
                upsert_source(
                    conn,
                    source_id,
                    name,
                    final_url,
                    now_utc(),
                    f"Archived to {target}; metadata in {meta_path}.",
                )
            print(f"[ok] {source_id}: {target}")
        if conn is not None:
            conn.commit()
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    main()
