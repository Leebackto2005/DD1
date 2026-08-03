"""钉钉群机器人推送。

钉钉自定义机器人（Webhook）只支持 text / link / markdown / actionCard / feedCard 五种消息，
**不支持直接发送图片文件**。因此：
- 看板图片由脚本自动上传到公网图床（ImgBB 或 GitHub raw），再以 markdown ``![]()`` 引用；
- 「每个新增会展链接前配一张封面图」用 feedCard 卡片实现（picURL 指向 onsiteclub 自有的公网 HTTPS 封面）。

可配置项见 config.py：DINGTALK_WEBHOOK_URL / DINGTALK_SECRET / IMGBB_API_KEY / GITHUB_TOKEN / GITHUB_IMAGE_REPO。

字节安全截断 / 行边界截断函数复用自 TrendRadar（https://github.com/Leebackto2005/TrendRadar）的
trendradar/notification/batch.py，保持签名一致。
"""
import base64
import hashlib
import hmac
import logging
import os
import time
import urllib.parse

import requests

logger = logging.getLogger("daily_news")

FEEDCARD_MAX = 9  # 钉钉手机端 feedCard 最多稳定显示 9 条
FALLBACK_PIC_URL = "https://static.onsiteclub.com/static/imgs/icon-calendar-blue.png"


# ---------- 复用自 TrendRadar 的字节安全截断 ----------

def truncate_to_bytes(text, max_bytes):
    """安全截断字符串到指定字节数，避免截断多字节字符。"""
    text_bytes = text.encode("utf-8")
    if len(text_bytes) <= max_bytes:
        return text
    truncated = text_bytes[:max_bytes]
    for i in range(min(4, len(truncated))):
        try:
            return truncated[: len(truncated) - i].decode("utf-8")
        except UnicodeDecodeError:
            continue
    return ""


def truncate_at_line_boundary(text, max_bytes):
    """在行边界处截断，保证每一行完整。"""
    if len(text.encode("utf-8")) <= max_bytes:
        return text
    rough_cut = truncate_to_bytes(text, max_bytes)
    last_newline = rough_cut.rfind("\n")
    if last_newline > 0:
        return rough_cut[:last_newline]
    return rough_cut


# ---------- 钉钉 Webhook 签名与发送 ----------

def sign_webhook(webhook_url, secret):
    """钉钉加签：timestamp + '\n' + secret 做 HMAC-SHA256，追加到 URL。"""
    if not secret:
        return webhook_url
    timestamp = str(round(time.time() * 1000))
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(secret.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha256).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(digest))
    separator = "&" if "?" in webhook_url else "?"
    return f"{webhook_url}{separator}timestamp={timestamp}&sign={sign}"


def _post(webhook_url, payload, timeout=10, retries=3, retry_interval=3):
    """发送钉钉消息，对请求失败与瞬时错误（errcode=-1 系统繁忙）做自动重试。"""
    last_error = ""
    for attempt in range(retries):
        try:
            resp = requests.post(webhook_url, json=payload, timeout=timeout)
            resp.raise_for_status()
            result = resp.json()
        except Exception as exc:
            last_error = str(exc)
            if attempt < retries - 1:
                time.sleep(retry_interval)
                continue
            logger.warning("[钉钉] 请求失败: %s", exc)
            return False
        if result.get("errcode") == 0:
            logger.info("[钉钉] 发送成功：%s", payload.get("msgtype"))
            return True
        # errcode=-1「系统繁忙」等瞬时错误，稍后重试
        if result.get("errcode") == -1 and attempt < retries - 1:
            last_error = f"errcode=-1 {result.get('errmsg')}"
            time.sleep(retry_interval)
            continue
        logger.warning("[钉钉] 返回错误 errcode=%s errmsg=%s", result.get("errcode"), result.get("errmsg"))
        return False
    logger.warning("[钉钉] 重试 %s 次仍失败: %s", retries, last_error)
    return False


def send_markdown(webhook_url, title, text, secret=None, timeout=10):
    """发送 markdown 消息。"""
    text = truncate_at_line_boundary(text, 19500)
    return _post(sign_webhook(webhook_url, secret), {"msgtype": "markdown", "markdown": {"title": title, "text": text}}, timeout)


def send_feedcard(webhook_url, links, secret=None, timeout=10):
    """发送 feedCard 卡片列表（每条 = 标题 + 图片 + 链接）。

    links: [{"title": ..., "messageURL": ..., "picURL": ...}, ...]，最多 FEEDCARD_MAX 条。
    """
    links = list(links)[:FEEDCARD_MAX]
    for link in links:
        link.setdefault("picURL", FALLBACK_PIC_URL)
        if not link.get("messageURL"):
            link["messageURL"] = "https://www.onsiteclub.com/calendar"
    if not links:
        return False
    return _post(sign_webhook(webhook_url, secret), {"msgtype": "feedCard", "feedCard": {"links": links}}, timeout)


# ---------- 看板图片上传 ----------

def upload_to_imgbb(image_path, api_key, retries=3, retry_interval=8):
    """上传到 ImgBB 图床，返回公网 HTTPS 直链；失败返回 None。

    ImgBB 偶发维护/限流，做几次带间隔的重试。
    """
    with open(image_path, "rb") as file:
        encoded = base64.b64encode(file.read())
    last_error = ""
    for attempt in range(retries):
        try:
            resp = requests.post(
                "https://api.imgbb.com/1/upload",
                data={"key": api_key, "image": encoded},
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json().get("data") or {}
            url = data.get("url") or data.get("display_url")
            if url:
                logger.info("[图床] ImgBB 上传成功：%s", url)
                return url
            last_error = f"未返回链接: {resp.json()}"
        except requests.exceptions.HTTPError as exc:
            # 服务维护（code=100）等可重试错误；先记下，间隔后重试
            last_error = str(exc)
            try:
                body = exc.response.json()
                message = (body.get("error") or {}).get("message", "")
                if message:
                    last_error = message
            except Exception:
                pass
        except Exception as exc:
            last_error = str(exc)
        if attempt < retries - 1:
            time.sleep(retry_interval)
    logger.warning("[图床] ImgBB 上传失败：%s", last_error)
    return None


def upload_to_github(image_path, token, repo, dest_name, branch="main"):
    """上传到 GitHub 仓库 raw，返回 raw.githubusercontent.com 直链；失败返回 None。"""
    try:
        with open(image_path, "rb") as file:
            encoded = base64.b64encode(file.read()).decode("ascii")
        url = f"https://api.github.com/repos/{repo}/contents/{dest_name}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        }
        resp = requests.put(
            url, headers=headers,
            json={"message": f"chore(onsiteclub): update dashboard {dest_name}", "content": encoded, "branch": branch},
            timeout=20,
        )
        resp.raise_for_status()
        raw_url = f"https://raw.githubusercontent.com/{repo}/{branch}/{dest_name}"
        logger.info("[图床] GitHub raw 上传成功：%s", raw_url)
        return raw_url
    except Exception as exc:
        logger.warning("[图床] GitHub 上传失败: %s", exc)
    return None


def upload_dashboard_image(image_path, imgbb_key="", github_token="", github_repo=""):
    """按配置依次尝试上传看板图片，返回公网 URL；全部不可用返回 None。"""
    if not os.path.exists(image_path):
        return None
    if imgbb_key:
        url = upload_to_imgbb(image_path, imgbb_key)
        if url:
            return url
    if github_token and github_repo:
        dest = os.path.basename(image_path)
        url = upload_to_github(image_path, github_token, github_repo, dest)
        if url:
            return url
    logger.warning("[图床] 未配置可用的图床（IMGBB_API_KEY 或 GITHUB_TOKEN+GITHUB_IMAGE_REPO），看板图片只存本地")
    return None


# ---------- 组装推送 ----------

def _keyword_tag():
    """机器人安全关键词前缀：feedCard 只校验卡片 title 文本，加前缀确保送达。"""
    try:
        from config import DINGTALK_KEYWORD
        return f"{DINGTALK_KEYWORD}｜"
    except Exception:
        return "会展｜"


def build_feedcard_links(new_events):
    tag = _keyword_tag()
    links = []
    for event in list(new_events)[:FEEDCARD_MAX]:
        date_part = f"{event.get('display_start', '')} ~ {event.get('display_end', '')}"
        title = f"{tag}{event.get('title', '')} · {date_part}"
        links.append({
            "title": title,
            "messageURL": event.get("url", "") or "https://www.onsiteclub.com/calendar",
            "picURL": event.get("image_url", "") or FALLBACK_PIC_URL,
        })
    return links


def push_calendar_report(webhook_url, events, new_events, report_text, dashboard_path,
                         secret="", imgbb_key="", github_token="", github_repo="",
                         month_label="8月", max_new_items=24):
    """推送一次会展监控：markdown 汇总（含看板图）→ feedCard 新增卡片。

    返回 dict：每类消息的发送结果。
    """
    from onsite_monitor import build_text_report

    results = {"markdown": False, "feedcard": False, "dashboard_url": ""}

    markdown_body = build_text_report(events, new_events, month_label=month_label, max_new_items=max_new_items)

    dashboard_url = upload_dashboard_image(dashboard_path, imgbb_key, github_token, github_repo)
    if dashboard_url:
        # 在摘要行之后插入看板图，保证 markdown 结构不被破坏
        parts = markdown_body.split("\n", 2)
        if len(parts) == 3:
            markdown_body = f"{parts[0]}\n{parts[1]}\n\n![📊 {month_label}会展看板]({dashboard_url})\n\n{parts[2]}"
        results["dashboard_url"] = dashboard_url

    title = f"Onsite Club {month_label}会展监控 · {time.strftime('%m-%d')}"
    results["markdown"] = send_markdown(webhook_url, title, markdown_body, secret=secret)

    links = build_feedcard_links(new_events)
    if links:
        results["feedcard"] = send_feedcard(webhook_url, links, secret=secret)

    return results


def push_dd_report(webhook_url, events, new_events, report_text, dashboard_path,
                   secret="", imgbb_key="", github_token="", github_repo="",
                   month_label="8月"):
    """推送一次 DD日推：极简文本 markdown（含看板图）→ feedCard 新增图文卡片。

    看板图片上传图床后以 ``![看板](url)`` 插入标题行下方，与文字一同推送；
    feedCard 卡片按「图片在前 + 标题 + 链接」排版，每个新增会展对应一张图。
    返回 dict：每类消息的发送结果。
    """
    results = {"markdown": False, "feedcard": False, "dashboard_url": ""}

    dashboard_url = upload_dashboard_image(dashboard_path, imgbb_key, github_token, github_repo)
    if dashboard_url:
        # 在标题行之后插入看板图，保证 markdown 结构不被破坏
        parts = report_text.split("\n", 2)
        if len(parts) == 3:
            report_text = f"{parts[0]}\n{parts[1]}\n\n![📊 {month_label}会展看板]({dashboard_url})\n\n{parts[2]}"
        results["dashboard_url"] = dashboard_url

    title = f"Onsite Club {month_label}会展监控 · {time.strftime('%m-%d')}"
    results["markdown"] = send_markdown(webhook_url, title, report_text, secret=secret)

    links = build_feedcard_links(new_events)
    if links:
        results["feedcard"] = send_feedcard(webhook_url, links, secret=secret)

    return results
