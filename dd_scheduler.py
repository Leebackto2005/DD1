"""DD日推 · Onsite Club 会展监控每日定时任务（默认每日 9:00）。

用法：
  python dd_scheduler.py            # 启动后常驻，到点自动执行
  python dd_scheduler.py --once     # 立即执行一次后退出（用于测试）

推送目标群在 .env 配置 DINGTALK_WEBHOOK_URL；未配置时只生成本地报告与看板。
"""
import argparse
import logging
import os
import traceback
from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler

import dd_main
from config import LOG_DIR

SCHEDULE_HOUR = 9  # 每日 9:00
CATEGORY_HOUR = 9  # 分类监控同样 9:00，与日历监控并行但单独推一条消息
CATEGORY_MINUTE = 5  # 错开 5 分钟，避免两条管道同时抓取造成压力

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def run_job():
    task_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    print(f"\n{'='*52}\n[调度] {task_id} 开始执行 DD日推会展监控...\n{'='*52}")
    try:
        dd_main.main(["--once"])
        print(f"[调度] {task_id} 任务完成")
    except Exception:
        print(f"[调度] {task_id} 任务异常:")
        traceback.print_exc()
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            log_path = os.path.join(LOG_DIR, f"dd_scheduler_{datetime.now().strftime('%Y-%m-%d')}.log")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"\n{'='*40}\n[调度异常] {task_id}\n")
                traceback.print_exc(file=f)
                f.write(f"{'='*40}\n")
        except Exception:
            pass


def run_category_job():
    """分类案例监控任务（独立管道，精短文本推送）。"""
    task_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    print(f"\n{'='*52}\n[调度] {task_id} 开始执行分类案例监控...\n{'='*52}")
    try:
        dd_main.main(["--category"])
        print(f"[调度] {task_id} 分类监控任务完成")
    except Exception:
        print(f"[调度] {task_id} 分类监控任务异常:")
        traceback.print_exc()
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            log_path = os.path.join(LOG_DIR, f"dd_scheduler_{datetime.now().strftime('%Y-%m-%d')}.log")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"\n{'='*40}\n[分类监控异常] {task_id}\n")
                traceback.print_exc(file=f)
                f.write(f"{'='*40}\n")
        except Exception:
            pass


def main(argv=None):
    parser = argparse.ArgumentParser(description="DD日推 · Onsite Club 会展监控定时任务")
    parser.add_argument("--once", action="store_true", help="立即执行一次后退出")
    parser.add_argument("--category", action="store_true", help="只执行分类案例监控")
    args = parser.parse_args(argv)

    if args.once:
        if args.category:
            run_category_job()
        else:
            run_job()
            run_category_job()
        return

    scheduler = BlockingScheduler(timezone="Asia/Shanghai")
    scheduler.add_job(
        run_job,
        "cron",
        hour=SCHEDULE_HOUR,
        minute=0,
        id="dd_onsiteclub_0900",
        misfire_grace_time=300,
    )
    scheduler.add_job(
        run_category_job,
        "cron",
        hour=CATEGORY_HOUR,
        minute=CATEGORY_MINUTE,
        id="dd_category_0905",
        misfire_grace_time=300,
    )
    print(f"DD日推定时任务已启动：日历监控每日 {SCHEDULE_HOUR:02d}:00，分类监控每日 {CATEGORY_HOUR:02d}:{CATEGORY_MINUTE:02d}（Ctrl+C 停止）")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("\n已停止")


if __name__ == "__main__":
    main()
