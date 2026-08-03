"""Onsite Club 分类案例库爬虫。

数据源：https://www.onsiteclub.com/category 服务端渲染 HTML 列表页，
分页 ``?page=N``（约 1555 页），默认按最新排序。每张卡片含一条指向
``/case/{slug}`` 的详情页链接。

本模块提供：
- ``fetch_category_cases(max_pages)``：抓取前 N 页的全部案例卡片（最新在前）。
- ``parse_category_page(html)``：解析单页 HTML，提取 slug/标题/链接/封面。
- ``enrich_case_detail(item)``：抓详情页补全标题/城市/品牌/行业/日期/封面
  （复用日历爬虫的 ``_extract_case_facts`` / ``_extract_cover``，详情页结构一致）。

「今日新增」判定由上层 category_monitor 用 ``seen_ids``（slug）增量对比完成：
分类页默认按最新排序，今日新增的案例一定出现在前几页，抓前 3 页即可覆盖。
"""
import logging
import re
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .base import fetch_with_retry, make_headers
from .onsiteclub_calendar import (
    BASE_URL,
    _city_from_slug,
    _city_from_title,
    _extract_case_facts,
    _extract_cover,
    classify_type,
)

logger = logging.getLogger("daily_news")

CATEGORY_URL = urljoin(BASE_URL, "category")
# 详情页链接形如 /case/some-slug，slug 不含斜杠
_CASE_HREF_RE = re.compile(r"^/case/(?P<slug>[^/]+)/?$")


def _extract_slug(href):
    """从 /case/{slug} 链接里取出 slug；不匹配返回 None。"""
    if not href:
        return None
    match = _CASE_HREF_RE.match(href.strip().split("#")[0].split("?")[0])
    return match.group("slug") if match else None


def _card_image(card_node):
    """取卡片容器内第一张 onsiteclub 封面图；找不到返回空串。"""
    for img in card_node.select("img"):
        src = img.get("src") or img.get("data-src") or ""
        if src:
            return src if src.startswith("http") else "https:" + src
    return ""


def _find_card_for_link(a_tag):
    """从案例链接 ``<a>`` 向上找最近的卡片级容器。

    卡片容器 = 包含封面图且兄弟节点结构相似的祖先；找不到时回退到直接父节点。
    """
    node = a_tag
    for _ in range(5):
        node = node.parent
        if node is None:
            return a_tag.parent
        if node.select("img"):
            return node
    return a_tag.parent


def parse_category_page(html, base_url=BASE_URL):
    """解析单页 HTML，返回案例卡片列表（按页面顺序，去重）。

    每条：{slug, title, url, image_url, type}
    标题优先取 ``<a>`` 的 title 属性 / img alt / 链接文本，详情页可再补全。
    """
    soup = BeautifulSoup(html, "html.parser")
    items, seen = [], set()
    for a in soup.find_all("a", href=True):
        slug = _extract_slug(a["href"])
        if not slug or slug in seen:
            continue
        seen.add(slug)
        url = urljoin(base_url, a["href"])
        # 标题：title 属性 > img alt > 链接文本
        title = (a.get("title") or "").strip()
        if not title:
            img = a.select_one("img")
            if img:
                title = (img.get("alt") or "").strip()
        if not title:
            title = " ".join(a.get_text(" ", strip=True).split())
        # 封面：链接内 img 优先，否则向上找卡片容器内的 img
        image_url = ""
        inner_img = a.select_one("img")
        if inner_img:
            src = inner_img.get("src") or inner_img.get("data-src") or ""
            image_url = src if src.startswith("http") else ("https:" + src if src else "")
        if not image_url:
            card = _find_card_for_link(a)
            image_url = _card_image(card)
        items.append({
            "slug": slug,
            "id": slug,  # 统一字段名，便于复用 diff_new_events
            "title": title,
            "url": url,
            "image_url": image_url,
            "type": classify_type(title),
            "city": "",
            "brand": "",
            "industry": "",
            "start": "",
            "end": "",
        })
    return items


def fetch_category_cases(max_pages=3, timeout=10, page_delay=0.8):
    """抓取分类页前 ``max_pages`` 页的全部案例（最新在前）。

    页面默认按最新排序，今日新增的案例一定出现在前几页；
    默认抓 3 页（约 60 条）足以覆盖单日新增。抓取间加短延时避免压力过大。
    返回去重后的案例列表（按 slug 去重，保留首次出现）。
    """
    def _fetch():
        all_items, seen = [], set()
        for page in range(1, max_pages + 1):
            try:
                resp = requests.get(
                    CATEGORY_URL,
                    params={"page": page},
                    headers=make_headers(referer=BASE_URL),
                    timeout=timeout,
                )
                resp.raise_for_status()
                page_items = parse_category_page(resp.text)
            except requests.RequestException as exc:
                logger.warning("[OnsiteClub分类] 第 %s 页抓取失败: %s", page, exc)
                break
            for item in page_items:
                if item["slug"] not in seen:
                    seen.add(item["slug"])
                    all_items.append(item)
            logger.info("[OnsiteClub分类] 第 %s 页解析 %s 条，累计 %s 条", page, len(page_items), len(all_items))
            if page < max_pages:
                time.sleep(page_delay)
        return all_items

    return fetch_with_retry(_fetch, "Onsite Club 分类案例")


def enrich_case_detail(item, timeout=10):
    """抓取案例详情页补全标题/城市/品牌/行业/日期/封面；失败时静默保留原字段。

    详情页结构与日历页一致（均 /case/{slug}），复用日历爬虫的解析函数。
    """
    if not item.get("url"):
        return item
    try:
        resp = requests.get(item["url"], headers=make_headers(referer=BASE_URL), timeout=timeout)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        facts = _extract_case_facts(soup)
        # 项目名称作为更干净的标题（列表页标题常混有日期/城市）
        name = (facts.get("项目名称") or "").strip()
        if name:
            item["title"] = name
        item["brand"] = (facts.get("项目品牌") or "").strip() or item.get("brand", "")
        item["industry"] = (facts.get("项目行业") or "").strip() or item.get("industry", "")

        # 日期：详情页「项目日期」形如 2026-08-01 ~ 2026-08-10
        date_text = (facts.get("项目日期") or "").strip()
        if date_text:
            dates = re.findall(r"\d{4}-\d{2}-\d{2}", date_text)
            if dates:
                item["start"] = dates[0]
                if len(dates) >= 2:
                    item["end"] = dates[1]
                elif "~" in date_text or "至" in date_text:
                    item["end"] = dates[0]

        # 城市：详情页「项目地点」优先，回退标题括号与 URL slug
        city = (facts.get("项目地点") or "").strip()
        if city:
            for known in {"上海", "北京", "广州", "深圳", "成都", "重庆", "杭州", "南京",
                          "武汉", "苏州", "西安", "天津", "长沙", "青岛", "大连", "海口",
                          "昆明", "沈阳", "厦门", "佛山", "无锡", "东莞", "哈尔滨", "合肥"}:
                if city.startswith(known):
                    item["city"] = known
                    break
        if not item.get("city"):
            item["city"] = _city_from_title(item.get("title", "")) or _city_from_slug(item.get("url", ""))

        item["image_url"] = _extract_cover(soup) or item.get("image_url", "")
        item["type"] = classify_type(item.get("title", ""))
    except requests.RequestException:
        pass
    return item


def enrich_cases(items, timeout=10, max_workers=6):
    """并发为案例抓取详情页补全字段。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    if not items:
        return items
    with ThreadPoolExecutor(max_workers=min(max_workers, len(items))) as executor:
        futures = [executor.submit(enrich_case_detail, item, timeout) for item in items]
        for future in as_completed(futures):
            future.result()
    return items
