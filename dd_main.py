"""DD日推 · Onsite Club 会展监控 CLI。

用法：
  python dd_main.py --once            # 执行一次监控：默认逐条 actionCard 推送（每条新增一张卡片）
  python dd_main.py --once --digest   # 恢复合并 markdown 报告推送（旧行为，一条消息）
  python dd_main.py --once --no-push  # 只生成本地产物，不推送钉钉
  python dd_main.py --once --month 2026-08   # 指定监控月份
  python dd_main.py --init-category <slug>   # 初始化分类案例监控基准（一次性）
  python dd_main.py --check-config   # 检查推送/图床配置是否就绪

配合 dd_scheduler.py 每日 9:00 自动执行（默认逐条 actionCard；--digest 切回合并报告）。
"""
import argparse
import sys

import dd_monitor as monitor
from config import (
    DINGTALK_APP_KEY,
    DINGTALK_APP_SECRET,
    DINGTALK_GROUP_ID,
    DINGTALK_GROUP_NAME,
    DINGTALK_RECIPIENT_NAME,
    DINGTALK_SECRET,
    DINGTALK_WEBHOOK_URL,
    GITHUB_IMAGE_REPO,
    GITHUB_TOKEN,
    IMGBB_API_KEY,
    ONSITECLUB_MONTH,
)


def parse_month(raw):
    """解析 YYYY-MM 或 (YYYY, MM)；非法返回 None。"""
    if not raw:
        return None
    text = str(raw).strip()
    if text.count("-") == 1:
        year, mon = text.split("-")
    else:
        year, mon = text, "0"
    try:
        year = int(year)
        mon = int(mon)
    except ValueError:
        return None
    if not (2000 <= year <= 2100 and 1 <= mon <= 12):
        return None
    return (year, mon)


def make_push_callback():
    """根据环境配置构建钉钉推送回调；未配置 webhook 时返回 None（跳过推送）。"""
    if not DINGTALK_WEBHOOK_URL or "your-webhook" in DINGTALK_WEBHOOK_URL:
        return None
    from notifier_dingtalk import push_dd_report

    def _callback(report_text, dashboard_path, new_events, events, month_label=""):
        return push_dd_report(
            DINGTALK_WEBHOOK_URL,
            events,
            new_events,
            report_text,
            dashboard_path,
            secret=DINGTALK_SECRET,
            imgbb_key=IMGBB_API_KEY,
            github_token=GITHUB_TOKEN,
            github_repo=GITHUB_IMAGE_REPO,
            month_label=month_label,
        )

    return _callback


def make_enterprise_push_callback():
    """构建企业机器人推送回调：把新增分类案例逐条推成 actionCard 卡片。

    优先发到机器人所在群（DINGTALK_GROUP_ID，openConversationId）；未配置群 ID 时
    回退给 DINGTALK_RECIPIENT_NAME 发单聊（旧行为）。配置了 DINGTALK_APP_KEY/
    APP_SECRET 时启用；未配置返回 None（回退 webhook 路径）。
    """
    if not (DINGTALK_APP_KEY and DINGTALK_APP_SECRET):
        return None
    from send_dingtalk_user import send_cases_actioncards, send_cases_actioncards_group

    def _callback(report_text, dashboard_path, month_label="", new_cases=None):
        # 每日必发：有新增逐条推 actionCard 卡片，无新增发「今日无新增」卡片
        if DINGTALK_GROUP_ID:
            ok, message = send_cases_actioncards_group(new_cases, DINGTALK_GROUP_ID)
            label = DINGTALK_GROUP_NAME or DINGTALK_GROUP_ID
            if ok:
                print(f"✅ 企业机器人群推送分类案例卡片（群「{label}」）：{message}")
            else:
                print(f"⚠️  企业机器人群推送失败：{message}")
            return ok
        ok, message = send_cases_actioncards(
            new_cases,
            recipient_name=DINGTALK_RECIPIENT_NAME,
        )
        if ok:
            print(f"✅ 企业机器人单聊推送分类案例卡片：{message}")
        else:
            print(f"⚠️  企业机器人单聊推送失败：{message}")
        return ok

    return _callback


def make_actioncard_push_callback():
    """构建逐条 actionCard 推送回调；未配置 webhook 时返回 None（跳过推送）。"""
    if not DINGTALK_WEBHOOK_URL or "your-webhook" in DINGTALK_WEBHOOK_URL:
        return None
    from notifier_dingtalk import push_new_items_actioncards, send_no_new_actioncard

    def _callback(new_items, h5_url=None):
        if not new_items:
            # 日历+分类都无新增：发一张「无新增」actionCard，避免静默
            return send_no_new_actioncard(DINGTALK_WEBHOOK_URL, secret=DINGTALK_SECRET)
        return push_new_items_actioncards(
            DINGTALK_WEBHOOK_URL,
            new_items,
            secret=DINGTALK_SECRET,
            imgbb_key=IMGBB_API_KEY,
            h5_url=h5_url,
        )

    return _callback


def check_config():
    checks = [
        ("钉钉 Webhook", bool(DINGTALK_WEBHOOK_URL and "your-webhook" not in DINGTALK_WEBHOOK_URL)),
        ("钉钉企业机器人（群推送）", bool(DINGTALK_APP_KEY and DINGTALK_APP_SECRET and DINGTALK_GROUP_ID)),
        ("钉钉企业机器人（单聊）", bool(DINGTALK_APP_KEY and DINGTALK_APP_SECRET and DINGTALK_RECIPIENT_NAME)),
        ("钉钉加签密钥", bool(DINGTALK_SECRET)),
        ("看板图床 ImgBB", bool(IMGBB_API_KEY)),
        ("看板图床 GitHub", bool(GITHUB_TOKEN and GITHUB_IMAGE_REPO)),
    ]
    for name, ready in checks:
        print(f"[{'✓' if ready else '○'}] {name}")
    webhook_ready = DINGTALK_WEBHOOK_URL and "your-webhook" not in DINGTALK_WEBHOOK_URL
    oto_ready = bool(DINGTALK_APP_KEY and DINGTALK_APP_SECRET and (DINGTALK_GROUP_ID or DINGTALK_RECIPIENT_NAME))
    print()
    if not (webhook_ready or oto_ready):
        print("⚠️  未配置任何钉钉推送渠道，推送将被跳过（只生成本地产物）。")
        print("    请在 .env 配置 DINGTALK_WEBHOOK_URL，或企业应用 DINGTALK_APP_KEY/APP_SECRET + DINGTALK_GROUP_ID（群推送）。")
    if not (IMGBB_API_KEY or (GITHUB_TOKEN and GITHUB_IMAGE_REPO)):
        print("⚠️  未配置图床，看板图片只会存到本地，不会显示在钉钉里。")
        print("    推荐免费注册 ImgBB 拿 API Key 填 IMGBB_API_KEY。")
    return webhook_ready or oto_ready


def main(argv=None):
    parser = argparse.ArgumentParser(description="DD日推 · Onsite Club 会展监控")
    parser.add_argument("--once", action="store_true", help="执行一次完整监控（日历+分类案例）")
    parser.add_argument("--no-push", action="store_true", help="不推送钉钉，只生成本地产物")
    parser.add_argument("--digest", action="store_true", help="推送合并 markdown 报告（默认逐条 actionCard）")
    parser.add_argument("--month", help="监控月份 YYYY-MM，默认当前月")
    parser.add_argument("--max-pages", type=int, default=3, help="分类案例抓取前N页（默认3）")
    parser.add_argument("--init-category", metavar="SLUG", help="以指定 slug 为基准初始化分类案例监控状态（一次性）")
    parser.add_argument("--check-config", action="store_true", help="检查推送与图床配置")
    args = parser.parse_args(argv)

    if args.check_config:
        check_config()
        return

    # 分类案例监控：初始化基准（一次性，设定「已见过」起点）
    if args.init_category:
        import category_monitor as cat_monitor
        summary = cat_monitor.init_baseline(args.init_category, max_pages=args.max_pages)
        print("\n===== 分类案例监控基准初始化完成 =====")
        for key, value in summary.items():
            print(f"  {key}: {value}")
        print(f"\n下次运行 python dd_main.py --once 时，将只报告排在基准之前的 {summary['will_be_new']} 条案例为新增")
        return

    month = parse_month(args.month) or parse_month(ONSITECLUB_MONTH)
    if month and not (1 <= month[1] <= 12 and 2000 <= month[0] <= 2100):
        print("月份参数非法，示例：--month 2026-08")
        sys.exit(1)

    # 推送策略：优先企业机器人（群推送，未配群 ID 回退单聊）；否则 --digest 走合并报告回调；
    # 否则默认逐条 actionCard 回调；--no-push 都置 None
    digest_cb = None
    actioncard_cb = None
    enterprise_cb = None
    if not args.no_push:
        enterprise_cb = make_enterprise_push_callback()
        if enterprise_cb:
            pass  # 仅企业机器人（群/单聊），忽略 --digest / actionCard
        elif args.digest:
            digest_cb = make_push_callback()
        else:
            actioncard_cb = make_actioncard_push_callback()

    webhook_configured = bool(
        DINGTALK_WEBHOOK_URL and "your-webhook" not in DINGTALK_WEBHOOK_URL
    )
    if not args.no_push and not enterprise_cb and not webhook_configured:
        check_config()

    summary = monitor.run(
        month=month,
        push_callback=digest_cb,
        digest=args.digest,
        actioncard_callback=actioncard_cb,
        oto_callback=enterprise_cb,
        max_pages=args.max_pages,
    )
    print("\n===== 执行摘要 =====")
    for key, value in summary.items():
        print(f"  {key}: {value}")

    if args.no_push:
        push_status = "未推送（只生成本地）"
    elif enterprise_cb:
        if DINGTALK_GROUP_ID:
            push_status = (
                f"企业机器人群推送：分类案例逐条 actionCard 推送"
                f"（群「{DINGTALK_GROUP_NAME or DINGTALK_GROUP_ID}」，无新增发无新增卡片）"
            )
        else:
            push_status = "企业机器人单聊：分类案例逐条 actionCard 推送（无新增发无新增卡片）"
    elif args.digest:
        push_status = "已推送合并报告" if digest_cb else "未推送（未配置 webhook）"
    else:
        push_status = "已逐条推送 actionCard" if actioncard_cb else "未推送（未配置 webhook）"
    print("\n推送状态：" + push_status)


if __name__ == "__main__":
    main()
