#!/usr/bin/env python3
"""Generate Xiaohongshu-style PNG cards from the World Cup prediction report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT_JSON = ROOT / "data/reports/odds-ev-2026-06-21.json"
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


def wrap_text(draw: ImageDraw.ImageDraw, text: str, max_width: int, fnt, max_lines: int = 2) -> list[str]:
    if not text:
        return []
    lines: list[str] = []
    current = ""
    for char in text:
        candidate = current + char
        if current and text_size(draw, candidate, fnt)[0] > max_width:
            lines.append(current)
            current = char
            if len(lines) == max_lines:
                break
        else:
            current = candidate
    if len(lines) < max_lines and current:
        lines.append(current)
    if len(lines) == max_lines:
        consumed = "".join(lines)
        if len(consumed) < len(text):
            while lines[-1] and text_size(draw, f"{lines[-1]}…", fnt)[0] > max_width:
                lines[-1] = lines[-1][:-1]
            lines[-1] = f"{lines[-1]}…"
    return lines


def rounded(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], fill: str, outline: str | None = None, width: int = 1, radius: int = 8):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def pill(draw: ImageDraw.ImageDraw, x: int, y: int, text: str, fill: str, fg: str = COLORS["ink"], pad_x: int = 16, pad_y: int = 8, size: int = 21):
    fnt = font(size)
    tw, th = text_size(draw, text, fnt)
    rounded(draw, (x, y, x + tw + pad_x * 2, y + th + pad_y * 2), fill=fill, radius=8)
    draw_text(draw, (x + pad_x, y + pad_y - 1), text, fnt, fg)
    return x + tw + pad_x * 2


def base_card(title: str, kicker: str, issue: str, height: int = 1440) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (WIDTH, height), COLORS["paper"])
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, WIDTH, height), fill=COLORS["green"])
    draw.rectangle((0, 0, 128, height), fill=COLORS["red"])
    draw.rectangle((128, 0, WIDTH, height), fill=COLORS["paper"])

    for i in range(0, height, 70):
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


def relationship_context(match: dict) -> dict:
    relationship = match.get("relationship")
    if relationship:
        return relationship
    wdl = match["blended_wdl"]
    favorite_group, favorite_probability = max(wdl.items(), key=lambda item: item[1])
    draw_probability = float(wdl.get("draw") or 0.0)
    if favorite_group == "draw":
        return {
            "label": "平局优先",
            "primary_group": "draw",
            "probability_text": pct(draw_probability),
            "note": "平局最高",
        }
    if draw_probability >= 0.27 and favorite_probability - draw_probability <= 0.12:
        return {
            "label": f"{outcome_label(favorite_group)}防平",
            "primary_group": favorite_group,
            "secondary_group": "draw",
            "probability_text": f"{pct(favorite_probability)} / 平{pct(draw_probability)}",
            "note": "防平",
        }
    return {
        "label": outcome_label(favorite_group),
        "primary_group": favorite_group,
        "probability_text": pct(favorite_probability),
        "note": "",
    }


def date_label(date_slug: str) -> str:
    parts = date_slug.split("-")
    if len(parts) >= 3:
        return f"{int(parts[1])}月{int(parts[2])}日"
    return date_slug


def competition_label(data: dict) -> str:
    return str(data.get("competition_label") or "世界杯 2026")


def card_wdl(data: dict, date_slug: str, title_date: str) -> Path:
    num_matches = len(data["matches"])
    height = max(1440, 232 + num_matches * 274 + 100)
    competition = competition_label(data)
    image, draw = base_card(f"{title_date} {num_matches}场胜负概率", f"{competition} 赛前方案", "01", height=height)
    y = 232
    for index, match in enumerate(data["matches"], start=1):
        box = (170, y, 1020, y + 238)
        rounded(draw, box, COLORS["panel"], COLORS["line"], radius=8)
        wdl = match["blended_wdl"]
        relationship = relationship_context(match)
        primary_group = relationship.get("primary_group") or max(wdl.items(), key=lambda item: item[1])[0]
        draw_text(draw, (196, y + 25), f"0{index}", FONTS["h3"], COLORS["red"])
        draw_text(draw, (252, y + 24), match["match"], FONTS["h2"], COLORS["ink"])
        pill(draw, 782, y + 26, relationship["label"], outcome_color(primary_group), COLORS["panel"], size=22, pad_x=16, pad_y=9)

        label = relationship["label"]
        color = outcome_color(primary_group)
        draw_text(draw, (252, y + 102), "关系", FONTS["small"], COLORS["muted"])
        draw_text(draw, (252, y + 130), label, fit_text(draw, label, 185, 48, 30), color)
        if relationship.get("secondary_group") == "draw":
            draw_text(draw, (252, y + 186), "防平", FONTS["tiny"], COLORS["amber"])

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
    draw_text(draw, (170, height - 92), "解读：胜平负概率为赛前模型口径，不代表确定结果。", FONTS["small"], COLORS["muted"])
    out = OUT_DIR / f"{date_slug}-01-win-loss.png"
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


def score_pick_rows(match: dict, limit: int = 3) -> list[dict]:
    return top_scores(match, limit=limit)


def score_pick_text(match: dict) -> str:
    return " / ".join(f"{row['score']}({pct(float(row.get('blended_probability') or 0.0))})" for row in score_pick_rows(match))


def score_top3_explanation(match: dict) -> str:
    rows = score_pick_rows(match)
    groups = {row.get("group") for row in rows}
    rel = relationship_context(match)
    label = rel.get("label", "")
    if len(groups) == 1:
        return f"说明：Top3集中在{outcome_label(next(iter(groups)))}方向，和{label}口径接近。"
    if "draw" in groups:
        return f"说明：Top3含平局比分，低比分与防平风险仍需保留。"
    return "说明：Top3按混合概率排序，展示主要比分分布。"


def score_explanation(match: dict) -> str:
    recommendation = match.get("recommended_score") or {}
    favorite_group = recommendation.get("favorite_group")
    if not favorite_group:
        favorite_group = max(match["blended_wdl"].items(), key=lambda item: item[1])[0]
    favorite_label = outcome_label(favorite_group)
    selection_kind = recommendation.get("selection_kind")
    if selection_kind == "dominant_tail":
        return f"说明：{favorite_label}优势叠加尾部信号，保留大胜比分。"
    if selection_kind == "matchday_tail":
        return f"说明：同阶段高比分升温，{favorite_label}方向上调。"
    selection_reason = recommendation.get("selection_reason")
    if selection_reason == "weak_favorite_draw_protection":
        return f"说明：热门优势不足，平局结构强，按防平保留。"
    raw_top_score = recommendation.get("raw_top_score")
    raw_top_group = recommendation.get("raw_top_group")
    if recommendation.get("openness_adjusted"):
        target = recommendation.get("openness_target_total")
        if target is not None:
            return f"说明：开放度偏高，按{favorite_label}方向上调至约{float(target):.1f}球。"
        return f"说明：开放度偏高，按{favorite_label}方向上调比分。"
    if raw_top_score and raw_top_group and raw_top_group != favorite_group:
        return f"说明：原始最高{raw_top_score}偏{outcome_label(raw_top_group)}，发布按{favorite_label}倾向优先。"
    if int(recommendation.get("tie_count") or 1) > 1:
        return f"说明：{favorite_label}方向内并列最高，多个比分同时保留。"
    return f"说明：在{favorite_label}倾向内选择最高概率比分。"


def card_scores(data: dict, date_slug: str) -> Path:
    num_matches = len(data["matches"])
    height = max(1440, 228 + num_matches * 306 + 100)
    image, draw = base_card("AI比分预估", f"{competition_label(data)} 比分预测", "02", height=height)
    
    y_start = 228
    panels = []
    for _ in range(num_matches):
        panels.append((170, y_start, 1020, y_start + 260))
        y_start += 306
        
    for match, box in zip(data["matches"], panels):
        x1, y1, x2, y2 = box
        rounded(draw, box, COLORS["panel"], COLORS["line"], radius=8)
        draw_text(draw, (x1 + 26, y1 + 25), match["match"], fit_text(draw, match["match"], x2 - x1 - 360, 34), COLORS["ink"])
        pill(draw, x2 - 210, y1 + 24, "比分 Top3", COLORS["soft_red"], COLORS["red"], size=20, pad_x=16, pad_y=8)
        draw.line((x1 + 26, y1 + 74, x2 - 26, y1 + 74), fill=COLORS["line"], width=1)

        rows = score_pick_rows(match)
        col_w = (x2 - x1 - 86) // 3
        for index, row in enumerate(rows):
            rx = x1 + 26 + index * col_w
            score = str(row.get("score") or "")
            probability = pct(float(row.get("blended_probability") or 0.0))
            label = "Top 1" if index == 0 else f"Top {index + 1}"
            draw_text(draw, (rx, y1 + 104), label, FONTS["tiny"], COLORS["muted"])
            draw_text(draw, (rx, y1 + 134), score, fit_text(draw, score, col_w - 28, 58, 36), COLORS["red"] if index == 0 else COLORS["ink"])
            draw_text(draw, (rx, y1 + 190), f"概率 {probability}", FONTS["small"], COLORS["green"] if index == 0 else COLORS["muted"])
            if index < 2:
                sx = rx + col_w - 18
                draw.line((sx, y1 + 108, sx, y1 + 210), fill=COLORS["line"], width=1)
        for line_index, line in enumerate(wrap_text(draw, score_top3_explanation(match), x2 - x1 - 72, FONTS["tiny"], 1)):
            draw_text(draw, (x1 + 26, y1 + 224 + line_index * 24), line, FONTS["tiny"], COLORS["muted"])
    draw_text(draw, (170, height - 80), "说明：AI 模型计算结果，不构成建议。", FONTS["small"], COLORS["muted"])
    out = OUT_DIR / f"{date_slug}-02-score-pick.png"
    image.save(out, quality=95)
    legacy_out = OUT_DIR / f"{date_slug}-02-score-top8.png"
    image.save(legacy_out, quality=95)
    return out


DEFAULT_MATCH_SHORT = {
    "荷兰 vs 瑞典": "荷瑞",
    "德国 vs 科特迪瓦": "德科",
    "厄瓜多尔 vs 库拉索": "厄库",
    "突尼斯 vs 日本": "突日",
    "新西兰 vs 埃及": "新埃",
    "乌拉圭 vs 佛得角": "乌佛",
    "比利时 vs 伊朗": "比伊",
    "西班牙 vs 沙特": "西沙",
    "阿根廷 vs 奥地利": "阿奥",
    "法国 vs 伊拉克": "法伊",
    "挪威 vs 塞内加尔": "挪塞",
    "约旦 vs 阿尔及利亚": "约阿",
    "葡萄牙 vs 乌兹别克": "葡乌",
    "英格兰 vs 加纳": "英加",
    "巴拿马 vs 克罗地亚": "巴克",
    "哥伦比亚 vs 刚果金": "哥刚",
    "瑞士 vs 加拿大": "瑞加",
    "波黑 vs 卡塔尔": "波卡",
    "苏格兰 vs 巴西": "苏巴",
    "摩洛哥 vs 海地": "摩海",
    "南非 vs 韩国": "南韩",
    "捷克 vs 墨西哥": "捷墨",
    "厄瓜多尔 vs 德国": "厄德",
    "库拉索 vs 科特迪瓦": "库科",
    "突尼斯 vs 荷兰": "突荷",
    "日本 vs 瑞典": "日瑞",
    "巴拉圭 vs 澳大利亚": "巴澳",
    "土耳其 vs 美国": "土美",
    "克罗地亚 vs 加纳": "克加",
    "巴拿马 vs 英格兰": "巴英",
    "哥伦比亚 vs 葡萄牙": "哥葡",
    "刚果金 vs 乌兹别克": "刚乌",
    "阿尔及利亚 vs 奥地利": "阿尔奥",
    "约旦 vs 阿根廷": "约阿根",
    "南非 vs 加拿大": "南加",
    "巴西 vs 日本": "巴日",
    "德国 vs 巴拉圭": "德巴",
    "荷兰 vs 摩洛哥": "荷摩",
}


def auto_short_match(match_name: str) -> str:
    parts = match_name.split(" vs ")
    if len(parts) == 2:
        return parts[0][0] + parts[1][0]
    return match_name

def short_parlay(parlay: dict, match_short: dict[str, str]) -> str:
    parts = []
    for leg in parlay["legs"]:
        m_name = leg['match']
        short_name = match_short.get(m_name) or auto_short_match(m_name)
        parts.append(f"{short_name} {leg['score']}")
    return " / ".join(parts)


def card_parlays(data: dict, date_slug: str, match_short: dict[str, str]) -> Path:
    # We might need a larger height if there are many legs making the text wrap more
    parlay_name = f"{len(data['matches'])} 串 1"
    image, draw = base_card(f"{parlay_name} 方案", f"{competition_label(data)} 赛前方案", "03", height=1440)
    groups = [
        ("前 3：优先方案", "probability_first", COLORS["soft_green"], "聚焦主比分路径"),
        ("中 3：进取方向", "odds_first", COLORS["soft_amber"], "保留可选比分路径"),
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
            text = short_parlay(parlay, match_short)
            draw_text(draw, (250, row_y + 5), text, fit_text(draw, text, 705, 25, 17), COLORS["ink"])
            row_y += 72
            seq += 1
        y += 368
    
    abb_list = []
    for m in data["matches"]:
        m_name = m["match"]
        s_name = match_short.get(m_name) or auto_short_match(m_name)
        abb_list.append(f"{s_name}={m_name}")
    abbreviations = "，".join(abb_list)
    
    draw_text(draw, (170, 1344), f"缩写：{abbreviations}。", fit_text(draw, f"缩写：{abbreviations}。", 850, 17, 13), COLORS["muted"])
    draw_text(draw, (170, 1375), "说明：精确比分长串属于高难度方案，仅作赛前模型结论展示。", FONTS["tiny"], COLORS["red"])
    out = OUT_DIR / f"{date_slug}-03-parlay-top9.png"
    image.save(out, quality=95)
    return out


def write_copy(data: dict, paths: list[Path], date_slug: str, title_date: str, match_short: dict[str, str]) -> Path:
    parlay_name = f"{len(data['matches'])}串1"
    competition = competition_label(data)
    matches = []
    for match in data["matches"]:
        rel = relationship_context(match)
        matches.append(f"- {match['match']}：{rel['label']}（{rel.get('probability_text', '')}）")

    score_lines = []
    for match in data["matches"]:
        score_lines.append(
            f"- {match['match']}：{score_pick_text(match)}。"
            f"{score_top3_explanation(match).replace('说明：', '')}"
        )

    parlay_lines = []
    for title, key in [
        ("概率优先", "probability_first"),
        ("赔率优先", "odds_first"),
        ("高方差方向", "expected_value_first"),
    ]:
        parlay_lines.append(f"{title}：")
        for parlay in data["parlay_groups"][key]:
            parlay_lines.append(f"- {short_parlay(parlay, match_short)}")

    body = f"""# 小红书图文文案｜{date_slug} {competition}预测

## 笔记标题
{competition} {title_date} {len(data['matches'])}场赛前结论：胜负、比分、{parlay_name}方案

## 正文
以下只展示赛前模型结论和方案，不展开计算指标。不是投注建议。

胜负倾向：
{chr(10).join(matches)}

比分 Top3：
{chr(10).join(score_lines)}

{parlay_name}分三组：
{chr(10).join(parlay_lines)}

## 话题
#{competition} #足球数据分析 #比分预测 #足球模型 #赛前分析 #小红书体育

## 图片文件
{chr(10).join(f'- {path.name}' for path in paths)}
"""
    out = OUT_DIR / f"{date_slug}-xhs-copy.md"
    out.write_text(body, encoding="utf-8")
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON), help="Analysis JSON produced by analyze_score_odds_parlay.py.")
    parser.add_argument("--date", default="2026-06-21", help="Date slug for output filenames.")
    parser.add_argument("--title-date", help="Human title date, e.g. 6月22日.")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    data = json.loads(Path(args.report_json).read_text(encoding="utf-8"))
    title_date = args.title_date or date_label(args.date)
    match_short = DEFAULT_MATCH_SHORT.copy()
    paths = [
        card_wdl(data, args.date, title_date),
        card_scores(data, args.date),
        card_parlays(data, args.date, match_short),
    ]
    copy_path = write_copy(data, paths, args.date, title_date, match_short)
    print(json.dumps({"images": [str(path) for path in paths], "copy": str(copy_path)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
