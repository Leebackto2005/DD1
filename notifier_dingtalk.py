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
import io
import logging
import os
import re
import time
import urllib.parse
from datetime import datetime

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


# 钉钉可重试的 errcode：系统繁忙、请求过快等瞬时错误
# -1: 系统繁忙
# 130101: 请求过快（OpenAPI 限流）
# 88: 下载图片超时等瞬时 IO 错误
RETRYABLE_ERRCODES = {-1, 130101, 88}


def _post(webhook_url, payload, timeout=10, retries=3, retry_interval=3):
    """发送钉钉消息，对请求失败与瞬时错误做自动重试。"""
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
        # 可重试的瞬时错误，稍后重试
        if result.get("errcode") in RETRYABLE_ERRCODES and attempt < retries - 1:
            last_error = f"errcode={result.get('errcode')} {result.get('errmsg')}"
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


def upload_remote_to_imgbb(image_url, api_key, retries=2, retry_interval=5):
    """把远程图片 URL 上传到 ImgBB，返回 (url, ok) 元组。

    - 成功：返回 (imbb_url, True)
    - 失败：返回 (原 url, False)，调用方据此决定是否在 markdown 里展示图片

    优先用 IMGBB API 的 url 参数让 ImgBB 服务器抓取；若失败（onsiteclub.com
    可能有防盗链/Cloudflare 防护导致 ImgBB 抓不到），fallback 到本地下载 +
    base64 上传（带 Referer 绕过防盗链）。
    """
    if not image_url or not api_key:
        return image_url, False

    # 方式一：远程 URL 上传（让 ImgBB 服务器去抓取）
    last_error = ""
    for attempt in range(retries):
        try:
            resp = requests.post(
                "https://api.imgbb.com/1/upload",
                data={"key": api_key, "url": image_url},
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json().get("data") or {}
            url = data.get("url") or data.get("display_url")
            if url:
                logger.info("[图床] ImgBB 远程上传成功：%s", url)
                return url, True
            last_error = f"未返回链接: {resp.json()}"
        except Exception as exc:
            last_error = str(exc)
        if attempt < retries - 1:
            time.sleep(retry_interval)
    logger.warning("[图床] ImgBB 远程上传失败（尝试 fallback 下载到本地）：image_url=%s, error=%s", image_url, last_error)

    # 方式二：fallback —— 下载到本地 + base64 上传（绕过 ImgBB 服务器抓取）
    try:
        from crawlers.base import make_headers
        from crawlers.onsiteclub_calendar import BASE_URL
        headers = make_headers(referer=BASE_URL)
        dl_resp = requests.get(image_url, headers=headers, timeout=20)
        dl_resp.raise_for_status()
        img_data = dl_resp.content
        if not img_data:
            logger.warning("[图床] 下载图片为空：%s", image_url)
            return image_url, False
        encoded = base64.b64encode(img_data)
        resp2 = requests.post(
            "https://api.imgbb.com/1/upload",
            data={"key": api_key, "image": encoded},
            timeout=20,
        )
        resp2.raise_for_status()
        data2 = resp2.json().get("data") or {}
        url2 = data2.get("url") or data2.get("display_url")
        if url2:
            logger.info("[图床] ImgBB 本地下载+上传成功：%s", url2)
            return url2, True
        logger.warning("[图床] ImgBB 本地上传未返回链接：%s", resp2.json())
    except Exception as exc:
        logger.warning("[图床] ImgBB fallback 本地下载+上传失败：%s", exc)

    return image_url, False


def _build_actioncard_banner(image_data, size=(1200, 540)):
    """Center-crop remote artwork into a shallow JPEG banner for actionCard."""
    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(image_data)) as source:
        source = ImageOps.exif_transpose(source).convert("RGB")
        resampling = getattr(Image, "Resampling", Image).LANCZOS
        banner = ImageOps.fit(source, size, method=resampling, centering=(0.5, 0.45))
        output = io.BytesIO()
        banner.save(output, format="JPEG", quality=86, optimize=True, progressive=True)
        return output.getvalue()


def upload_actioncard_banner_to_imgbb(image_url, api_key, retries=2, retry_interval=3):
    """Download, crop and upload a compact 1200x540 banner; return None on failure."""
    if not image_url or not api_key:
        return None
    try:
        from crawlers.base import make_headers
        from crawlers.onsiteclub_calendar import BASE_URL
        response = requests.get(
            image_url, headers=make_headers(referer=BASE_URL), timeout=20,
        )
        response.raise_for_status()
        banner_data = _build_actioncard_banner(response.content)
    except Exception as exc:
        logger.warning("[actionCard] 横幅图片下载或裁切失败：%s", exc)
        return None

    last_error = ""
    for attempt in range(retries):
        try:
            response = requests.post(
                "https://api.imgbb.com/1/upload",
                data={"key": api_key, "image": base64.b64encode(banner_data)},
                timeout=20,
            )
            response.raise_for_status()
            data = response.json().get("data") or {}
            url = data.get("url") or data.get("display_url")
            if url:
                logger.info("[actionCard] 横幅图片上传成功：%s", url)
                return url
            last_error = f"未返回链接: {response.json()}"
        except Exception as exc:
            last_error = str(exc)
        if attempt < retries - 1:
            time.sleep(retry_interval)
    logger.warning("[actionCard] 横幅图片上传失败：%s", last_error)
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


def upload_h5_to_github(html_content, date_str, token, repo, branch="main"):
    """上传 H5 报告页 HTML 到 GitHub 仓库 h5/ 目录，返回 jsDelivr CDN URL；失败返回 None。

    Args:
        html_content: HTML 字符串
        date_str: 日期字符串 YYYY-MM-DD，用作文件名
        token: GitHub token
        repo: GitHub 仓库（owner/repo 格式）
        branch: 分支，默认 main
    Returns:
        jsDelivr URL: https://cdn.jsdelivr.net/gh/{repo}@{branch}/h5/{date_str}.html
        失败返回 None
    """
    try:
        encoded = base64.b64encode(html_content.encode("utf-8")).decode("ascii")
        path = f"h5/{date_str}.html"
        url = f"https://api.github.com/repos/{repo}/contents/{path}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        }
        resp = requests.put(
            url, headers=headers,
            json={"message": f"chore(onsiteclub): update h5 report {date_str}", "content": encoded, "branch": branch},
            timeout=20,
        )
        resp.raise_for_status()
        from config import JSDELIVR_URL_TEMPLATE
        jsdelivr_url = JSDELIVR_URL_TEMPLATE.format(repo=repo, date=date_str)
        logger.info("[H5] GitHub 上传成功：%s", jsdelivr_url)
        return jsdelivr_url
    except Exception as exc:
        logger.warning("[H5] GitHub 上传失败: %s", exc)
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


# ---------- actionCard 逐条推送 ----------

def send_actioncard(webhook_url, title, text, single_url, secret=None, single_title="查看详情", timeout=10):
    """发送整体跳转 actionCard 消息。

    title 为消息列表预览标题（纯文本，不含 markdown），text 为卡片 markdown 正文，
    singleTitle 为按钮文案（默认「查看详情」），singleURL 为详情页跳转链接。
    """
    text = truncate_at_line_boundary(text, 19500)
    payload = {
        "msgtype": "actionCard",
        "actionCard": {
            "title": title,
            "text": text,
            "singleTitle": single_title,
            "singleURL": single_url,
        },
    }
    return _post(sign_webhook(webhook_url, secret), payload, timeout)


def send_no_new_actioncard(webhook_url, secret=None,
                           site_label="Onsite Club",
                           site_url="https://www.onsiteclub.com/calendar",
                           now=None, timeout=10):
    """今日无新增会展时的「无新增」actionCard 推送。

    最小信息：网站名 + 时间（到小时/分钟）+ 无新增提示；按钮跳转完整日历。
    标题含「会展」，满足自定义机器人关键词校验。
    """
    now = now or datetime.now()
    time_str = now.strftime("%Y-%m-%d %H:%M")
    title = f"✅ 今日无新增案例 · {time_str}"
    text = "\n\n".join([
        f"🎉 **{site_label} 案例监控**",
        f"📅 **{time_str}**",
        f"✨ **今日没有新增案例**",
    ])
    return send_actioncard(webhook_url, title, text, site_url,
                           secret=secret, single_title="查看完整日历", timeout=timeout)


def summarize_description(text, limit=None):
    """Normalize body text; optionally truncate for callers that need a limit."""
    raw_text = str(text or "").strip()
    if limit:
        text = re.sub(r"\s+", " ", raw_text)
    else:
        paragraphs = [re.sub(r"[ \t]+", " ", line).strip() for line in raw_text.splitlines()]
        text = "\n\n".join(line for line in paragraphs if line)
    if not limit or len(text) <= limit:
        return text

    minimum_break = int(limit * 0.7)
    preview = text[:limit]
    break_at = 0
    for match in re.finditer(r"[。！？!?；;]", preview):
        if match.end() >= minimum_break:
            break_at = match.end()
    if not break_at:
        for match in re.finditer(r"[，、,]", preview):
            if match.end() >= minimum_break:
                break_at = match.end()
    if not break_at:
        break_at = limit
    return text[:break_at].rstrip() + "…"


def _format_actioncard_date(item):
    start = str(item.get("start") or item.get("start_date") or "").strip()
    end = str(item.get("end") or item.get("end_date") or "").strip()
    if not start:
        return ""
    start = start[:10] if len(start) >= 10 else start
    end = end[:10] if len(end) >= 10 else end
    return f"{start} — {end}" if end and end != start else start


def _build_actioncard_metadata(item):
    """Build at most two compact metadata lines, omitting unavailable fields."""
    primary = []
    city = str(item.get("city") or "").strip()
    if city:
        primary.append(f"**地点** {city}")
    date_span = _format_actioncard_date(item)
    if date_span:
        primary.append(f"**日期** {date_span}")

    secondary = []
    for label, key in (("品牌", "brand"), ("类型", "type"), ("行业", "industry")):
        value = str(item.get(key) or "").strip()
        if value and value not in {"待定", "未知", "其他"}:
            secondary.append(f"**{label}** {value}")

    return [" · ".join(values) for values in (primary, secondary) if values]


def build_actioncard_text(item):
    """按 spec 排版生成 actionCard 的 markdown text。

    结构：封面图 + 紧凑标题 + 项目简介 + 两行结构化元信息。
    兼容日历会展（start_date/end_date）与分类案例（start/end）两种日期字段名，
    日期区间显示完整年月日，简介在卡片端控制为约 150 字并自然断句。
    """
    parts = []
    title = (item.get("title") or "").strip()
    # 封面图：有 image_url 才显示
    image_url = (item.get("image_url") or "").strip()
    if image_url:
        parts.append(f"![{title}]({image_url})")
    # 标题：始终显示
    parts.append(f"### {title or '未命名'}")
    # 横幅保持适度高度，正文控制在约 150 字并优先在标点处自然收尾。
    description = summarize_description(item.get("description"), limit=150)
    if description:
        parts.append(f"**项目简介**\n\n{description}")
    metadata = _build_actioncard_metadata(item)
    if metadata:
        parts.append("  \n".join(metadata))
    # 各段落间用空行分隔
    return "\n\n".join(parts)


def push_new_items_actioncards(webhook_url, items, secret=None, imgbb_key="", h5_url=None):
    """批量逐条推送 actionCard。

    每条新增资讯 = 一张 actionCard = 一条钉钉消息，间隔 1 秒避免每分钟 20 条限流。
    超出 20 条的部分合并为一条 markdown 汇总消息推送（含剩余条目序号+标题+链接）。
    返回 dict：{"actioncard_count": N, "overflow_count": M, "overflow_pushed": bool}。

    Args:
        h5_url: H5 报告页 jsDelivr URL（成功上传后传入）。非空时前 20 条 actionCard 的
            singleURL 指向 `{h5_url}#case-{i}`（i 与 H5 页面 article id 对应，从 1 起）；
            为 None 时回退 onsiteclub.com 原始详情页 URL。溢出 markdown 汇总不受影响。
    """
    items = list(items or [])
    ACTIONCARD_LIMIT = 20  # 钉钉自定义机器人每分钟 20 条限流

    head = items[:ACTIONCARD_LIMIT]
    overflow = items[ACTIONCARD_LIMIT:]

    actioncard_count = 0
    # i 仅用于 H5 锚点编号（已废弃 H5 跳转，保留编号便于未来恢复）
    for i, src_item in enumerate(head, start=1):
        # 浅拷贝 item，避免 IMGBB 转链修改污染源数据（影响 H5/digest/重复推送等场景）
        item = dict(src_item)
        # IMGBB 转链：成功才显示图片，失败则不显示图片（避免裂图破坏卡片美观）
        original_image_url = item.get("image_url") or ""
        if imgbb_key and original_image_url:
            converted = upload_actioncard_banner_to_imgbb(original_image_url, imgbb_key)
            if converted:
                item["image_url"] = converted
            else:
                # IMGBB 失败，置空 image_url 避免显示裂图
                item["image_url"] = ""
        # 生成卡片正文
        text = build_actioncard_text(item)
        # title 用纯文本标题
        title = (item.get("title") or "").strip() or "未命名"
        # singleURL：直接用原网址（onsiteclub.com 详情页）
        single_url = item.get("url") or ""
        if send_actioncard(webhook_url, title, text, single_url, secret=secret):
            actioncard_count += 1
        # 每条之间 sleep 1 秒，避免钉钉每分钟 20 条限流
        time.sleep(1)

    overflow_count = len(overflow)
    overflow_pushed = False
    if overflow:
        # 与最后一条 actionCard 间隔 1 秒，再推送溢出汇总
        time.sleep(1)
        lines = [f"### 今日新增会展资讯（剩余 {overflow_count} 项）", ""]
        for idx, item in enumerate(overflow, start=1):
            item_title = (item.get("title") or "").strip() or "未命名"
            url = item.get("url") or ""
            lines.append(f"{idx}. [{item_title}]({url})")
        summary_text = "\n".join(lines)
        summary_title = f"今日新增剩余 {overflow_count} 项"
        overflow_pushed = send_markdown(webhook_url, summary_title, summary_text, secret=secret)

    return {
        "actioncard_count": actioncard_count,
        "overflow_count": overflow_count,
        "overflow_pushed": overflow_pushed,
    }
