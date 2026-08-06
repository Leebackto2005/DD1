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

from config import DATA_DIR, IMGBB_API_KEY, LOG_DIR, ONSITECLUB_SCHEDULE_DAYS, REPORT_DIR
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

    CACHE_KEYS = (
        "title", "start", "end", "url", "type", "city", "brand", "industry",
        "topics", "image_url", "description", "description_source",
    )

    def _refresh_description(item):
        candidate = dict(item)
        candidate["description"] = ""
        candidate.pop("description_source", None)
        enriched = enrich_event_detail(candidate)
        record = {k: enriched.get(k) for k in CACHE_KEYS}
        if record.get("description_source") != "entry_content":
            record["description"] = ""
        return record

    def _enrich(item):
        key = str(item["id"])
        if key in cache:
            item.update(cache[key])
            # 无正文来源标记的旧缓存可能来自 SEO meta，强制迁移一次。
            # 迁移失败时标记 description_source="failed"，避免每次重抓陷入死循环。
            if cache[key].get("description_source") != "entry_content":
                try:
                    cache[key] = _refresh_description(item)
                    item.update(cache[key])
                except Exception:
                    cache[key] = {**{k: item.get(k, "") for k in CACHE_KEYS},
                                  "description": "", "description_source": "failed"}
                    item["description"] = ""
                    item["description_source"] = "failed"
            return
        enriched = enrich_event_detail(dict(item))
        cache[key] = {k: enriched.get(k) for k in CACHE_KEYS}
        if cache[key].get("description_source") != "entry_content":
            cache[key]["description"] = ""
        item.update(cache[key])

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        list(executor.map(_enrich, events))
    return events

REPORT_FILENAME = "dd_report_{month}.md"
DASHBOARD_FILENAME = "dd_dashboard_{month}.png"


def run(month=None, push_callback=None, no_enrich=False, state_path=None, logger=None,
        digest=False, actioncard_callback=None, max_pages=None, oto_callback=None):
    """执行一次完整监控：抓取→去重→文本→看板→推送→存状态。

    推送策略：
    - 优先 oto_callback：企业机器人推送（优先发到机器人所在群，未配群 ID 时发收件人单聊；
      把新增分类案例逐条推成 actionCard 卡片），忽略 digest/actionCard。
    - 否则默认（digest=False）：逐条 actionCard 推送（actioncard_callback(new_items, h5_url=h5_url)），
      合并报告仅本地归档；new_items = new_events + new_cases；h5_url 为 H5 详情页 jsDelivr URL
      （上传失败为 None，actionCard 回退原详情页）。
    - digest=True：恢复合并 markdown 报告推送（push_callback(report, dashboard_path,
      new_events, events, month_label=...)）。

    Args:
        month: (year, month) 元组，默认当前月。
        push_callback: callable(report_text, dashboard_png_path, new_events, events, month_label="") -> None
            仅 digest=True 时调用。
        no_enrich: 跳过详情页抓取（测试用）。
        digest: True 时走合并报告推送；False 时走逐条 actionCard 推送。
        actioncard_callback: callable(new_items, h5_url=None) -> None，仅 digest=False 时调用。
        oto_callback: callable(report_text, dashboard_png_path, month_label="", new_cases=None) -> bool，
            企业机器人推送（有配置时优先于此两分支）；优先发到机器人所在群（DINGTALK_GROUP_ID），
            未配群 ID 回退收件人单聊。回调自行决定推什么（当前实现把新增分类案例逐条推成
            actionCard 卡片，无新增发「今日无新增」卡片）。
        max_pages: 分类案例抓取页数，None 时用 category_monitor.DEFAULT_MAX_PAGES。
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
    try:
        raw_events = fetch_calendar_events(year, mon)
    except Exception as exc:
        # fetch_with_retry 重试失败会抛 FetchError，明确告警而非静默返回空
        logger.error("[DD日推] 日历抓取失败（网络/网站异常）: %s", exc)
        raise RuntimeError(f"日历抓取失败: {exc}") from exc
    events = clean_events(raw_events, month)
    logger.info("[DD日推] 抓取 %s 场，有效 %s 场", len(raw_events), len(events))

    if not events:
        logger.warning("[DD日推] 日历抓取 0 场（网站可能当月无会展数据）")
        raise RuntimeError("未抓到任何会展，请检查接口或网络")

    if not no_enrich:
        enrich_new_events(events, state)

    new_events = diff_new_events(events, state, logger=logger)
    first_run = not state.get("seen_ids")
    if first_run:
        logger.info("[DD日推] 首次运行，今日新增显示全部 %s 场", len(new_events))

    # 分类案例监控（并入主流程，与日历新增合并展示）
    # 注意：分类状态延后到 run() 末尾统一保存，避免后续步骤失败导致 seen_ids 提前写入、新增漏报
    new_cases = []
    cat_state = None
    cat_state_path = None
    try:
        from category_monitor import (
            DEFAULT_MAX_PAGES,
            default_state_path as _cat_state_path,
            diff_new_cases,
            load_state as load_cat_state,
            _enrich_new_cases,
        )
        from crawlers.onsiteclub_category import fetch_category_cases

        cat_state_path = _cat_state_path()
        cat_state = load_cat_state(cat_state_path)
        logger.info("[DD日推] 抓取 /category 分类案例")
        try:
            cases = fetch_category_cases(max_pages=max_pages or DEFAULT_MAX_PAGES)
        except Exception as exc:
            # fetch_with_retry 重试失败会抛 FetchError，明确告警而非静默跳过
            logger.error("[DD日推] 分类案例抓取失败（网络/网站异常）: %s", exc)
            cases = []
        logger.info("[DD日推] 分类案例抓取 %s 条", len(cases))
        if not cases:
            # 区分两种情况：FetchError 异常已上面 log error；这里是「网站确实无新案例」
            logger.info("[DD日推] 分类案例抓取 0 条（网站可能无新案例）；本次跳过新增判定")
        if cases:
            _enrich_new_cases(cases, cat_state)
            new_cases = diff_new_cases(cases, cat_state, logger=logger)
            logger.info("[DD日推] 分类案例今日新增 %s 条", len(new_cases))
            # 更新分类状态字段（延后保存，不在这里 save_cat_state）
            seen = set(cat_state.get("seen_ids", []))
            for item in cases:
                seen.add(item["slug"])
            cat_state["seen_ids"] = sorted(seen)
            cat_state["history"].setdefault(today.isoformat(), []).extend(item["slug"] for item in new_cases)
            cat_state["last_run"] = datetime.now().isoformat(timespec="seconds")
    except Exception as exc:
        logger.warning("[DD日推] 分类案例监控失败（不影响日历监控）: %s", exc)

    # 封面图上传 IMGBB（把 onsiteclub.com 直链转成钉钉可加载的 IMGBB 直链）
    # digest 模式合并报告里 ![](url) 需要转链；actionCard 模式由 push_new_items_actioncards
    # 内部按需转链，避免 run() 重复转链。
    if digest and new_cases and IMGBB_API_KEY:
        from notifier_dingtalk import upload_remote_to_imgbb
        for item in new_cases:
            remote_url = item.get("image_url")
            if remote_url:
                # upload_remote_to_imgbb 返回 (url, ok)；失败时置空 image_url 避免钉钉裂图
                new_url, ok = upload_remote_to_imgbb(remote_url, IMGBB_API_KEY)
                item["image_url"] = new_url if ok else ""

    report = build_dd_report(
        events, new_events, new_cases=new_cases, today=today, month_label=month_label,
        days=ONSITECLUB_SCHEDULE_DAYS,
    )
    logger.info("[DD日推] 文本报告生成，日历新增 %s 场 · 案例新增 %s 条", len(new_events), len(new_cases))

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

    # 生成 H5 报告页（独立于推送回调，--no-push 也会生成本地归档）
    h5_url = None
    new_items = list(new_events) + list(new_cases)
    if (not digest) and new_items:
        try:
            from dd_h5_report import build_h5_report
            from notifier_dingtalk import upload_h5_to_github
            from config import GITHUB_TOKEN, GITHUB_IMAGE_REPO
            html_content = build_h5_report(new_items, today=today)
            # 本地归档
            h5_path = os.path.join(REPORT_DIR, f"h5_report_{today.isoformat()}.html")
            with open(h5_path, "w", encoding="utf-8") as f:
                f.write(html_content)
            logger.info("[DD日推] H5 报告页生成：%s", h5_path)
            # 上传 GitHub（仅配置了 token/repo 时；--no-push 也上传无妨，便于实跑前预览）
            if GITHUB_TOKEN and GITHUB_IMAGE_REPO:
                h5_url = upload_h5_to_github(html_content, today.isoformat(), GITHUB_TOKEN, GITHUB_IMAGE_REPO)
                if h5_url:
                    logger.info("[DD日推] H5 上传成功：%s", h5_url)
                else:
                    logger.warning("[DD日推] H5 上传失败，actionCard 将回退原详情页 URL")
            else:
                logger.warning("[DD日推] 未配置 GitHub token/repo，H5 不上传，actionCard 回退原详情页 URL")
        except Exception as exc:
            logger.warning("[DD日推] H5 生成/上传失败（actionCard 回退原 URL）: %s", exc)

    # 推送分支：优先 oto_callback（企业机器人单聊，只发报告）；digest 模式调 push_callback（合并报告）；
    # 默认调 actioncard_callback（逐条卡片）。用 try/except 包裹，推送失败不阻塞状态保存（避免下次重复推送）
    try:
        if oto_callback:
            oto_callback(report, dashboard_path, month_label=month_label, new_cases=new_cases)
        elif digest and push_callback:
            push_callback(report, dashboard_path, new_events, events, month_label=month_label)
        elif (not digest) and actioncard_callback:
            actioncard_callback(new_items, h5_url=h5_url)
    except Exception as exc:
        logger.warning("[DD日推] 推送失败（状态仍会保存，下次不会重复推送）: %s", exc)

    # 统一保存状态：无论推送成功与否都执行，避免重复推送/漏报
    try:
        # 日历状态
        seen = set(state.get("seen_ids", []))
        for item in events:
            seen.add(item["id"])
        state["seen_ids"] = sorted(seen)
        state["history"].setdefault(today.isoformat(), []).extend(item["id"] for item in new_events)
        state["month"] = f"{year:04d}-{mon:02d}"
        state["last_run"] = datetime.now().isoformat(timespec="seconds")
        os.makedirs(DATA_DIR, exist_ok=True)
        save_state(state_path, state)
        logger.info("[DD日推] 日历状态已保存：%s", state_path)

        # 分类状态（延后保存，与日历状态一起收口）
        if cat_state is not None and cat_state_path is not None:
            from category_monitor import save_state as save_cat_state
            save_cat_state(cat_state_path, cat_state)
            logger.info("[DD日推] 分类状态已保存：%s", cat_state_path)
    except Exception as exc:
        logger.error("[DD日推] 状态保存失败（下次可能重复推送）: %s", exc)

    summary = {
        "month": f"{year:04d}-{mon:02d}",
        "total": len(events),
        "new": len(new_events),
        "first_run": first_run,
        "report_path": report_path,
        "dashboard_path": dashboard_path,
        "state_path": state_path,
    }
    if not digest:
        summary["new_items"] = len(new_events) + len(new_cases)
    return summary
