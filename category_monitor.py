"""Onsite Club 分类案例库监控（已并入 dd_monitor 主流程，作为库被调用）。

提供分类案例的抓取、去重、enrich、基准初始化等功能：
- dd_monitor.run() 调用本模块的 diff_new_cases / _enrich_new_cases 等函数，
  将分类案例新增并入日历报告的「今日新增」部分合并展示。
- dd_main.py --init-category 调用 init_baseline 设定基准。

状态存于 data/onsiteclub_category_state.json（与日历状态隔离）：
- seen_ids：历史出现过的 slug
- history：每日新增 slug 记录
- cache：详情页缓存
"""
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from config import DATA_DIR, LOG_DIR
from crawlers.onsiteclub_category import fetch_category_cases
from runtime import setup_logger

STATE_FILENAME = "onsiteclub_category_state.json"

# 默认抓取前 3 页（约 60 条），足以覆盖单日新增
DEFAULT_MAX_PAGES = 3
ENRICH_WORKERS = 6

CACHE_KEYS = (
    "title", "url", "type", "city", "brand", "industry", "start", "end",
    "image_url", "description", "description_source",
)


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


def diff_new_cases(cases, state, logger=None):
    """与历史 seen_ids 对比，返回今日新增；首日（无历史）返回全部。

    Args:
        cases: 本次抓取到的案例列表
        state: 状态字典（含 seen_ids）
        logger: 可选日志器；不传则不打印调试日志
    """
    seen = set(state.get("seen_ids", []))
    new_cases = [item for item in cases if item["slug"] not in seen]

    if logger:
        logger.info("[分类监控] diff_new_cases：抓取 %s 条，seen_ids 共 %s 条",
                    len(cases), len(seen))
        for item in cases:
            is_new = item["slug"] not in seen
            logger.debug("[分类监控]   %s slug=%s",
                         "新增" if is_new else "已见过", item["slug"])
        logger.info("[分类监控] 本次新增 %s 条", len(new_cases))

    return new_cases


def _enrich_new_cases(cases, state, max_workers=ENRICH_WORKERS):
    """并发为新增案例抓详情页并写缓存；已缓存的直接复用。

    无正文来源标记的旧缓存会强制补抓一次，淘汰历史 SEO meta 简介。
    """
    cache = state.setdefault("cache", {})
    targets = []
    for item in cases:
        key = item["slug"]
        if key in cache:
            item.update(cache[key])
            if cache[key].get("description_source") != "entry_content":
                item["description"] = ""
                item.pop("description_source", None)
                targets.append(item)
        else:
            targets.append(item)

    if not targets:
        return cases

    def _enrich(item):
        from crawlers.onsiteclub_category import enrich_case_detail
        try:
            enriched = enrich_case_detail(dict(item))
            cache[item["slug"]] = {k: enriched.get(k) for k in CACHE_KEYS}
            if cache[item["slug"]].get("description_source") != "entry_content":
                cache[item["slug"]]["description"] = ""
        except Exception:
            # 抓取失败时标记 description_source="failed"，避免每次重抓陷入死循环
            cache[item["slug"]] = {**{k: item.get(k, "") for k in CACHE_KEYS},
                                   "description": "", "description_source": "failed"}
        item.update(cache[item["slug"]])

    with ThreadPoolExecutor(max_workers=min(max_workers, len(targets))) as executor:
        list(executor.map(_enrich, targets))
    return cases


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
