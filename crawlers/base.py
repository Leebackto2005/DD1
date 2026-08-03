"""爬虫共享基础设施：统一请求头与重试封装。

各平台爬虫只需保留自己的 URL 和解析逻辑，
重试循环、休眠退避、失败日志都由这里统一处理。
"""
import logging
import random
import time

logger = logging.getLogger("daily_news")

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
}


def make_headers(referer=None):
    """基于默认 UA 生成请求头；传入 referer 时附带 Referer 字段。"""
    headers = dict(DEFAULT_HEADERS)
    if referer:
        headers["Referer"] = referer
    return headers


def fetch_with_retry(fetch_once, source_name, max_retries=3):
    """通用重试封装。

    fetch_once() 应返回非空列表表示成功；返回空列表或抛异常时重试。
    全部失败后返回空列表（不抛出），交由上层记录运行状态。
    """
    for attempt in range(max_retries):
        try:
            results = fetch_once()
            if results:
                return results
        except Exception as exc:
            if attempt < max_retries - 1:
                time.sleep(random.uniform(1, 3) * (attempt + 1))
            else:
                logger.warning("[%s] 抓取失败: %s", source_name, exc)
    return []
