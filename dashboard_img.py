"""Onsite Club 会展监控看板图片生成器。

输出 1600×900 深色看板 PNG（风格与项目日报一致）：
- 顶部标题 + 关键指标条（当月会展 / 今日新增 / 今日进行 / 覆盖城市 / 涉及类型）
- 中国地图：国家轮廓 + 城市气泡（气泡大小 = 该城会展数）
- 折线图：当月每日进行中会展数
- 饼图：会展类型分布
- 词云：标题关键词（jieba 分词 + PIL 螺旋排布，无第三方词云依赖）
- 同步输出地图、趋势、类型和词云四张 1600×900 独立图片，按类型目录归档
- 底部数据源说明

依赖 matplotlib / numpy / Pillow / jieba，中文字体用系统字体（微软雅黑/黑体）。
"""
import json
import math
import os
import random
from datetime import date, datetime, timedelta

import jieba
import matplotlib
matplotlib.use("Agg")
import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Polygon as MplPolygon
from PIL import Image, ImageDraw, ImageFont

# 中性深色底配合金色、蓝色、绿色数据色，兼顾屏幕与推送缩略图。
BG = "#0E1116"
PANEL = "#171B22"
FG = "#F2F0EA"
MUTED = "#A7AFBA"
BORDER = "#303744"
ACCENT = "#DDB65B"
ACCENT_BRIGHT = "#F0CB75"
CRIMSON = "#D85F70"
BLUE = "#67A5D8"
GREEN = "#65B58B"
VIOLET = "#A98AC4"

CHART_SUBDIRS = {
    "city_map": "city_maps",
    "daily_trend": "daily_trends",
    "type_distribution": "type_distributions",
    "wordcloud": "wordclouds",
}

CHINA_MAP_URL = "https://geo.datav.aliyun.com/areas_v3/bound/100000_full.json"
CHINA_MAP_FILE = "china_map.json"
CHINA_BBOX = (73, 135, 17, 54)  # lon_min, lon_max, lat_min, lat_max

_CHINA_MAP_CACHE = {}
_PROJECTED_CHINA_CACHE = {}
_CONFIGURED_FONT_PATH = None

CITY_COORDS = {
    "北京": (116.40, 39.90), "上海": (121.47, 31.23), "广州": (113.26, 23.13),
    "深圳": (114.06, 22.55), "成都": (104.07, 30.67), "重庆": (106.55, 29.56),
    "杭州": (120.15, 30.27), "南京": (118.78, 32.06), "武汉": (114.31, 30.59),
    "苏州": (120.58, 31.30), "西安": (108.94, 34.34), "南宁": (108.32, 22.82),
    "宁波": (121.55, 29.88), "天津": (117.20, 39.08), "长沙": (112.94, 28.23),
    "郑州": (113.63, 34.75), "青岛": (120.38, 36.07), "大连": (121.62, 38.91),
    "海口": (110.32, 20.03), "昆明": (102.83, 24.88), "沈阳": (123.43, 41.80),
    "厦门": (118.09, 24.48), "佛山": (113.12, 23.02), "无锡": (120.30, 31.57),
    "东莞": (113.75, 23.02), "哈尔滨": (126.63, 45.75), "合肥": (117.23, 31.82),
    "南昌": (115.86, 28.68), "贵阳": (106.63, 26.65), "福州": (119.30, 26.08),
    "石家庄": (114.51, 38.04), "太原": (112.55, 37.87), "济南": (117.12, 36.65),
    "长春": (125.32, 43.90), "呼和浩特": (111.75, 40.84), "兰州": (103.83, 36.06),
    "乌鲁木齐": (87.62, 43.83), "拉萨": (91.11, 29.97), "银川": (106.23, 38.49),
    "西宁": (101.78, 36.62), "三亚": (109.51, 18.25), "香港": (114.17, 22.28),
    "澳门": (113.55, 22.19), "台北": (121.52, 25.03), "常州": (119.95, 31.78),
    "南通": (120.89, 31.98), "温州": (120.70, 28.00), "烟台": (121.45, 37.46),
    "珠海": (113.57, 22.27), "中山": (113.39, 22.52), "桂林": (110.29, 25.27),
    "泉州": (118.68, 24.87), "合肥": (117.23, 31.82), "兰州": (103.83, 36.06),
    "嘉兴": (120.76, 30.75), "绍兴": (120.58, 30.00), "台州": (121.42, 28.66),
    "徐州": (117.28, 34.26), "潍坊": (119.16, 36.71), "保定": (115.46, 38.87),
    "洛阳": (112.45, 34.62), "唐山": (118.18, 39.63), "秦皇岛": (119.60, 39.94),
    "常熟": (120.75, 31.65), "昆山": (120.98, 31.39), "太仓": (121.13, 31.45),
    "张家港": (120.55, 31.88), "江阴": (120.28, 31.92), "义乌": (120.07, 29.31),
}

# 词云/高频词过滤：文案词、类型词、城市与年份等无实义词（只保留会展内容关键词）
CLOUD_STOPWORDS = {
    "主题", "限时", "特展", "首发", "空间", "城市", "系列", "品牌", "快闪", "慢闪",
    "全国", "中国", "暨", "与", "的", "了", "「", "」", "《", "》", "·", "之一",
    "购物中心", "商场", "第一", "全球", "内地", "顶级", "限时店", "首展", "重磅",
    "展览", "市集", "嘉年华", "秀场", "演出", "发布会", "首映", "艺术展",
    "快闪店", "慢闪展", "慢闪空间", "主题店", "概念店", "精品店",
    # 城市/年份/季节等噪声（分布段已单独展示城市）
    "上海", "北京", "广州", "深圳", "成都", "重庆", "杭州", "南京", "武汉", "苏州",
    "西安", "南宁", "宁波", "天津", "长沙", "郑州", "青岛", "大连", "海口", "昆明",
    "沈阳", "厦门", "佛山", "无锡", "东莞", "哈尔滨", "合肥", "济南", "南昌", "贵阳",
    "福州", "石家庄", "太原", "长春", "三亚", "香港", "澳门", "台北",
    "2026", "2025", "2024", "IP", "巡展", "春夏", "秋冬", "全新", "首站",
}


def _font_candidates():
    return [
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
        r"C:\Windows\Fonts\Deng.ttf",
    ]


def find_chinese_font():
    for path in _font_candidates():
        if os.path.exists(path):
            return path
    return None


def setup_chinese_font():
    """注册中文字体并设为 matplotlib 默认，返回字体路径。"""
    global _CONFIGURED_FONT_PATH
    path = find_chinese_font()
    if not path:
        return None
    if path == _CONFIGURED_FONT_PATH:
        return path
    try:
        fm.fontManager.addfont(path)
    except Exception:
        pass
    try:
        family = fm.FontProperties(fname=path).get_name()
        plt.rcParams["font.family"] = [family, "sans-serif"]
    except Exception:
        pass
    plt.rcParams["axes.unicode_minus"] = False
    _CONFIGURED_FONT_PATH = path
    return path


def load_china_map(map_path=None, timeout=15):
    """读取国家轮廓 GeoJSON；本地缺失时从 DataV 拉取并缓存到 DATA_DIR。"""
    map_path = map_path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", CHINA_MAP_FILE)
    if os.path.exists(map_path):
        stat = os.stat(map_path)
        cache_key = os.path.abspath(map_path)
        fingerprint = (stat.st_mtime_ns, stat.st_size)
        cached = _CHINA_MAP_CACHE.get(cache_key)
        if cached and cached[0] == fingerprint:
            return cached[1]
        with open(map_path, "r", encoding="utf-8") as file:
            data = json.load(file)
        _CHINA_MAP_CACHE[cache_key] = (fingerprint, data)
        return data
    import requests
    response = requests.get(CHINA_MAP_URL, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    os.makedirs(os.path.dirname(map_path), exist_ok=True)
    with open(map_path, "w", encoding="utf-8") as file:
        json.dump(data, file)
    _CHINA_MAP_CACHE[os.path.abspath(map_path)] = (
        (os.stat(map_path).st_mtime_ns, os.path.getsize(map_path)), data,
    )
    return data


def _iter_rings(coords):
    """递归展开 GeoJSON 坐标，产出每个闭环（list of [lon, lat]）。

    Geometry 可能为 Polygon（多环）或 MultiPolygon（多多边形），
    需要下探到「环」这一层：环是第一个元素为 [lon, lat] 的列表。
    """
    if not coords:
        return
    first = coords[0]
    if isinstance(first, (int, float)):
        return  # 单点，不是环
    if isinstance(first[0], (int, float)):
        yield coords  # 环：[[lon, lat], ...]
    else:
        for part in coords:
            yield from _iter_rings(part)


def _project(lon, lat):
    lon_min, lon_max, lat_min, lat_max = CHINA_BBOX
    x = (lon - lon_min) / (lon_max - lon_min)
    y = (lat - lat_min) / (lat_max - lat_min)
    return x, y


def _projected_china_rings(geoj):
    """Return cached normalized coordinate arrays for a GeoJSON object."""
    cache_key = id(geoj)
    cached = _PROJECTED_CHINA_CACHE.get(cache_key)
    if cached and cached[0] is geoj:
        return cached[1]

    rings = []
    for feature in geoj.get("features", []):
        geom = feature.get("geometry") or {}
        for ring in _iter_rings(geom.get("coordinates", [])):
            if len(ring) < 3:
                continue
            coordinates = np.asarray(ring, dtype=float)
            if coordinates.ndim != 2 or coordinates.shape[1] < 2:
                continue
            projected = np.empty((len(coordinates), 2), dtype=float)
            projected[:, 0] = (coordinates[:, 0] - CHINA_BBOX[0]) / (CHINA_BBOX[1] - CHINA_BBOX[0])
            projected[:, 1] = (coordinates[:, 1] - CHINA_BBOX[2]) / (CHINA_BBOX[3] - CHINA_BBOX[2])
            rings.append(projected)

    if len(_PROJECTED_CHINA_CACHE) >= 4:
        _PROJECTED_CHINA_CACHE.clear()
    _PROJECTED_CHINA_CACHE[cache_key] = (geoj, rings)
    return rings


def draw_china(ax, geoj, color="#202832", edge="#465365", lw=0.6):
    """在给定的 axes 上绘制国家轮廓（Polygon 剪影）。"""
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_aspect(1.18)  # 纬度高纬度压缩，近似等距投影的视觉比例
    ax.axis("off")
    ax.set_facecolor(PANEL)
    for ring in _projected_china_rings(geoj):
        ax.add_patch(MplPolygon(ring, closed=True, facecolor=color, edgecolor=edge, lw=lw))


def draw_city_bubbles(ax, city_counts, max_labels=None):
    """绘制城市气泡：气泡大小 ∝ 会展数，标签显示城市名与数量。"""
    if not city_counts:
        return
    sizes = np.array([c for _c, c in city_counts], dtype=float)
    scale = max(1.0, float(sizes.max()))
    max_r = 0.045
    label_offsets = [(12, 10), (12, -12), (-12, 12), (-12, -12), (18, 0), (-18, 0)]
    for index, ((name, count), size) in enumerate(zip(city_counts, sizes)):
        if name not in CITY_COORDS:
            continue
        lon, lat = CITY_COORDS[name]
        x, y = _project(lon, lat)
        r = max_r * (size / scale) ** 0.6 + 0.004
        ax.scatter([x], [y], s=(r * 900) ** 2, color=ACCENT, alpha=0.9,
                   edgecolors=FG, linewidths=0.7, zorder=5)
        if max_labels is not None and index >= max_labels:
            continue
        label = f"{name} {count}" if count > 1 else name
        offset_x, offset_y = label_offsets[index % len(label_offsets)]
        ax.annotate(
            label, xy=(x, y), xytext=(offset_x, offset_y), textcoords="offset points",
            color=FG, fontsize=9, va="center",
            ha="left" if offset_x > 0 else "right", zorder=6,
            arrowprops={"arrowstyle": "-", "color": BORDER, "lw": 0.7},
        )


def _daily_active(events, first, days):
    """Return the number of active exhibitions for every day in the month."""
    if days <= 0:
        return []

    last = first + timedelta(days=days - 1)
    changes = [0] * (days + 1)
    for event in events:
        try:
            start = date.fromisoformat(str(event.get("start_date") or ""))
            end = date.fromisoformat(str(event.get("end_date") or ""))
        except (TypeError, ValueError):
            continue
        if start > end or end < first or start > last:
            continue
        start_index = max(0, (start - first).days)
        end_index = min(days - 1, (end - first).days)
        changes[start_index] += 1
        changes[end_index + 1] -= 1

    counts = []
    active = 0
    for offset in range(days):
        active += changes[offset]
        counts.append(active)
    return counts


def draw_line(ax, counts, month_label, annotate_peak=False):
    """绘制当月每日进行中会展数折线。"""
    ax.set_facecolor(PANEL)
    if not counts:
        ax.text(0.5, 0.5, "暂无趋势数据", transform=ax.transAxes, ha="center", va="center",
                color=MUTED, fontsize=12)
        ax.axis("off")
        return
    x = np.arange(len(counts))
    ax.fill_between(x, counts, color=BLUE, alpha=0.13)
    ax.plot(x, counts, color=BLUE, lw=2.4, marker="o", markersize=3.2,
            markerfacecolor=ACCENT_BRIGHT, markeredgewidth=0)
    ax.grid(axis="y", color=BORDER, lw=0.7, alpha=0.7)
    ax.tick_params(colors=MUTED, labelsize=9, length=0, pad=8)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(BORDER)
    ax.spines["bottom"].set_color(BORDER)
    tick_indexes = sorted(set([0, 7, 14, 21, len(counts) - 1]))
    tick_indexes = [index for index in tick_indexes if index < len(counts)]
    ax.set_xticks(tick_indexes)
    ax.set_xticklabels([f"{index + 1:02d}" for index in tick_indexes])
    ax.set_xlabel(f"{month_label}日期", color=MUTED, fontsize=9)
    ax.set_ylabel("进行中会展", color=MUTED, fontsize=9)
    ax.margins(x=0.02, y=0.16)

    if annotate_peak:
        peak_index = int(np.argmax(counts))
        peak = counts[peak_index]
        ax.scatter([peak_index], [peak], s=85, color=ACCENT_BRIGHT, edgecolor=BG,
                   linewidth=1.5, zorder=6)
        ax.annotate(
            f"峰值 {peak} 场 · {peak_index + 1:02d} 日",
            xy=(peak_index, peak), xytext=(0, 24), textcoords="offset points",
            ha="center", color=FG, fontsize=11,
            arrowprops={"arrowstyle": "-", "color": ACCENT, "lw": 1},
        )


def draw_pie(ax, type_counts, compact=False):
    """绘制会展类型分布环形图。"""
    labels = list(type_counts.keys())
    values = list(type_counts.values())
    if not values:
        ax.text(0.5, 0.5, "暂无类型数据", transform=ax.transAxes, ha="center", va="center",
                color=MUTED, fontsize=12)
        ax.axis("off")
        return
    colors = [ACCENT, CRIMSON, BLUE, GREEN, VIOLET, "#D58B5F", "#7B8491"][:len(labels)]
    radius = 0.76 if compact else 1.0
    center = (-0.34, 0) if compact else (0, 0)
    wedges, _texts, autotexts = ax.pie(
        values, colors=colors, startangle=90, counterclock=False,
        wedgeprops={"width": 0.38, "edgecolor": PANEL, "linewidth": 2.5},
        autopct=lambda p: f"{int(round(p * sum(values) / 100))}场" if p >= 5 else "",
        pctdistance=0.81, radius=radius, center=center,
    )
    for t in autotexts:
        t.set_color(FG)
        t.set_fontsize(8)
    legend_kwargs = {
        "loc": "center right" if compact else "center left",
        "bbox_to_anchor": (1.0, 0.5) if compact else (0.98, 0.5),
        "fontsize": 7 if compact else 9,
    }
    ax.legend(
        wedges, [f"{l} {v}" for l, v in zip(labels, values)],
        frameon=False, labelcolor=FG, handlelength=1.2, handletextpad=0.8,
        **legend_kwargs,
    )
    ax.text(center[0], 0.06, str(sum(values)), ha="center", va="center", color=FG,
            fontsize=20 if compact else 25, fontweight="bold", family="DejaVu Sans")
    ax.text(center[0], -0.13, "会展总数", ha="center", va="center", color=MUTED,
            fontsize=8 if compact else 9)
    if compact:
        ax.set_xlim(-1.2, 1.2)
    ax.set_facecolor(PANEL)


def _tokenize_titles(events):
    """jieba 分词并统计词频，过滤停用词与单字。"""
    freq = {}
    for event in events:
        text = str(event.get("title") or "")
        for token in jieba.cut(text):
            token = token.strip()
            if len(token) < 2:
                continue
            if token in CLOUD_STOPWORDS:
                continue
            freq[token] = freq.get(token, 0) + 1
    return sorted(freq.items(), key=lambda x: -x[1])[:32]


def _rects_overlap(rect, placed):
    x0, y0, x1, y1 = rect
    for (px0, py0, px1, py1) in placed:
        if not (x1 < px0 or px1 < x0 or y1 < py0 or py1 < y0):
            return True
    return False


class _RectSpatialIndex:
    """Grid index that limits word-cloud overlap checks to nearby words."""

    def __init__(self, cell_size=64):
        self.cell_size = cell_size
        self.rectangles = []
        self.cells = {}

    def _cells_for(self, rect):
        x0, y0, x1, y1 = rect
        min_x = int(x0 // self.cell_size)
        max_x = int(x1 // self.cell_size)
        min_y = int(y0 // self.cell_size)
        max_y = int(y1 // self.cell_size)
        for cell_x in range(min_x, max_x + 1):
            for cell_y in range(min_y, max_y + 1):
                yield cell_x, cell_y

    def overlaps(self, rect):
        candidates = set()
        for cell in self._cells_for(rect):
            candidates.update(self.cells.get(cell, ()))
        return _rects_overlap(rect, (self.rectangles[index] for index in candidates))

    def add(self, rect):
        index = len(self.rectangles)
        self.rectangles.append(rect)
        for cell in self._cells_for(rect):
            self.cells.setdefault(cell, []).append(index)


def draw_wordcloud(img, words, font_path, seed=7, min_font_size=24, font_size_span=34):
    """PIL 螺旋排布词云（无第三方词云依赖）。"""
    rng = random.Random(seed)
    draw = ImageDraw.Draw(img)
    w, h = img.size
    cx, cy = w / 2, h / 2
    placed = _RectSpatialIndex()
    fonts = {}
    weights = [v for _k, v in words]
    max_w = max(weights) if weights else 1

    for idx, (word, weight) in enumerate(words):
        size = min_font_size + int(round(weight / max_w * font_size_span))
        try:
            font = fonts.get(size)
            if font is None:
                font = ImageFont.truetype(font_path, size)
                fonts[size] = font
        except Exception:
            continue
        bbox = draw.textbbox((0, 0), word, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        if tw <= 0 or th <= 0:
            continue

        color = rng.choice([ACCENT_BRIGHT, FG, BLUE, GREEN, VIOLET, "#D9A66F"])

        placed_flag = False
        for step in range(1, 160):
            radius = step * 7.0
            for k in range(24):
                theta = 2 * math.pi * k / 24 + (step % 7)
                dx = radius * math.cos(theta)
                dy = radius * math.sin(theta) * 0.55
                x = cx + dx - tw / 2
                y = cy + dy - th / 2
                if x < 4 or y < 4 or x + tw > w - 4 or y + th > h - 4:
                    continue
                rect = (x, y, x + tw, y + th)
                if not placed.overlaps(rect):
                    draw.text((x, y), word, font=font, fill=color)
                    placed.add(rect)
                    placed_flag = True
                    break
            if placed_flag:
                break


def _kpi_box(fig, ax, title, value, sub="", value_color=None):
    ax.set_facecolor(PANEL)
    ax.axis("off")
    for spine in ax.spines.values():
        spine.set_color(BORDER)
    value_color = value_color or ACCENT_BRIGHT
    ax.text(0.5, 0.72, str(value), ha="center", va="center", color=value_color,
            fontsize=30, fontweight="bold", family="DejaVu Sans")
    ax.text(0.5, 0.28, title, ha="center", va="center", color=MUTED, fontsize=10)


def individual_chart_paths(output_path, month, chart_output_dir=None):
    """Return stable, type-specific output paths for standalone chart images."""
    dashboard_dir = os.path.dirname(output_path) or "."
    root = chart_output_dir or os.path.join(dashboard_dir, "charts")
    return {
        chart_type: os.path.join(
            root, subdir, f"onsiteclub_{chart_type}_{month}.png",
        )
        for chart_type, subdir in CHART_SUBDIRS.items()
    }


def _chart_figure(title, subtitle):
    fig = plt.figure(figsize=(16, 9), dpi=100)
    fig.patch.set_facecolor(BG)
    fig.text(0.05, 0.94, title, ha="left", va="center", color=FG,
             fontsize=28, fontweight="bold")
    fig.text(0.95, 0.94, subtitle, ha="right", va="center", color=MUTED, fontsize=11)
    accent_bar = fig.add_axes([0.05, 0.895, 0.90, 0.004])
    accent_bar.set_facecolor(ACCENT)
    accent_bar.set_xticks([])
    accent_bar.set_yticks([])
    for spine in accent_bar.spines.values():
        spine.set_visible(False)
    return fig


def _save_figure(fig, path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fig.savefig(path, dpi=100, facecolor=BG)
    plt.close(fig)


def _save_individual_charts(counts, city_counts, type_counts, words, month, month_label,
                            today, output_path, map_data, font_path, chart_output_dir=None):
    paths = individual_chart_paths(output_path, month, chart_output_dir)
    subtitle = f"{month_label} · 更新于 {today.strftime('%Y-%m-%d')} · onsiteclub.com"

    fig = _chart_figure("会展城市分布", subtitle)
    ax = fig.add_axes([0.08, 0.10, 0.84, 0.74])
    if map_data:
        draw_china(ax, map_data)
        draw_city_bubbles(ax, city_counts[:24], max_labels=16)
    else:
        ax.set_facecolor(PANEL)
        ax.text(0.5, 0.5, "地图数据暂不可用", transform=ax.transAxes,
                ha="center", va="center", color=MUTED, fontsize=13)
        ax.axis("off")
    top_cities = "  ·  ".join(f"{name} {count}场" for name, count in city_counts[:6])
    fig.text(0.05, 0.045, top_cities or "暂无城市数据", color=MUTED, fontsize=10)
    _save_figure(fig, paths["city_map"])

    fig = _chart_figure("每日进行中会展趋势", subtitle)
    ax = fig.add_axes([0.08, 0.12, 0.87, 0.70])
    draw_line(ax, counts, month_label, annotate_peak=True)
    _save_figure(fig, paths["daily_trend"])

    fig = _chart_figure("会展类型分布", subtitle)
    ax = fig.add_axes([0.10, 0.08, 0.72, 0.76])
    draw_pie(ax, type_counts)
    _save_figure(fig, paths["type_distribution"])

    fig = _chart_figure("标题关键词云", subtitle)
    cloud_img = Image.new("RGBA", (1500, 620), (0, 0, 0, 0))
    if font_path and words:
        draw_wordcloud(cloud_img, words, font_path, min_font_size=38, font_size_span=56)
    ax = fig.add_axes([0.04, 0.08, 0.92, 0.76])
    ax.set_facecolor(PANEL)
    if words and font_path:
        ax.imshow(cloud_img, extent=[0, 1, 0, 1], aspect="auto")
    else:
        ax.text(0.5, 0.5, "暂无关键词数据", transform=ax.transAxes,
                ha="center", va="center", color=MUTED, fontsize=13)
    ax.axis("off")
    _save_figure(fig, paths["wordcloud"])
    return paths


def build_dashboard(events, new_events, today=None, output_path=None, month_label="8月",
                    map_data=None, font_path=None, chart_output_dir=None,
                    generate_individual=True):
    """生成看板 PNG，返回输出路径。"""
    today = today or date.today()
    font_path = font_path or find_chinese_font()
    setup_chinese_font()

    month = today.strftime("%Y-%m")
    year, mon = map(int, month.split("-"))
    first = date(year, mon, 1)
    if mon == 12:
        last = date(year + 1, 1, 1)
    else:
        last = date(year, mon + 1, 1)
    days = (last - first).days

    counts = _daily_active(events, first, days)

    city_counts = {}
    for e in events:
        city = e.get("city") or "其他"
        city_counts[city] = city_counts.get(city, 0) + 1
    city_counts_sorted = sorted(city_counts.items(), key=lambda x: -x[1])

    type_counts = {}
    for e in events:
        t = e.get("type") or "其他"
        type_counts[t] = type_counts.get(t, 0) + 1

    active_today = sum(1 for e in events if e.get("start_date", "") <= today.isoformat() <= e.get("end_date", ""))

    # 固定画布 16:9 @110dpi，坐标可精确映射，避免 bbox_inches=tight 导致布局漂移
    fig = plt.figure(figsize=(16, 9), dpi=110)
    fig.patch.set_facecolor(BG)

    # 标题
    fig.text(0.05, 0.955, f"Onsite Club {month_label}会展看板", ha="left", va="center",
             color=FG, fontsize=26, fontweight="bold")
    fig.text(0.95, 0.955, f"{today.strftime('%Y-%m-%d')} · onsiteclub.com/calendar",
             ha="right", va="center", color=MUTED, fontsize=11)
    title_bar = fig.add_axes([0.05, 0.922, 0.90, 0.003])
    title_bar.set_facecolor(ACCENT)
    title_bar.axis("off")

    # KPI 条
    kpis = [
        ("当月会展", len(events)), ("今日新增", len(new_events)), ("今日进行中", active_today),
        ("覆盖城市", len(city_counts)), ("涉及类型", len(type_counts)),
    ]
    kpi_axes = [fig.add_axes([0.02 + i * 0.19, 0.845, 0.175, 0.065]) for i in range(5)]
    for ax, (title, value) in zip(kpi_axes, kpis):
        _kpi_box(fig, ax, title, value)

    # 三栏：地图 / 折线 / 饼图
    ax_map = fig.add_axes([0.02, 0.36, 0.34, 0.44])
    ax_line = fig.add_axes([0.40, 0.36, 0.31, 0.44])
    ax_pie = fig.add_axes([0.74, 0.36, 0.24, 0.44])
    _panel_title(fig, 0.19, 0.815, "会展城市分布")
    _panel_title(fig, 0.555, 0.815, "每日进行中会展数")
    _panel_title(fig, 0.86, 0.815, "类型分布")

    if map_data:
        draw_china(ax_map, map_data)
        draw_city_bubbles(ax_map, city_counts_sorted, max_labels=5)
    else:
        ax_map.axis("off")
        ax_map.set_facecolor(PANEL)

    draw_line(ax_line, counts, month_label)
    draw_pie(ax_pie, type_counts, compact=True)

    # 词云（PIL 渲染后 imshow 到固定区域，定位可控）
    _panel_title(fig, 0.5, 0.315, "标题关键词云")
    words = _tokenize_titles(events)
    cloud_img = Image.new("RGBA", (1560, 240), (0, 0, 0, 0))
    if font_path and words:
        draw_wordcloud(cloud_img, words, font_path)
    ax_cloud = fig.add_axes([0.02, 0.10, 0.96, 0.20])
    ax_cloud.imshow(cloud_img, extent=[0, 1, 0, 1], aspect="auto")
    ax_cloud.axis("off")
    ax_cloud.set_facecolor(BG)

    # 底部说明
    fig.text(0.5, 0.03, "自动生成 · 新增会展见文字通知与卡片 · 含跨月长期展", ha="center",
             va="center", color=MUTED, fontsize=10)

    output_path = output_path or os.path.join("reports", f"onsiteclub_dashboard_{month}.png")
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    fig.savefig(output_path, dpi=110, facecolor=BG)
    plt.close(fig)
    if generate_individual:
        _save_individual_charts(
            counts, city_counts_sorted, type_counts, words, month, month_label,
            today, output_path, map_data, font_path, chart_output_dir,
        )
    return output_path


def _panel_title(fig, x, y, text):
    fig.text(x, y, text, ha="center", va="center", color=ACCENT, fontsize=13, fontweight="bold")
