"""Onsite Club 分类案例库监控主流程（独立于日历监控的管道）。

每日流程：
1. 抓取 /category 前 N 页案例（按最新排序，今日新增必在前几页）。
2. 与本地 seen_ids（slug）对比，识别「今日新增」（首日 = 全部）。
3. 为新增案例抓详情页补全城市/品牌/日期/封面。
4. 生成一段精短 markdown 文本，推送到钉钉（纯文字，无看板图）。

状态存于 data/onsiteclub_category_state.json（与日历状态隔离）：
- seen_ids：历史出现过的 slug
- history：每日新增 slug 记录
- cache：详情页缓存
"""
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime

from config import DATA_DIR, LOG_DIR, REPORT_DIR
from crawlers.onsiteclub_category import (
    enrich_cases,
    fetch_category_cases,
)
from runtime import setup_logger

STATE_FILENAME = "onsiteclub_category_state.json"
REPORT_FILENAME = "category_report_{date}.md"

# 默认抓取前 3 页（约 60 条），足以覆盖单日新增
DEFAULT_MAX_PAGES = 3
# 推送时今日新增最多展示条数（精短文本）
MAX_NEW_SHOW = 20
ENRICH_WORKERS = 6

CACHE_KEYS = ("title", "url", "type", "city", "brand", "industry", "start", "end", "image_url")


def default_state_path():
    return os.path.join(DATA_DIR, STATE_FILENAME)


def load_state(path=None):
    """加载历史状态；文件缺失或损坏时返回空状态（首日场景）。"""
    path = path or default_state_path()
    defaults = {
        "version": 1,
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


def diff_new_cases(cases, state):
    """与历史 seen_ids 对比，返回今日新增；首日（无历史）返回全部。"""
    seen = set(state.get("seen_ids", []))
    return [item for item in cases if item["slug"] not in seen]


def _enrich_new_cases(cases, state, max_workers=ENRICH_WORKERS):
    """并发为新增案例抓详情页并写缓存；已缓存的直接复用。"""
    cache = state.setdefault("cache", {})
    targets = []
    for item in cases:
        key = item["slug"]
        if key in cache:
            item.update(cache[key])
        else:
            targets.append(item)

    if not targets:
        return cases

    def _enrich(item):
        from crawlers.onsiteclub_category import enrich_case_detail
        enriched = enrich_case_detail(dict(item))
        cache[item["slug"]] = {k: enriched.get(k) for k in CACHE_KEYS}
        item.update(cache[item["slug"]])

    with ThreadPoolExecutor(max_workers=min(max_workers, len(targets))) as executor:
        list(executor.map(_enrich, targets))
    return cases


def _date_span(item):
    """日期区间展示，如 08-01~08-10；无日期返回空串。"""
    start = str(item.get("start") or "")
    end = str(item.get("end") or "")
    if not start:
        return ""
    fmt = lambda d: d[5:].replace("-", "-") if len(d) >= 10 else d  # 2026-08-01 → 08-01
    if end and end != start:
        return f"{fmt(start)}~{fmt(end)}"
    return fmt(start)


def build_category_report(new_cases, today=None, max_show=MAX_NEW_SHOW):
    """生成精短 markdown 文本：今日新增案例列表（标题+城市+日期+链接）。

    钉钉安全关键词「会展」已包含在标题行，确保消息送达。
    """
    today = today or date.today()
    weekday_cn = "一二三四五六日"
    lines = [
        f"## 会展｜Onsite Club 今日新增案例 · {today.month}/{today.day} 周{weekday_cn[today.weekday()]}",
        f"**今日新增 {len(new_cases)} 条** · 🔗 [完整列表](https://www.onsiteclub.com/category)",
        "",
    ]
    if not new_cases:
        lines.append("今日暂无新增案例。")
        return "\n".join(lines)

    shown = 0
    for idx, item in enumerate(new_cases, 1):
        if shown >= max_show:
            lines.append(f"\n… 其余 {len(new_cases) - shown} 条见 [完整列表](https://www.onsiteclub.com/category)")
            break
        title = item.get("title") or "未命名"
        url = item.get("url", "")
        parts = [f"{idx}. [{title}]({url})"]
        meta = []
        if item.get("city"):
            meta.append(item["city"])
        if item.get("brand") and item["brand"] != "待定":
            meta.append(item["brand"])
        span = _date_span(item)
        if span:
            meta.append(span)
        if meta:
            parts.append(" · ".join(meta))
        lines.append(" · ".join(parts))
        shown += 1

    lines.append("")
    lines.append("> 数据源 onsiteclub.com/category")
    return "\n".join(lines)


def init_baseline(baseline_slug, max_pages=DEFAULT_MAX_PAGES, state_path=None, logger=None):
    """以指定 slug 为基准初始化状态：基准及其之后（更旧）的所有 slug 标记为已见过。

    分类页默认按最新排序，排在基准之前（更新）的案例下次 run() 时会算作「今日新增」。
    用于首次部署时设定起点，避免首日把全部案例都报为新增。

    例如基准为王鹤棣（WHD-D-LAND-...），则王鹤棣及其之后的全部写入 seen_ids，
    下次运行只有排在王鹤棣前面的香奈儿（Chanel-X-le19M-...）会被报为新增。
    """
    logger = logger or setup_logger(LOG_DIR)[0]
    state = load_state(state_path)
    state_path = state_path or default_state_path()

    logger.info("[分类监控] 初始化基准 slug=%s（抓取前 %s 页）", baseline_slug, max_pages)
    cases = fetch_category_cases(max_pages=max_pages)
    if not cases:
        raise RuntimeError("未抓到任何案例，无法初始化基准")

    slugs = [c["slug"] for c in cases]
    try:
        baseline_idx = slugs.index(baseline_slug)
    except ValueError:
        raise RuntimeError(
            f"未在抓取结果中找到基准 slug: {baseline_slug}"
            f"（请确认 slug 正确，或用 --max-pages 增大抓取页数）"
        )

    # 基准及其之后（更旧）的所有 slug 标记为已见过
    seen = set(slugs[baseline_idx:])
    state["seen_ids"] = sorted(seen)
    state["last_run"] = datetime.now().isoformat(timespec="seconds")
    os.makedirs(DATA_DIR, exist_ok=True)
    save_state(state_path, state)

    logger.info(
        "[分类监控] 基准初始化完成：%s（第 %s 条）及之后共 %s 条标记为已见过",
        baseline_slug, baseline_idx + 1, len(seen),
    )
    logger.info(
        "[分类监控] 下次运行将把排在基准之前的 %s 条报为新增",
        baseline_idx,
    )

    return {
        "baseline_slug": baseline_slug,
        "baseline_index": baseline_idx,
        "total_seen": len(seen),
        "will_be_new": baseline_idx,
        "state_path": state_path,
    }


def run(push_callback=None, no_enrich=False, max_pages=DEFAULT_MAX_PAGES,
        state_path=None, logger=None):
    """执行一次分类案例监控：抓取→去重→enrich→文本→推送→存状态。

    Args:
        push_callback: callable(report_text, new_cases) -> None；为 None 不推送。
        no_enrich: 跳过详情页抓取（测试用）。
        max_pages: 抓取前 N 页（默认 3）。
    Returns:
        dict: 运行摘要（counts / 产物路径）。
    """
    today = date.today()
    logger = logger or setup_logger(LOG_DIR)[0]
    state = load_state(state_path)
    state_path = state_path or default_state_path()

    logger.info("[分类监控] 抓取 /category 前 %s 页", max_pages)
    cases = fetch_category_cases(max_pages=max_pages)
    logger.info("[分类监控] 抓取 %s 条案例", len(cases))

    if not cases:
        raise RuntimeError("未抓到任何案例，请检查网络或页面结构")

    if not no_enrich:
        _enrich_new_cases(cases, state)

    new_cases = diff_new_cases(cases, state)
    first_run = not state.get("seen_ids")
    if first_run:
        logger.info("[分类监控] 首次运行，今日新增显示全部 %s 条", len(new_cases))

    report = build_category_report(new_cases, today=today)
    logger.info("[分类监控] 文本报告生成，新增 %s 条", len(new_cases))

    os.makedirs(REPORT_DIR, exist_ok=True)
    report_path = os.path.join(REPORT_DIR, REPORT_FILENAME.format(date=today.isoformat()))
    with open(report_path, "w", encoding="utf-8") as file:
        file.write(report)

    if push_callback:
        push_callback(report, new_cases)

    # 更新状态
    seen = set(state.get("seen_ids", []))
    for item in cases:
        seen.add(item["slug"])
    state["seen_ids"] = sorted(seen)
    state["history"].setdefault(today.isoformat(), []).extend(item["slug"] for item in new_cases)
    state["last_run"] = datetime.now().isoformat(timespec="seconds")
    os.makedirs(DATA_DIR, exist_ok=True)
    save_state(state_path, state)
    logger.info("[分类监控] 状态已保存：%s", state_path)

    return {
        "total": len(cases),
        "new": len(new_cases),
        "first_run": first_run,
        "report_path": report_path,
        "state_path": state_path,
    }
