"""DD日推 · Onsite Club 会展「极简文本」报告生成。

按钉钉消息排版生成报告，**进行中会展按「距结束天数」分四档**：
1. 标题行：当月会展 / 今日新增 / 完整日历链接
2. 🆕 今日新增（全部罗列；END_URGENT 天内结束的附 ⏰）
3. ⏳ 剩 END_URGENT 天内将结束（全部罗列，按剩X天分组）
4. ⏳ 剩 END_NEAR 天内将结束（全部罗列，按剩X天分组）
5. ⏳ 剩 END_FAR 天内将结束（全部罗列，按剩X天分组）
6. 🔄 长期进行中（剩 END_FAR 天以上，**按结束月份细分**，每月最多 LONG_CAP 条）
7. 📅 未来N天开幕（按开始日分组，全部罗列）
8. 🔚 已结束（按结束日分组，全部罗列）
9. 📊 分布速览（城市 / 类型 / 高频词 / 总数）

阈值与截断上限可在 .env 配置：
ONSITECLUB_END_URGENT / ONSITECLUB_END_NEAR / ONSITECLUB_END_FAR / ONSITECLUB_LONG_CAP。
"""
from datetime import date, timedelta

from config import (
    ONSITECLUB_END_FAR,
    ONSITECLUB_END_NEAR,
    ONSITECLUB_END_URGENT,
    ONSITECLUB_LONG_CAP,
)
from onsite_monitor import format_event_line

CALENDAR_URL = "https://www.onsiteclub.com/calendar"

# 类型简写：完整分类 → 展示用短标签（文本分布与饼图共用，保持图文一致）
TYPE_SHORT = {
    "其他/快闪展": "快闪展",
    "主题店/限时店": "限时店",
    "慢闪空间": "慢闪",
    "发布会/首映": "发布会",
    "市集/嘉年华": "市集",
    "秀场/演出": "秀场",
}


def short_type(name):
    """完整分类名 → 展示用短标签；未映射则原样返回。"""
    return TYPE_SHORT.get(str(name or ""), name or "")


def _days_left(item, today):
    """会展结束日距今天数；结束日异常时返回 None。"""
    try:
        end = date.fromisoformat(str(item.get("end_date") or ""))
    except ValueError:
        return None
    return (end - today).days


def _grouped_line(item):
    """分组列表用紧凑行：``[标题](url) · 城市 · 至MM-DD``（类型已在分组头）。"""
    parts = [f"[{item.get('title', '')}]({item.get('url', '')})"]
    if item.get("city"):
        parts.append(item["city"])
    if item.get("display_end"):
        parts.append(f"至{item['display_end']}")
    return " · ".join(parts)


def _high_freq_words(events, top=12):
    """会展标题高频词文本（复用 dashboard_img 分词，已过滤类型词/文案词）。"""
    from dashboard_img import _tokenize_titles
    words = _tokenize_titles(events)
    return " · ".join(word for word, _freq in words[:top])


def _day_groups(items, today, weekday_cn):
    """把会展按结束日距今天数分组，返回 [(剩天数, [items...])]，剩天数升序。"""
    groups = {}
    for item in items:
        left = _days_left(item, today)
        groups.setdefault(left, []).append(item)
    return [(left, groups[left]) for left in sorted(groups)]


def _append_day_group(lines, items, today, weekday_cn, head_by_left):
    """按剩余天数分组输出；head_by_left(left) 生成分组头。"""
    for left, grouped_items in _day_groups(items, today, weekday_cn):
        lines.append(head_by_left(left, len(grouped_items)))
        for item in grouped_items:
            lines.append(f"  - {_grouped_line(item)}")


def build_dd_report(events, new_events, today=None, days=7, month_label="8月"):
    """生成极简文本报告：所有会展按状态罗列，进行中分三段、长期段截断。"""
    today = today or date.today()
    weekday_cn = "一二三四五六日"
    horizon = today + timedelta(days=days - 1)
    today_iso, horizon_iso = today.isoformat(), horizon.isoformat()
    new_ids = {item["id"] for item in new_events}
    end_urgent, end_near, end_far = ONSITECLUB_END_URGENT, ONSITECLUB_END_NEAR, ONSITECLUB_END_FAR
    long_cap = ONSITECLUB_LONG_CAP

    # 分区（每场只出现一次）
    ongoing = [
        item for item in events
        if item.get("start_date", "") <= today_iso <= item.get("end_date", "")
        and item["id"] not in new_ids
    ]
    starting = sorted(
        [item for item in events if today_iso <= item.get("start_date", "") <= horizon_iso
         and item["id"] not in new_ids],
        key=lambda x: x.get("start_date", ""),
    )
    ended = sorted(
        [item for item in events if item.get("end_date", "") < today_iso],
        key=lambda x: x.get("end_date", ""), reverse=True,
    )
    tier1 = sorted([item for item in ongoing if _days_left(item, today) <= end_urgent],
                   key=lambda x: x.get("end_date", ""))
    tier2 = sorted([item for item in ongoing if end_urgent < _days_left(item, today) <= end_near],
                   key=lambda x: x.get("end_date", ""))
    tier3 = sorted([item for item in ongoing if end_near < _days_left(item, today) <= end_far],
                   key=lambda x: x.get("end_date", ""))
    tier4 = sorted([item for item in ongoing if _days_left(item, today) > end_far],
                   key=lambda x: x.get("end_date", ""))

    lines = []
    # 1. 标题行
    lines.append(f"📅 Onsite Club {month_label}会展 · {today.month}/{today.day} 周{weekday_cn[today.weekday()]}")
    lines.append(f"当月 **{len(events)}** 场 · 今日新增 **{len(new_events)}** 场 · 🔗 [完整日历]({CALENDAR_URL})")
    lines.append("")

    # 2. 今日新增（全部罗列；10天内结束附 ⏰）
    if new_events:
        lines.append(f"### 🆕 今日新增（{len(new_events)} 场）")
        for item in new_events:
            left = _days_left(item, today)
            flag = " ⏰" if left is not None and left <= end_urgent else ""
            lines.append(f"- {format_event_line(item, with_date=True, detailed=True)}{flag}")
    else:
        lines.append("### 🆕 今日新增\n今日无新增会展。")
    lines.append("")

    # 3. 剩 END_URGENT 天内将结束（全部罗列）
    if tier1:
        lines.append(f"### ⏳ {end_urgent}天内将结束（{len(tier1)} 场）")
        lines.append("> 按剩余天数分组 · 剩0天=今天结束，请立刻关注")
        _append_day_group(
            lines, tier1, today, weekday_cn,
            head_by_left=lambda left, n:
            f"▸ 剩**0**天 · 今天结束（{n} 场）" if left == 0 else f"▸ 剩**{left}**天（{n} 场）",
        )
    else:
        lines.append(f"### ⏳ {end_urgent}天内将结束\n无。")
    lines.append("")

    # 4. 剩 END_NEAR 天内将结束（全部罗列）
    if tier2:
        lines.append(f"### ⏳ {end_urgent + 1}-{end_near}天将结束（{len(tier2)} 场）")
        lines.append("> 按剩余天数分组")
        _append_day_group(
            lines, tier2, today, weekday_cn,
            head_by_left=lambda left, n: f"▸ 剩**{left}**天（{n} 场）",
        )
    else:
        lines.append(f"### ⏳ {end_urgent + 1}-{end_near}天将结束\n无。")
    lines.append("")

    # 5. 剩 END_NEAR+1 ~ END_FAR 天将结束（全部罗列）
    if tier3:
        lines.append(f"### ⏳ {end_near + 1}-{end_far}天将结束（{len(tier3)} 场）")
        lines.append("> 按剩余天数分组")
        _append_day_group(
            lines, tier3, today, weekday_cn,
            head_by_left=lambda left, n: f"▸ 剩**{left}**天（{n} 场）",
        )
    else:
        lines.append(f"### ⏳ {end_near + 1}-{end_far}天将结束\n无。")
    lines.append("")

    # 6. 长期进行中（>END_FAR 天）：按结束月份细分，每月展示上限 LONG_CAP
    if tier4:
        lines.append(f"### 🔄 长期进行中（剩{end_far}天以上 · 共 {len(tier4)} 场）")
        lines.append(f"> 按结束月份细分 · 每月展示前 {long_cap} 场")
        by_month = {}
        for item in tier4:
            by_month.setdefault(item.get("end_date", "")[:7], []).append(item)
        for ym in sorted(by_month):
            items = by_month[ym]
            year, month = ym.split("-")
            label = f"{int(month)}月" if int(year) == today.year else f"{int(year)}年{int(month)}月"
            lines.append(f"▸ {label}结束（{len(items)} 场）")
            for item in items[:long_cap]:
                lines.append(f"  - {_grouped_line(item)}")
            if len(items) > long_cap:
                lines.append(f"  - … 该月其余 {len(items) - long_cap} 场见[完整日历]({CALENDAR_URL})")
    else:
        lines.append(f"### 🔄 长期进行中\n无。")
    lines.append("")

    # 7. 未来N天开幕（按开始日分组，全部罗列）
    if starting:
        lines.append(f"### 📅 未来{days}天开幕（{len(starting)} 场）")
        lines.append("> 按开始日分组")
        by_day = {}
        for item in starting:
            by_day.setdefault(item.get("start_date", ""), []).append(item)
        for day_iso in sorted(by_day):
            items = by_day[day_iso]
            d = date.fromisoformat(day_iso)
            head = f"▸ {d.month}/{d.day} 周{weekday_cn[d.weekday()]}（{len(items)} 场）"
            lines.append(head)
            for item in items:
                lines.append(f"  - {_grouped_line(item)}")
    else:
        lines.append(f"### 📅 未来{days}天开幕\n窗口内暂无。")
    lines.append("")

    # 8. 已结束（按结束日分组，全部罗列）
    if ended:
        lines.append(f"### 🔚 已结束（{len(ended)} 场）")
        lines.append("> 按结束日分组 · 最近结束在前")
        by_day = {}
        for item in ended:
            by_day.setdefault(item.get("end_date", ""), []).append(item)
        for day_iso in sorted(by_day, reverse=True):
            items = by_day[day_iso]
            d = date.fromisoformat(day_iso)
            head = f"▸ {d.month}/{d.day} 周{weekday_cn[d.weekday()]}（{len(items)} 场）"
            lines.append(head)
            for item in items:
                lines.append(f"  - {_grouped_line(item)}")
    else:
        lines.append("### 🔚 已结束\n无。")
    lines.append("")

    # 9. 分布速览
    city_count, type_count = {}, {}
    for item in events:
        city = item.get("city") or "其他"
        city_count[city] = city_count.get(city, 0) + 1
        t = item.get("type") or "其他"
        type_count[t] = type_count.get(t, 0) + 1

    top_cities = " · ".join(
        f"{name} {count}" for name, count in
        sorted(city_count.items(), key=lambda x: -x[1])[:6]
    )
    top_types = " · ".join(
        f"{short_type(name)} {count}" for name, count in
        sorted(type_count.items(), key=lambda x: -x[1])[:6]
    )

    lines.append("### 📊 分布")
    lines.append(f"🏙️ {top_cities or '暂无'}")
    lines.append(f"🏷️ {top_types or '暂无'}")
    lines.append(f"🔑 高频词：{_high_freq_words(events)}")
    lines.append("")
    lines.append(f"共 **{len(events)}** 场 · {len(city_count)}城 · {len(type_count)}类")
    return "\n".join(lines)
