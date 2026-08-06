"""Onsite Club 会展日历爬虫。

数据源：https://www.onsiteclub.com/calendar 日历页通过 FullCalendar 组件
按 ``/calendar_cases?start=..&end=..`` 接口拉取事件（返回 FullCalendar 事件数组），
每场会展对应一个 id、起止时间、标题与详情页 url。

本模块提供：
- ``fetch_calendar_events(year, month)``：抓取指定月份的会展（含跨月长期展）。
- ``enrich_event_detail(event)``：懒加载详情页，补充城市/品牌/行业/主题/封面图。
- ``classify_type(title, topics)``：按标题关键词与会展性质分类（快闪/慢闪/展览/发布/市集…）。

城市优先取详情页「项目地点」，其次标题括号（（北京）），最后退化为 URL slug。
"""

import calendar as _cal
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .base import fetch_with_retry, get_with_ssl_fallback, make_headers
from config import ONSITECLUB_ALLOW_INSECURE_SSL

BASE_URL = "https://www.onsiteclub.com/"
CALENDAR_API = urljoin(BASE_URL, "calendar_cases")

# 标题/URL slug 里的城市码（长码优先，避免 "SH" 误匹配 "SHANGXIA" 之类）
SLUG_CITY_MAP = [
    ("SHANGHAI", "上海"), ("BEIJING", "北京"), ("GUANGZHOU", "广州"),
    ("SHENZHEN", "深圳"), ("CHENGDU", "成都"), ("CHONGQING", "重庆"),
    ("HANGZHOU", "杭州"), ("NANJING", "南京"), ("WUHAN", "武汉"),
    ("SUZHOU", "苏州"), ("XI'AN", "西安"), ("XIAN", "西安"),
    ("NANNING", "南宁"), ("NINGBO", "宁波"), ("TIANJIN", "天津"),
    ("CHANGSHA", "长沙"), ("ZHENGZHOU", "郑州"), ("QINGDAO", "青岛"),
    ("DALIAN", "大连"), ("HAIKOU", "海口"), ("KUNMING", "昆明"),
    ("SHENYANG", "沈阳"), ("XIAMEN", "厦门"), ("FOSHAN", "佛山"),
    ("WUXI", "无锡"), ("DONGGUAN", "东莞"), ("HARBIN", "哈尔滨"),
    ("HEFEI", "合肥"), ("KUNSHAN", "昆山"),
]

# 类型分类：按优先级匹配标题关键词
TYPE_RULES = [
    ("慢闪", "慢闪空间"),
    ("快闪", "快闪店"),
    ("发布会", "发布会/首映"),
    ("首映", "发布会/首映"),
    ("艺术展", "艺术展"),
    ("展览", "展览"),
    ("展", "展览"),
    ("市集", "市集/嘉年华"),
    ("嘉年华", "市集/嘉年华"),
    ("主题店", "主题店/限时店"),
    ("限时店", "主题店/限时店"),
    ("概念店", "主题店/限时店"),
    ("精品店", "主题店/限时店"),
    ("秀场", "秀场/演出"),
    ("秀", "秀场/演出"),
    ("演出", "秀场/演出"),
]

_OTHER_TYPE = "其他/快闪展"


def classify_type(title, topics=None):
    """按标题关键词分类会展类型；无命中返回『其他/快闪展』。"""
    text = str(title or "")
    if not text:
        return _OTHER_TYPE
    for keyword, category in TYPE_RULES:
        if keyword in text:
            return category
    return _OTHER_TYPE


def month_range(year, month):
    """返回 (月初, 月末) 的 date 对象。"""
    first = date(year, month, 1)
    last = date(year, month, _cal.monthrange(year, month)[1])
    return first, last


def _normalize_event(raw):
    url = str(raw.get("url") or "").strip()
    return {
        "id": int(raw.get("id") or 0),
        "title": " ".join(str(raw.get("title") or "").split()),
        "start": str(raw.get("start") or ""),
        "end": str(raw.get("end") or ""),
        "url": urljoin(BASE_URL, url) if url else "",
        "type": classify_type(raw.get("title", "")),
        "city": "",
        "brand": "",
        "industry": "",
        "topics": [],
        "image_url": "",
    }


def _city_from_slug(url):
    upper = str(url or "").upper()
    for code, name in SLUG_CITY_MAP:
        if code in upper:
            return name
    return ""


def _city_from_title(title):
    text = str(title or "")
    if "（" in text and "）" in text:
        inner = text[text.rfind("（") + 1: text.rfind("）")]
        if inner and "品牌" not in inner and "主题" not in inner and len(inner) <= 4:
            return inner
    return ""


def parse_calendar_payload(payload):
    """把 FullCalendar 事件数组规范化为统一的会展列表，并按 id 去重。"""
    items, seen = [], set()
    for raw in payload or []:
        item = _normalize_event(raw)
        if not item["id"] or item["id"] in seen:
            continue
        seen.add(item["id"])
        items.append(item)
    return items


def _clean_text(node):
    return " ".join(node.get_text(" ", strip=True).split()) if node else ""


def _extract_case_facts(soup):
    """从详情页 .case-info 里提取 品牌/行业/日期/地点/名称。"""
    facts = {}
    for node in soup.select(".case-info .case-item"):
        label_node = node.select_one(".ctt")
        value_node = node.select_one(".vv")
        if not label_node:
            continue
        label = _clean_text(label_node).replace("：", "").replace(":", "")
        value = _clean_text(value_node)
        if value:
            facts[label] = value
    return facts


def _extract_cover(soup):
    for img in soup.select(".cc img"):
        src = img.get("src") or img.get("data-src") or ""
        if src and ("onsiteclub.com" in src or src.startswith("http")):
            return src if src.startswith("http") else "https:" + src
    return ""


def _extract_body_description(soup, max_chars=None):
    """只从详情页已知正文容器提取中文介绍。

    旧页面使用 ``.entry-content``，当前页面使用 ``.cc``。SEO meta、导航区、
    推荐案例和来源声明都不参与提取，避免把无关内容错配到当前会展。
    """
    boilerplate_markers = (
        "图片及内容来自", "图片来自", "内容来自网络", "出处见网络",
        "文字来自 AI", "文字来自AI", "AI信息自我辨识",
    )
    paragraphs = []
    for selector in (".entry-content", ".cc"):
        container = soup.select_one(selector)
        if not container:
            continue
        for node in container.select("p"):
            text = _clean_text(node)
            if any(marker in text for marker in boilerplate_markers):
                continue
            if re.fullmatch(r"[\d\s年月日./~～\-—至]+", text):
                continue
            # 当前 .cc 容器还包含标题和日期；正文段落明显更长。
            if selector == ".cc" and len(text) < 30:
                continue
            chinese_count = len(re.findall(r"[\u4e00-\u9fff]", text))
            if chinese_count < 6 or chinese_count / max(len(text), 1) < 0.15:
                continue
            paragraphs.append(text)
        if paragraphs:
            break

    description = "\n".join(dict.fromkeys(paragraphs)).strip()
    if not max_chars or len(description) <= max_chars:
        return description
    return description[:max_chars].rstrip("，。；;、,.!?！？:： \n") + "…"


def enrich_event_detail(item, timeout=10):
    """抓取详情页补充城市/品牌/行业/主题/封面图；失败时静默保留原有字段。"""
    if not item.get("url"):
        return item
    try:
        response = get_with_ssl_fallback(
            item["url"],
            headers=make_headers(referer=BASE_URL),
            timeout=timeout,
            allow_insecure_fallback=ONSITECLUB_ALLOW_INSECURE_SSL,
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        facts = _extract_case_facts(soup)
        item["brand"] = facts.get("项目品牌", "").strip() or item.get("brand", "")
        item["industry"] = facts.get("项目行业", "").strip() or item.get("industry", "")

        city = facts.get("项目地点", "").strip()
        if city:
            # 「上海 新天地广场」→ 上海；城市一般出现在地点文本开头
            for known in {entry[1] for entry in SLUG_CITY_MAP}:
                if city.startswith(known):
                    item["city"] = known
                    break
        if not item.get("city"):
            item["city"] = _city_from_title(item.get("title", "")) or _city_from_slug(item.get("url", ""))

        topics = [_clean_text(node) for node in soup.select(".topics a.tag, .topics span.tag")]
        item["topics"] = list(dict.fromkeys(t for t in topics if t))

        item["image_url"] = _extract_cover(soup) or item.get("image_url", "")
        item["description"] = _extract_body_description(soup)
        item["description_source"] = "entry_content"
    except requests.RequestException:
        pass
    return item


def fetch_calendar_events(year, month, timeout=10, max_workers=6):
    """抓取指定月份的全部会展（含跨月长期展）。

    返回去重后的会展列表；事件字段若来自上次缓存缺少详情，可自行调用 enrich_event_detail 补齐。
    """
    first, last = month_range(year, month)

    def _fetch():
        params = {"start": first.isoformat(), "end": last.isoformat()}
        response = get_with_ssl_fallback(
            CALENDAR_API,
            headers=make_headers(referer=BASE_URL),
            params=params,
            timeout=timeout,
            allow_insecure_fallback=ONSITECLUB_ALLOW_INSECURE_SSL,
        )
        response.raise_for_status()
        payload = response.json()
        return parse_calendar_payload(payload)

    return fetch_with_retry(_fetch, "Onsite Club 会展日历")


def enrich_events(events, timeout=10, max_workers=6):
    """并发为尚未补齐城市信息的会展抓取详情页。"""
    targets = [item for item in events if not item.get("city") or not item.get("image_url")]
    if not targets:
        return events
    with ThreadPoolExecutor(max_workers=min(max_workers, len(targets))) as executor:
        futures = [executor.submit(enrich_event_detail, item, timeout) for item in targets]
        for future in as_completed(futures):
            future.result()
    return events
