"""Onsite Club 会展日历监控主流程。

每日 9:00 流程：
1. 抓取当月全部会展（含跨月长期展）。
2. 与本地历史记录对比，识别「今日新增」（首日显示全部）。
3. 生成两个产物：一段详细文本（今日新增 + 未来N天新开日程 + 正在进行 + 分布速览）、一张看板图片。
4. 推送文本与图片到钉钉，同事直接在群里阅读。

状态存于 data/onsiteclub_calendar_state.json：
- seen_ids：历史出现过的会展 id（用于识别新增）
- cache：会展详情缓存（城市/品牌/类型/封面等，避免重复抓详情页）
- history：每日新增记录
"""
import json
import os
from datetime import datetime, date, timedelta
from pathlib import Path

from config import DATA_DIR, LOG_DIR, ONSITECLUB_SCHEDULE_DAYS, REPORT_DIR
from crawlers.onsiteclub_calendar import (
    fetch_calendar_events,
    month_range,
)
from runtime import setup_logger

STATE_FILENAME = "onsiteclub_calendar_state.json"
DASHBOARD_FILENAME = "onsiteclub_dashboard_{month}.png"
REPORT_FILENAME = "onsiteclub_report_{month}.md"

# 推送排版参数：未来 N 天新开日程窗口 / 展示条数上限
SCHEDULE_DAYS = ONSITECLUB_SCHEDULE_DAYS
ONGOING_SHOW = 12      # 「正在进行」一列最多展示条数
NEW_PER_DAY_SHOW = 8   # 单日新开最多展示条数

# 早于当前月前 11 个月开场的会展视为历史遗留案例，不计入当月监控
_ARCHIVE_MONTHS_BACK = 11


class OnsiteCalendarError(Exception):
    """监控流程的顶层异常。"""


def load_state(path=None):
    """加载历史状态；文件缺失或损坏时返回空状态（首日场景）。"""
    path = path or default_state_path()
    defaults = {
        "version": 1,
        "month": "",
        "last_run": "",
        "seen_ids": [],
        "history": {},
        "cache": {},
    }
    try:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
        if not isinstance(data, dict):
            return defaults
        for key, value in defaults.items():
            data.setdefault(key, value)
        return data
    except (OSError, json.JSONDecodeError):
        return defaults


def save_state(path, state):
    with open(path, "w", encoding="utf-8") as file:
        json.dump(state, file, ensure_ascii=False, indent=1)


def default_state_path():
    return os.path.join(DATA_DIR, STATE_FILENAME)


def default_dashboard_path(month):
    return os.path.join(REPORT_DIR, DASHBOARD_FILENAME.format(month=month))


def default_report_path(month):
    return os.path.join(REPORT_DIR, REPORT_FILENAME.format(month=month))


def _parse_date(value):
    try:
        return datetime.fromisoformat(str(value)[:19]).date()
    except (TypeError, ValueError):
        return None


def is_archived_event(item, month_first):
    """排除历史遗留案例（开场远早于监控月）与异常日期（结束在 5 年之后）。"""
    start = _parse_date(item.get("start"))
    end = _parse_date(item.get("end"))
    if not start:
        return True
    archive_cutoff = (month_first - timedelta(days=30 * _ARCHIVE_MONTHS_BACK))
    if start < archive_cutoff:
        return True
    if end and end.year > month_first.year + 5:
        return True
    return False


def clean_events(events, month):
    """过滤历史遗留案例，并整理展示用日期字段。"""
    first, last = month_range(*month)
    cleaned = []
    for item in events:
        if is_archived_event(item, first):
            continue
        start = _parse_date(item.get("start")) or first
        end = _parse_date(item.get("end")) or last
        # 长期展的异常结束年只用于展示，不改变归属
        item["start_date"] = start.isoformat()
        item["end_date"] = end.isoformat()
        item["display_start"] = start.strftime("%m-%d")
        item["display_end"] = end.strftime("%m-%d") if end.year <= first.year + 1 else "长期"
        cleaned.append(item)
    return cleaned


def enrich_new_events(events, state, logger=None):
    """为「新增」的会展抓取详情页，并写入缓存；已缓存的直接复用。"""
    cache = state.setdefault("cache", {})
    cache_keys = (
        "title", "start", "end", "url", "type", "city", "brand", "industry",
        "topics", "image_url", "description", "description_source",
    )
    for item in events:
        key = str(item["id"])
        if key in cache:
            item.update(cache[key])
            if cache[key].get("description_source") == "entry_content":
                continue
        from crawlers.onsiteclub_calendar import enrich_event_detail
        candidate = dict(item)
        candidate["description"] = ""
        candidate.pop("description_source", None)
        enriched = enrich_event_detail(candidate)
        cache[key] = {k: enriched.get(k) for k in cache_keys}
        if cache[key].get("description_source") != "entry_content":
            cache[key]["description"] = ""
        item.update(cache[key])
    return events


def diff_new_events(events, state, logger=None):
    """与历史 seen_ids 对比，返回今日新增的会展；首日（无历史）时返回全部。

    Args:
        events: 本次抓取到的会展列表
        state: 状态字典（含 seen_ids）
        logger: 可选日志器；不传则不打印调试日志
    """
    seen = set(state.get("seen_ids", []))
    new_events = [item for item in events if item["id"] not in seen]

    if logger:
        logger.info("[日历监控] diff_new_events：抓取 %s 场，seen_ids 共 %s 条",
                    len(events), len(seen))
        for item in events:
            is_new = item["id"] not in seen
            logger.debug("[日历监控]   %s id=%s title=%s",
                         "新增" if is_new else "已见过", item["id"],
                         str(item.get("title", ""))[:30])
        logger.info("[日历监控] 本次新增 %s 场", len(new_events))

    return new_events


def active_on(events, day):
    """筛选在 day 当天进行的会展。"""
    day_iso = day.isoformat()
    return [
        item for item in events
        if item.get("start_date", "") <= day_iso <= item.get("end_date", "")
    ]


def upcoming_events(events, today, days=3):
    """拆分未来 N 天「新开」与「进行中」的会展。

    返回 (starting, ongoing)：
    - starting：开始日落在 [today, today+days-1] 的新开会展，按开始日升序
    - ongoing：今天之前已开始、今天仍进行的长期会展，按结束日升序
    """
    horizon = today + timedelta(days=days - 1)
    today_iso = today.isoformat()
    starting = sorted(
        [item for item in events if today_iso <= item.get("start_date", "") <= horizon.isoformat()],
        key=lambda x: x.get("start_date", ""),
    )
    ongoing = sorted(
        [item for item in events if item.get("start_date", "") < today_iso <= item.get("end_date", "")],
        key=lambda x: x.get("end_date", ""),
    )
    return starting, ongoing


def _duration_days(item):
    """会展持续天数（含首尾）；日期异常时返回 None。"""
    try:
        start = date.fromisoformat(item.get("start_date") or "")
        end = date.fromisoformat(item.get("end_date") or "")
    except ValueError:
        return None
    return (end - start).days + 1


def _date_span(item):
    """日期区间展示，如 ``08-04~08-10（共7天）``。"""
    span = f"{item.get('display_start', '')}~{item.get('display_end', '')}"
    days = _duration_days(item)
    if days:
        span += f"（共{days}天）"
    return span


def format_event_line(item, with_date=False, detailed=False):
    """会展单行描述（钉钉 markdown 用）。

    - 紧凑模式：``[标题](url)（类型）· 城市``
    - 详细模式：追加日期区间（含天数）、行业、品牌（品牌为「待定」时省略）
    """
    parts = [f"[{item.get('title', '')}]({item.get('url', '')})"]
    if item.get("type"):
        parts.append(f"（{item['type']}）")
    if with_date:
        parts.append(_date_span(item))
    if item.get("city"):
        parts.append(item["city"])
    if detailed:
        if item.get("industry"):
            parts.append(item["industry"])
        brand = (item.get("brand") or "").strip()
        if brand and brand != "待定":
            parts.append(f"品牌 {brand}")
    return " · ".join(parts)


def build_text_report(events, new_events, today=None, month_label="8月", max_new_items=None, days=SCHEDULE_DAYS):
    """生成详细文本报告：今日新增 + 未来 N 天新开日程（按日开始分组）+ 正在进行 + 分布速览。

    - 今日新增：逐场列详细（类型/日期/城市/行业/品牌/链接）。
    - 未来 N 天新开：按开始日期逐日分组，带周几与今日/明日/后天标记，方便按日程查时间。
    - 正在进行：仅一列紧凑展示（按结束日升序，⏰ 标 N 天内将结束的），避免长期展重复刷屏。

    max_new_items 用于钉钉消息控制长度：今日新增超上限时只列前若干条并附省略说明，
    存档到 .md 的完整报告不受影响（调用处不传该参数）。
    """
    today = today or date.today()
    weekday_cn = "一二三四五六日"
    lines = []
    lines.append(f"## 📅 Onsite Club {month_label}会展监控 · {today.month}/{today.day} 周{weekday_cn[today.weekday()]}")
    lines.append(f"**当月会展 {len(events)} 场 · 今日新增 {len(new_events)} 场** · 🔗 [完整日历](https://www.onsiteclub.com/calendar)")
    lines.append("")

    new_ids = {item["id"] for item in new_events}

    # 今日新增
    if new_events:
        lines.append(f"### 🆕 今日新增（{len(new_events)}）")
        shown = 0
        for item in new_events:
            if max_new_items is not None and shown >= max_new_items:
                lines.append(f"- … 其余 {len(new_events) - shown} 场见 feedCard 卡片/完整报告")
                break
            lines.append(f"- {format_event_line(item, with_date=True, detailed=True)}")
            shown += 1
        lines.append("")
    else:
        lines.append("### 🆕 今日新增\n今日无新增会展。\n")

    # 未来 N 天「新开」日程：按开始日期逐日分组
    starting, ongoing = upcoming_events(events, today, days=days)
    horizon = today + timedelta(days=days - 1)
    lines.append(f"### 📌 未来{days}天新开（{today.month}/{today.day} ~ {horizon.month}/{horizon.day}）")
    if starting:
        by_day = {}
        for item in starting:
            by_day.setdefault(item.get("start_date", ""), []).append(item)
        for offset in range(days):
            day = today + timedelta(days=offset)
            items = by_day.get(day.isoformat(), [])
            day_head = f"{day.month}/{day.day} 周{weekday_cn[day.weekday()]}"
            if offset == 0:
                day_head += " · 今日"
            elif offset == 1:
                day_head += " · 明日"
            elif offset == 2:
                day_head += " · 后天"
            lines.append(f"**{day_head}**（{len(items)} 场）")
            if not items:
                lines.append("- 无")
                continue
            for item in items[:NEW_PER_DAY_SHOW]:
                prefix = "🆕 " if item["id"] in new_ids else ""
                lines.append(f"- {prefix}{format_event_line(item, with_date=True, detailed=True)}")
            if len(items) > NEW_PER_DAY_SHOW:
                lines.append(f"- … 该日另有 {len(items) - NEW_PER_DAY_SHOW} 场，见[完整日历](https://www.onsiteclub.com/calendar)")
        lines.append("")
    else:
        lines.append(f"未来{days}天暂无新开会展。\n")

    # 正在进行（今天仍开放）：紧凑一列，按结束日升序，⏰ 标记即将结束
    if ongoing:
        ongoing = sorted(ongoing, key=lambda x: x.get("end_date", "9999"))
        today_iso = today.isoformat()
        horizon_iso = (today + timedelta(days=days - 1)).isoformat()
        ending_soon = {
            item["id"] for item in ongoing
            if today_iso <= item.get("end_date", "") <= horizon_iso
        }
        lines.append(f"### 🔄 正在进行（今日 {len(ongoing)} 场）")
        lines.append(f"> 按结束日排序 · ⏰ = {days}天内结束")
        for item in ongoing[:ONGOING_SHOW]:
            mark = "⏰ " if item["id"] in ending_soon else ""
            base = format_event_line(item)
            end_label = item.get("display_end") or ""
            if end_label:
                base += f" · 至 {end_label}"
            lines.append(f"- {mark}{base}")
        if len(ongoing) > ONGOING_SHOW:
            lines.append(f"- … 其余 {len(ongoing) - ONGOING_SHOW} 场见[完整日历](https://www.onsiteclub.com/calendar)")
        lines.append("")
    else:
        lines.append("### 🔄 正在进行\n今日暂无开放中的会展。\n")

    # 分布速览
    city_count = {}
    type_count = {}
    for item in events:
        city = item.get("city") or "其他"
        city_count[city] = city_count.get(city, 0) + 1
        type_count[item.get("type") or "其他"] = type_count.get(item.get("type") or "其他", 0) + 1

    top_cities = " · ".join(f"{k} {v}" for k, v in sorted(city_count.items(), key=lambda x: -x[1])[:6])
    top_types = " · ".join(f"{k} {v}" for k, v in sorted(type_count.items(), key=lambda x: -x[1])[:6])

    lines.append("### 📊 分布速览")
    lines.append(f"- 城市 Top：{top_cities}")
    lines.append(f"- 类型 Top：{top_types}")
    lines.append("")
    lines.append(f"> 共 {len(events)} 场 · 覆盖 {len(city_count)} 城 · 涉及 {len(type_count)} 类")
    return "\n".join(lines)


def run(month=None, push_callback=None, no_enrich=False, state_path=None, logger=None):
    """执行一次完整监控：抓取→去重→文本→看板→推送→存状态。

    Args:
        month: (year, month) 元组，默认当前月。
        push_callback: callable(text_report, dashboard_png_path, new_events) -> None
        no_enrich: 跳过详情页抓取（测试用）。
    Returns:
        dict: 本次运行摘要（counts / 产物路径）。
    """
    today = date.today()
    month = month or (today.year, today.month)
    year, mon = month
    month_label = f"{mon}月"
    first, _last = month_range(year, mon)

    logger = logger or setup_logger(LOG_DIR)[0]
    state = load_state(state_path)
    state_path = state_path or default_state_path()

    logger.info("[会展监控] 抓取 %s 月会展", month_label)
    raw_events = fetch_calendar_events(year, mon)
    events = clean_events(raw_events, month)
    logger.info("[会展监控] 抓取 %s 场，有效 %s 场", len(raw_events), len(events))

    if not events:
        raise OnsiteCalendarError("未抓到任何会展，请检查接口或网络")

    if not no_enrich:
        enrich_new_events(events, state, logger=logger)

    new_events = diff_new_events(events, state)
    first_run = not state.get("seen_ids")
    if first_run:
        logger.info("[会展监控] 首次运行，今日新增显示全部 %s 场", len(new_events))

    report = build_text_report(events, new_events, today=today, month_label=month_label)
    logger.info("[会展监控] 文本报告生成，新增 %s 场", len(new_events))

    os.makedirs(REPORT_DIR, exist_ok=True)
    report_path = default_report_path(f"{year:04d}-{mon:02d}")
    with open(report_path, "w", encoding="utf-8") as file:
        file.write(report)

    # 看板图片：主流程固定步骤，生成失败不中断后续
    dashboard_path = default_dashboard_path(f"{year:04d}-{mon:02d}")
    chart_paths = {}
    try:
        from dashboard_img import build_dashboard, individual_chart_paths, load_china_map
        map_data = load_china_map()
        build_dashboard(
            events, new_events, today=today, output_path=dashboard_path,
            month_label=month_label, map_data=map_data,
        )
        chart_paths = individual_chart_paths(dashboard_path, f"{year:04d}-{mon:02d}")
        logger.info("[会展监控] 看板图片生成：%s", dashboard_path)
        logger.info("[会展监控] 独立图表生成：%s", ", ".join(chart_paths.values()))
    except Exception as exc:
        logger.warning("[会展监控] 看板图片生成失败：%s", exc)

    if push_callback:
        push_callback(report, dashboard_path, new_events, events, month_label=month_label)

    # 更新状态：记录 seen_ids、今日新增历史、缓存与运行时间
    seen = set(state.get("seen_ids", []))
    for item in events:
        seen.add(item["id"])
    state["seen_ids"] = sorted(seen)
    state["history"].setdefault(today.isoformat(), []).extend(item["id"] for item in new_events)
    state["month"] = f"{year:04d}-{mon:02d}"
    state["last_run"] = datetime.now().isoformat(timespec="seconds")
    os.makedirs(DATA_DIR, exist_ok=True)
    save_state(state_path, state)
    logger.info("[会展监控] 状态已保存：%s", state_path)

    return {
        "month": f"{year:04d}-{mon:02d}",
        "total": len(events),
        "new": len(new_events),
        "first_run": first_run,
        "report_path": report_path,
        "dashboard_path": dashboard_path,
        "chart_paths": chart_paths,
        "state_path": state_path,
    }
