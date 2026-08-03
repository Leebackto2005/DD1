"""DD日推 · Onsite Club 会展日历监控主流程。

每日 9:00 流程：
1. 抓取当月全部会展（含跨月长期展）。
2. 与本地历史记录对比，识别「今日新增」（首日显示全部）。
3. 生成两个产物：一段极简文本（新增列表 + 未来N天日程 + 链接）、一张看板图片（折线/标注饼图/关键指标）。
4. 推送文本与图片到钉钉，同事直接在群里阅读。

历史状态复用 onsite_monitor 的 data/onsiteclub_calendar_state.json：
- seen_ids：历史出现过的会展 id（识别新增）
- cache：会展详情缓存（避免重复抓详情页）
- history：每日新增记录
"""
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime

from config import DATA_DIR, LOG_DIR, ONSITECLUB_SCHEDULE_DAYS, REPORT_DIR
from crawlers.onsiteclub_calendar import fetch_calendar_events, month_range
from runtime import setup_logger

from dd_report import build_dd_report
from onsite_monitor import (
    clean_events,
    diff_new_events,
    load_state,
    save_state,
)

# 详情页抓取并发数（首日需抓全部新增会展的详情）
ENRICH_WORKERS = 6


def enrich_new_events(events, state, max_workers=ENRICH_WORKERS):
    """并发为「新增」的会展抓取详情页并写入缓存；已缓存的直接复用。

    与 onsite_monitor.enrich_new_events 等价，但用线程池并发，避免首日逐条耗时。
    """
    cache = state.setdefault("cache", {})
    from crawlers.onsiteclub_calendar import enrich_event_detail

    CACHE_KEYS = ("title", "start", "end", "url", "type", "city", "brand", "industry", "topics", "image_url")

    def _enrich(item):
        key = str(item["id"])
        if key in cache:
            item.update(cache[key])
            return
        enriched = enrich_event_detail(dict(item))
        cache[key] = {k: enriched.get(k) for k in CACHE_KEYS}
        item.update(cache[key])

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        list(executor.map(_enrich, events))
    return events

REPORT_FILENAME = "dd_report_{month}.md"
DASHBOARD_FILENAME = "dd_dashboard_{month}.png"


def run(month=None, push_callback=None, no_enrich=False, state_path=None, logger=None):
    """执行一次完整监控：抓取→去重→文本→看板→推送→存状态。

    Args:
        month: (year, month) 元组，默认当前月。
        push_callback: callable(report_text, dashboard_png_path, new_events, events) -> None
        no_enrich: 跳过详情页抓取（测试用）。
    Returns:
        dict: 本次运行摘要（counts / 产物路径）。
    """
    today = date.today()
    month = month or (today.year, today.month)
    year, mon = month
    month_label = f"{mon}月"

    logger = logger or setup_logger(LOG_DIR)[0]
    state = load_state(state_path)
    state_path = state_path or os.path.join(DATA_DIR, "onsiteclub_calendar_state.json")

    logger.info("[DD日推] 抓取 %s 月会展", month_label)
    raw_events = fetch_calendar_events(year, mon)
    events = clean_events(raw_events, month)
    logger.info("[DD日推] 抓取 %s 场，有效 %s 场", len(raw_events), len(events))

    if not events:
        raise RuntimeError("未抓到任何会展，请检查接口或网络")

    if not no_enrich:
        enrich_new_events(events, state)

    new_events = diff_new_events(events, state)
    first_run = not state.get("seen_ids")
    if first_run:
        logger.info("[DD日推] 首次运行，今日新增显示全部 %s 场", len(new_events))

    report = build_dd_report(
        events, new_events, today=today, month_label=month_label,
        days=ONSITECLUB_SCHEDULE_DAYS,
    )
    logger.info("[DD日推] 文本报告生成，新增 %s 场", len(new_events))

    os.makedirs(REPORT_DIR, exist_ok=True)
    report_path = os.path.join(REPORT_DIR, REPORT_FILENAME.format(month=f"{year:04d}-{mon:02d}"))
    with open(report_path, "w", encoding="utf-8") as file:
        file.write(report)

    dashboard_path = os.path.join(REPORT_DIR, DASHBOARD_FILENAME.format(month=f"{year:04d}-{mon:02d}"))
    try:
        from dd_dashboard import build_dd_dashboard
        build_dd_dashboard(events, new_events, today=today, output_path=dashboard_path,
                           month_label=month_label)
        logger.info("[DD日推] 看板图片生成：%s", dashboard_path)
    except Exception as exc:
        logger.warning("[DD日推] 看板图片生成失败：%s", exc)

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
    logger.info("[DD日推] 状态已保存：%s", state_path)

    return {
        "month": f"{year:04d}-{mon:02d}",
        "total": len(events),
        "new": len(new_events),
        "first_run": first_run,
        "report_path": report_path,
        "dashboard_path": dashboard_path,
        "state_path": state_path,
    }
