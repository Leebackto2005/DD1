"""DD日推 · Onsite Club 会展监控 CLI。

用法：
  python dd_main.py --once            # 执行一次日历监控（抓取→去重→报告→看板→推送）
  python dd_main.py --once --no-push  # 只生成本地产物，不推送钉钉
  python dd_main.py --once --month 2026-08   # 指定监控月份
  python dd_main.py --category        # 执行一次分类案例监控（独立管道，精短文本推送）
  python dd_main.py --category --no-push     # 分类监控只生成本地产物
  python dd_main.py --check-config   # 检查推送/图床配置是否就绪

配合 dd_scheduler.py 每日 9:00 自动执行（日历 + 分类两条独立管道）。
"""
import argparse
import sys

import dd_monitor as monitor
from config import (
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


def make_category_push_callback():
    """分类监控的钉钉推送回调（纯 markdown 精短文本，无看板图）；未配置 webhook 返回 None。"""
    if not DINGTALK_WEBHOOK_URL or "your-webhook" in DINGTALK_WEBHOOK_URL:
        return None
    from notifier_dingtalk import send_markdown

    def _callback(report_text, new_cases):
        title = f"Onsite Club 今日新增案例 · {__import__('time').strftime('%m-%d')}"
        return send_markdown(DINGTALK_WEBHOOK_URL, title, report_text, secret=DINGTALK_SECRET)
    return _callback


def check_config():
    checks = [
        ("钉钉 Webhook", bool(DINGTALK_WEBHOOK_URL and "your-webhook" not in DINGTALK_WEBHOOK_URL)),
        ("钉钉加签密钥", bool(DINGTALK_SECRET)),
        ("看板图床 ImgBB", bool(IMGBB_API_KEY)),
        ("看板图床 GitHub", bool(GITHUB_TOKEN and GITHUB_IMAGE_REPO)),
    ]
    for name, ready in checks:
        print(f"[{'✓' if ready else '○'}] {name}")
    webhook_ready = DINGTALK_WEBHOOK_URL and "your-webhook" not in DINGTALK_WEBHOOK_URL
    print()
    if not webhook_ready:
        print("⚠️  未配置钉钉 Webhook，推送将被跳过（只生成本地产物）。")
        print("    请在 .env 设置 DINGTALK_WEBHOOK_URL。")
    if not (IMGBB_API_KEY or (GITHUB_TOKEN and GITHUB_IMAGE_REPO)):
        print("⚠️  未配置图床，看板图片只会存到本地，不会显示在钉钉里。")
        print("    推荐免费注册 ImgBB 拿 API Key 填 IMGBB_API_KEY。")
    return webhook_ready


def main(argv=None):
    parser = argparse.ArgumentParser(description="DD日推 · Onsite Club 会展监控")
    parser.add_argument("--once", action="store_true", help="执行一次日历监控流程")
    parser.add_argument("--category", action="store_true", help="执行一次分类案例监控（独立管道）")
    parser.add_argument("--no-push", action="store_true", help="不推送钉钉，只生成本地产物")
    parser.add_argument("--month", help="监控月份 YYYY-MM，默认当前月")
    parser.add_argument("--max-pages", type=int, default=3, help="分类监控抓取前N页（默认3）")
    parser.add_argument("--init-category", metavar="SLUG", help="以指定 slug 为基准初始化分类监控状态（基准及其之后标记为已见过）")
    parser.add_argument("--check-config", action="store_true", help="检查推送与图床配置")
    args = parser.parse_args(argv)

    if args.check_config:
        check_config()
        return

    # 分类监控：初始化基准（一次性，设定「已见过」起点）
    if args.init_category:
        import category_monitor as cat_monitor
        summary = cat_monitor.init_baseline(args.init_category, max_pages=args.max_pages)
        print("\n===== 分类监控基准初始化完成 =====")
        for key, value in summary.items():
            print(f"  {key}: {value}")
        print(f"\n下次运行 python dd_main.py --category 时，将只报告排在基准之前的 {summary['will_be_new']} 条为新增")
        return

    # 分类案例监控（独立管道）
    if args.category:
        import category_monitor as cat_monitor
        push_cb = None if args.no_push else make_category_push_callback()
        if push_cb is None and not args.no_push:
            check_config()
        summary = cat_monitor.run(push_callback=push_cb, max_pages=args.max_pages)
        print("\n===== 分类监控执行摘要 =====")
        for key, value in summary.items():
            print(f"  {key}: {value}")
        print("\n推送状态：" + ("已推送钉钉" if push_cb else "未推送（只生成本地）"))
        return

    month = parse_month(args.month) or parse_month(ONSITECLUB_MONTH)
    if month and not (1 <= month[1] <= 12 and 2000 <= month[0] <= 2100):
        print("月份参数非法，示例：--month 2026-08")
        sys.exit(1)

    push_callback = None if args.no_push else make_push_callback()
    if push_callback is None and not args.no_push:
        check_config()

    summary = monitor.run(month=month, push_callback=push_callback)
    print("\n===== 执行摘要 =====")
    for key, value in summary.items():
        print(f"  {key}: {value}")
    print("\n推送状态：" + ("已推送钉钉" if push_callback else "未推送（只生成本地）"))


if __name__ == "__main__":
    main()
