#!/usr/bin/env python3
"""Generate Xiaohongshu-style PNG cards from the World Cup prediction report."""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
REPORT_JSON = ROOT / "data/reports/odds-ev-2026-06-21.json"
OUT_DIR = ROOT / "data/xiaohongshu"
WIDTH = 1080
HEIGHT = 1440

COLORS = {
    "ink": "#171B18",
    "paper": "#F4F1E8",
    "panel": "#FBF8EE",
    "green": "#143D2E",
    "green2": "#245843",
    "red": "#E64632",
    "amber": "#F0B93E",
    "teal": "#48A7A2",
    "muted": "#6A6E64",
    "line": "#D6D0C0",
    "soft_red": "#F6D7D0",
    "soft_green": "#DCEADF",
    "soft_amber": "#F6E6B9",
}

FONT_CANDIDATES = [
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
]


def font_path() -> str:
    for candidate in FONT_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return ""


FONT_PATH = font_path()


def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if FONT_PATH:
        return ImageFont.truetype(FONT_PATH, size=size)
    return ImageFont.load_default()


FONTS = {
    "display": font(76),
    "h1": font(50),
    "h2": font(34),
    "h3": font(28),
    "body": font(25),
    "small": font(21),
    "tiny": font(17),
    "num": font(28),
    "num_big": font(42),
}


def pct(value: float, digits: int = 1) -> str:
    return f"{value * 100:.{digits}f}%"


def money(value: float) -> str:
    if abs(value) >= 1000:
        return f"{value:.0f}"
    if abs(value) >= 100:
        return f"{value:.1f}"
    return f"{value:.2f}"


def text_size(draw: ImageDraw.ImageDraw, text: str, fnt) -> tuple[int, int]:
    box = draw.textbbox((0, 0), text, font=fnt)
    return box[2] - box[0], box[3] - box[1]


def draw_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    fnt,
    fill: str = COLORS["ink"],
    anchor: str | None = None,
) -> None:
    draw.text(xy, text, font=fnt, fill=fill, anchor=anchor)


def draw_right(draw: ImageDraw.ImageDraw, x: int, y: int, text: str, fnt, fill: str = COLORS["ink"]) -> None:
    draw.text((x, y), text, font=fnt, fill=fill, anchor="ra")


def fit_text(draw: ImageDraw.ImageDraw, text: str, max_width: int, size: int, min_size: int = 14):
    while size >= min_size:
        fnt = font(size)
        if text_size(draw, text, fnt)[0] <= max_width:
            return fnt
        size -= 1
    return font(min_size)


def rounded(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], fill: str, outline: str | None = None, width: int = 1, radius: int = 8):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def pill(draw: ImageDraw.ImageDraw, x: int, y: int, text: str, fill: str, fg: str = COLORS["ink"], pad_x: int = 16, pad_y: int = 8, size: int = 21):
    fnt = font(size)
    tw, th = text_size(draw, text, fnt)
    rounded(draw, (x, y, x + tw + pad_x * 2, y + th + pad_y * 2), fill=fill, radius=8)
    draw_text(draw, (x + pad_x, y + pad_y - 1), text, fnt, fg)
    return x + tw + pad_x * 2


def base_card(title: str, kicker: str, issue: str) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (WIDTH, HEIGHT), COLORS["paper"])
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, WIDTH, HEIGHT), fill=COLORS["green"])
    draw.rectangle((0, 0, 128, HEIGHT), fill=COLORS["red"])
    draw.rectangle((128, 0, WIDTH, HEIGHT), fill=COLORS["paper"])

    for i in range(0, HEIGHT, 70):
        draw.line((128, i, WIDTH, i - 220), fill="#E5DECD", width=1)
    draw.rectangle((128, 0, WIDTH, 188), fill=COLORS["green"])
    draw.rectangle((128, 169, WIDTH, 188), fill=COLORS["red"])
    draw_text(draw, (56, 84), issue, FONTS["small"], COLORS["paper"], anchor="mm")
    draw_text(draw, (58, 810), "非投注建议", font(20), COLORS["paper"], anchor="mm")
    draw_text(draw, (170, 38), kicker, FONTS["small"], COLORS["amber"])
    draw_text(draw, (170, 76), title, FONTS["h1"], COLORS["paper"])
    draw_text(draw, (170, 137), "AI赛前结论与方案", FONTS["body"], "#DFE9D8")
    return image, draw


def outcome_label(group: str) -> str:
    return {"home_win": "主胜", "draw": "平局", "away_win": "客胜"}.get(group, group)


def outcome_color(group: str) -> str:
    return {"home_win": COLORS["red"], "draw": COLORS["amber"], "away_win": COLORS["teal"]}.get(group, COLORS["muted"])


def card_wdl(data: dict) -> Path:
    image, draw = base_card("6月21日 4场胜负概率", "世界杯 2026 赛前方案", "01")
    y = 232
    for index, match in enumerate(data["matches"], start=1):
        box = (170, y, 1020, y + 238)
        rounded(draw, box, COLORS["panel"], COLORS["line"], radius=8)
        wdl = match["blended_wdl"]
        favorite_group, favorite_probability = max(wdl.items(), key=lambda item: item[1])
        draw_text(draw, (196, y + 25), f"0{index}", FONTS["h3"], COLORS["red"])
        draw_text(draw, (252, y + 24), match["match"], FONTS["h2"], COLORS["ink"])
        pill(draw, 802, y + 26, outcome_label(favorite_group), outcome_color(favorite_group), COLORS["panel"], size=24, pad_x=18, pad_y=9)

        label = outcome_label(favorite_group)
        color = outcome_color(favorite_group)
        draw_text(draw, (252, y + 102), "倾向", FONTS["small"], COLORS["muted"])
        draw_text(draw, (252, y + 130), label, font(52), color)

        bar_x, bar_y, bar_w, bar_h = 462, y + 104, 478, 18
        cursor = bar_x
        for group in ("home_win", "draw", "away_win"):
            seg_w = max(2, int(bar_w * wdl[group]))
            draw.rectangle((cursor, bar_y, cursor + seg_w, bar_y + bar_h), fill=outcome_color(group))
            cursor += seg_w
        draw.rectangle((bar_x, bar_y, bar_x + bar_w, bar_y + bar_h), outline=COLORS["ink"], width=1)

        labels = [("主胜", wdl["home_win"], COLORS["red"]), ("平局", wdl["draw"], COLORS["amber"]), ("客胜", wdl["away_win"], COLORS["teal"])]
        col_x = [462, 622, 782]
        for (name, value, item_color), x in zip(labels, col_x):
            draw_text(draw, (x, y + 145), name, FONTS["tiny"], COLORS["muted"])
            draw_text(draw, (x, y + 171), pct(value), FONTS["small"], item_color)
        y += 274
    draw_text(draw, (170, 1348), "解读：胜平负概率为赛前模型口径，不代表确定结果。", FONTS["small"], COLORS["muted"])
    out = OUT_DIR / "2026-06-21-01-win-loss.png"
    image.save(out, quality=95)
    return out


def top_scores(match: dict, limit: int = 8) -> list[dict]:
    rows = sorted(match["candidates"], key=lambda row: row["blended_probability"], reverse=True)
    return rows[:limit]


def recommended_scores(match: dict) -> list[str]:
    recommendation = match.get("recommended_score") or {}
    scores = [item.get("score") for item in recommendation.get("scores", []) if item.get("score")]
    if scores:
        return scores
    if recommendation.get("score"):
        return [recommendation["score"]]
    return [top_scores(match)[0]["score"]]


def recommended_scores_text(match: dict) -> str:
    return " / ".join(recommended_scores(match))


def recommended_probability(match: dict) -> float:
    recommendation = match.get("recommended_score") or {}
    scores = [item for item in recommendation.get("scores", []) if item.get("score")]
    if scores:
        return float(scores[0].get("blended_probability") or 0.0)
    if recommendation.get("blended_probability") is not None:
        return float(recommendation["blended_probability"])
    return float(top_scores(match)[0].get("blended_probability") or 0.0)


def card_scores(data: dict) -> Path:
    image, draw = base_card("AI比分预估", "世界杯 2026 比分预测", "02")
    panels = [
        (170, 228, 1020, 462),
        (170, 508, 1020, 742),
        (170, 788, 1020, 1022),
        (170, 1068, 1020, 1302),
    ]
    for match, box in zip(data["matches"], panels):
        x1, y1, x2, y2 = box
        rounded(draw, box, COLORS["panel"], COLORS["line"], radius=8)
        score = recommended_scores_text(match)
        draw_text(draw, (x1 + 26, y1 + 25), match["match"], fit_text(draw, match["match"], x2 - x1 - 360, 34), COLORS["ink"])
        pill(draw, x2 - 182, y1 + 24, "首选比分", COLORS["soft_red"], COLORS["red"], size=20, pad_x=16, pad_y=8)
        draw.line((x1 + 26, y1 + 74, x2 - 26, y1 + 74), fill=COLORS["line"], width=1)

        draw_text(draw, (x1 + 68, y1 + 110), "预估", FONTS["small"], COLORS["muted"])
        draw_text(draw, (x1 + 66, y1 + 140), score, fit_text(draw, score, 240, 78, 42), COLORS["red"])
        draw.line((x1 + 330, y1 + 125, x2 - 60, y1 + 125), fill=COLORS["line"], width=2)
        draw_text(draw, (x1 + 330, y1 + 143), f"概率 {pct(recommended_probability(match))}", FONTS["small"], COLORS["green"])
        draw_text(draw, (x1 + 330, y1 + 174), "说明：在胜负倾向内优先。", FONTS["small"], COLORS["muted"])
    draw_text(draw, (170, 1360), "说明：AI 模型计算结果，不构成建议。", FONTS["small"], COLORS["muted"])
    out = OUT_DIR / "2026-06-21-02-score-pick.png"
    image.save(out, quality=95)
    legacy_out = OUT_DIR / "2026-06-21-02-score-top8.png"
    image.save(legacy_out, quality=95)
    return out


MATCH_SHORT = {
    "荷兰 vs 瑞典": "荷瑞",
    "德国 vs 科特迪瓦": "德科",
    "厄瓜多尔 vs 库拉索": "厄库",
    "突尼斯 vs 日本": "突日",
}


def short_parlay(parlay: dict) -> str:
    parts = []
    for leg in parlay["legs"]:
        parts.append(f"{MATCH_SHORT.get(leg['match'], leg['match'])} {leg['score']}")
    return " / ".join(parts)


def card_parlays(data: dict) -> Path:
    image, draw = base_card("4 串 1 方案", "世界杯 2026 赛前方案", "03")
    groups = [
        ("前 3：稳健方向", "probability_first", COLORS["soft_green"], "更贴近胜负倾向"),
        ("中 3：进取方向", "odds_first", COLORS["soft_amber"], "保留一处冷门思路"),
        ("后 3：高方差方向", "expected_value_first", COLORS["soft_red"], "更激进的比分组合"),
    ]
    y = 226
    seq = 1
    for title, key, fill, note in groups:
        rounded(draw, (170, y, 1020, y + 338), COLORS["panel"], COLORS["line"], radius=8)
        rounded(draw, (190, y + 20, 460, y + 58), fill, radius=8)
        draw_text(draw, (206, y + 27), title, FONTS["small"], COLORS["ink"])
        draw_text(draw, (486, y + 27), note, FONTS["tiny"], COLORS["muted"])
        header_y = y + 82
        draw_text(draw, (196, header_y), "序", FONTS["tiny"], COLORS["muted"])
        draw_text(draw, (250, header_y), "比分串方案", FONTS["tiny"], COLORS["muted"])
        row_y = y + 116
        for parlay in data["parlay_groups"][key]:
            row_fill = "#F7F1DE" if seq % 2 else COLORS["panel"]
            rounded(draw, (190, row_y - 9, 1000, row_y + 58), row_fill, radius=6)
            draw_text(draw, (198, row_y + 6), str(seq), FONTS["body"], COLORS["red"] if key == "expected_value_first" else COLORS["green"])
            text = short_parlay(parlay)
            draw_text(draw, (250, row_y + 5), text, fit_text(draw, text, 705, 25, 17), COLORS["ink"])
            row_y += 72
            seq += 1
        y += 368
    draw_text(draw, (170, 1344), "缩写：荷瑞=荷兰vs瑞典，德科=德国vs科特迪瓦，厄库=厄瓜多尔vs库拉索，突日=突尼斯vs日本。", FONTS["tiny"], COLORS["muted"])
    draw_text(draw, (170, 1375), "说明：精确比分4串1属于高难度方案，仅作赛前模型结论展示。", FONTS["tiny"], COLORS["red"])
    out = OUT_DIR / "2026-06-21-03-parlay-top9.png"
    image.save(out, quality=95)
    return out


def write_copy(data: dict, paths: list[Path]) -> Path:
    matches = []
    for match in data["matches"]:
        group, _prob = max(match["blended_wdl"].items(), key=lambda item: item[1])
        matches.append(f"- {match['match']}：{outcome_label(group)}")

    score_lines = []
    for match in data["matches"]:
        score_lines.append(f"- {match['match']}：{recommended_scores_text(match)}")

    parlay_lines = []
    for title, key in [
        ("稳健方向", "probability_first"),
        ("进取方向", "odds_first"),
        ("高方差方向", "expected_value_first"),
    ]:
        parlay_lines.append(f"{title}：")
        for parlay in data["parlay_groups"][key]:
            parlay_lines.append(f"- {short_parlay(parlay)}")

    body = f"""# 小红书图文文案｜2026-06-21 世界杯预测

## 笔记标题
世界杯 6/21 四场赛前结论：胜负、比分、4串1方案

## 正文
以下只展示赛前模型结论和方案，不展开计算指标。不是投注建议。

胜负倾向：
{chr(10).join(matches)}

比分首选：
{chr(10).join(score_lines)}

4串1分三组：
{chr(10).join(parlay_lines)}

## 话题
#世界杯预测 #足球数据分析 #比分预测 #足球模型 #赛前分析 #小红书体育

## 图片文件
{chr(10).join(f'- {path.name}' for path in paths)}
"""
    out = OUT_DIR / "2026-06-21-xhs-copy.md"
    out.write_text(body, encoding="utf-8")
    return out


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    data = json.loads(REPORT_JSON.read_text(encoding="utf-8"))
    paths = [card_wdl(data), card_scores(data), card_parlays(data)]
    copy_path = write_copy(data, paths)
    print(json.dumps({"images": [str(path) for path in paths], "copy": str(copy_path)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
