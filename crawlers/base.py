"""爬虫共享基础设施：统一请求头与重试封装。

各平台爬虫只需保留自己的 URL 和解析逻辑，
重试循环、休眠退避、失败日志都由这里统一处理。
"""
import logging
import random
import time
import warnings

import requests
import urllib3

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


class FetchError(Exception):
    """抓取失败（网络异常/解析失败/网站改版），调用方应视为错误而非「无数据」。"""


def fetch_with_retry(fetch_once, source_name, max_retries=3):
    """通用重试封装。

    fetch_once() 应返回列表：
    - 非空列表：成功，直接返回
    - 空列表：视为「网站确实无数据」（如某月无会展），不重试，直接返回空列表
    - 抛 FetchError 或其他异常：视为抓取失败，重试 max_retries 次

    全部重试失败后抛出 FetchError，交由上层决定如何处理（告警/降级）。
    """
    last_exc = None
    for attempt in range(max_retries):
        try:
            results = fetch_once()
            # 空列表 = 网站确实无数据，不重试
            if not results:
                logger.info("[%s] 抓取返回 0 条（网站可能无数据，不重试）", source_name)
                return []
            return results
        except Exception as exc:
            last_exc = exc
            if attempt < max_retries - 1:
                time.sleep(random.uniform(1, 3) * (attempt + 1))
            else:
                logger.warning("[%s] 抓取失败（重试 %s 次）: %s", source_name, max_retries, exc)
    # 全部重试失败，抛异常让上层感知
    raise FetchError(f"{source_name} 抓取失败（重试 {max_retries} 次）: {last_exc}")


def _is_cert_verify_failure(exc):
    """判断 SSLError 是否由「证书校验失败」引起（而非协议/网络层 TLS 错误）。

    新老 requests/urllib3 的异常类不一（SSLCertVerificationError 已从
    urllib3 2.x 移除），统一按报错文本里的校验失败标记识别。
    """
    text = str(exc)
    return "CERTIFICATE_VERIFY_FAILED" in text or "certificate verify failed" in text


def get_with_ssl_fallback(url, *, params=None, headers=None, timeout=10, allow_insecure_fallback=True):
    """GET 请求；站点证书过期/无效时自动降级为不校验重试一次。

    优先正常校验证书；当证书校验失败（服务端问题，如证书过期）时
    用 verify=False 重试并打 warning。站点证书恢复后自动回到验证路径。
    非证书类 SSL 错误不降级，原样抛出。
    """
    try:
        return requests.get(url, params=params, headers=headers, timeout=timeout)
    except requests.exceptions.SSLError as exc:
        if not allow_insecure_fallback or not _is_cert_verify_failure(exc):
            raise
        logger.warning("[SSL降级] %s 证书校验失败（%s），改用不校验 SSL 重试", url, exc)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", urllib3.exceptions.InsecureRequestWarning)
            return requests.get(url, params=params, headers=headers, timeout=timeout, verify=False)
