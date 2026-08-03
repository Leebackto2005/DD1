import json
import logging
import os
import time
from datetime import datetime

LOGGER_NAME = "daily_news"
SNAPSHOT_VERSION = "2.0"  # 快照格式版本号，用于未来兼容


def setup_logger(log_dir="logs"):
    """Log to both console and logs/daily_YYYY-MM-DD.log."""
    os.makedirs(log_dir, exist_ok=True)
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    log_path = os.path.join(log_dir, f"daily_{datetime.now().strftime('%Y-%m-%d')}.log")
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    existing_paths = {
        getattr(handler, "baseFilename", None)
        for handler in logger.handlers
        if isinstance(handler, logging.FileHandler)
    }
    if os.path.abspath(log_path) not in existing_paths:
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    if not any(isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler) for handler in logger.handlers):
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    return logger, log_path


def status(name, ok=True, count=0, elapsed=0.0, error="", skipped=False):
    return {
        "name": name,
        "ok": bool(ok),
        "count": int(count or 0),
        "elapsed": round(float(elapsed or 0), 2),
        "error": str(error or ""),
        "skipped": bool(skipped),
    }


def timed_status(name, func, logger=None):
    """带计时和增强错误日志的状态包装器。"""
    start = time.monotonic()
    try:
        value = func()
        elapsed = time.monotonic() - start
        count = len(value) if isinstance(value, list) else int(bool(value))
        ok = bool(value)
        item_status = status(name, ok=ok, count=count, elapsed=elapsed, error="" if ok else "无数据")
        if logger:
            level = logging.INFO if item_status["ok"] else logging.WARNING
            logger.log(level, "[%s] %s，数量 %s，耗时 %.2fs", name, "成功" if item_status["ok"] else "无数据", count, elapsed)
        return value, item_status
    except Exception as exc:
        elapsed = time.monotonic() - start
        error_detail = _format_error(exc)
        item_status = status(name, ok=False, elapsed=elapsed, error=error_detail)
        if logger:
            logger.exception("[%s] 失败 (%s)，耗时 %.2fs", name, error_detail, elapsed)
        return [], item_status


def _format_error(exc):
    """将异常格式化为可读的错误原因。"""
    import requests as req_mod
    if isinstance(exc, req_mod.HTTPError):
        code = exc.response.status_code if hasattr(exc, "response") and exc.response is not None else "?"
        return f"HTTP {code}"
    if isinstance(exc, req_mod.ConnectionError):
        return "连接失败"
    if isinstance(exc, req_mod.Timeout):
        return "请求超时"
    if isinstance(exc, req_mod.TooManyRedirects):
        return "重定向过多"
    if isinstance(exc, (json.JSONDecodeError, ValueError, KeyError, IndexError, TypeError)):
        return f"解析失败: {type(exc).__name__}"
    return f"{type(exc).__name__}: {exc}"


def save_json_snapshot(data_dir, payload):
    """保存 JSON 快照，附带版本号。"""
    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, f"daily_snapshot_{datetime.now().strftime('%Y-%m-%d')}.json")

    # 注入快照版本号（先浅拷贝，避免原地修改调用方数据）
    snapshot = dict(payload) if isinstance(payload, dict) else payload
    if isinstance(snapshot, dict):
        snapshot["snapshot_version"] = SNAPSHOT_VERSION

    with open(path, "w", encoding="utf-8") as file:
        json.dump(snapshot, file, ensure_ascii=False, indent=2)
    return path


def news_to_dict(all_results):
    return [
        {
            "name": name,
            "emoji": emoji,
            "items": list(items or []),
        }
        for name, items, emoji in all_results
    ]


def validate_crawler_item(item, source_name=""):
    """校验单条爬虫数据结构的合法性，返回 (is_valid, issues)。

    在每条数据入库前调用，避免异常字段污染报告。
    """
    issues = []
    if not isinstance(item, dict):
        return False, ["非字典类型"]

    title = item.get("title")
    if not title or not isinstance(title, str) or not title.strip():
        issues.append("缺少有效 title")

    rank = item.get("rank")
    if rank is not None and not isinstance(rank, (int, float)):
        issues.append("rank 类型异常")

    url = item.get("url", "")
    if url and not isinstance(url, str):
        issues.append("url 类型异常")

    return len(issues) == 0, issues


def validate_crawler_results(items, source_name=""):
    """批量校验爬虫结果，过滤掉无效条目并记录警告。"""
    valid = []
    invalid_count = 0
    for item in items or []:
        is_ok, issues = validate_crawler_item(item, source_name)
        if is_ok:
            valid.append(item)
        else:
            invalid_count += 1
            logger = logging.getLogger(LOGGER_NAME)
            logger.warning(
                "[%s] 跳过无效条目: %s — %s",
                source_name,
                str(item.get("title", ""))[:40] if isinstance(item, dict) else str(item)[:40],
                "; ".join(issues),
            )
    return valid, invalid_count
