import unittest
import io
from unittest import mock

from bs4 import BeautifulSoup
from PIL import Image

from crawlers import onsiteclub_calendar
import dd_monitor
from notifier_dingtalk import (
    _build_actioncard_banner,
    build_actioncard_text,
    summarize_description,
)


class BodyDescriptionTests(unittest.TestCase):
    def test_extracts_only_chinese_entry_content_paragraphs(self):
        soup = BeautifulSoup("""
            <html><head><meta name="description" content="错误的 SEO 文案"></head><body>
              <p>页面其他区域的中文推广文案不应该被提取。</p>
              <div class="entry-content">
                <p>This English paragraph should be ignored completely.</p>
                <p>本次展览围绕城市公共空间展开，通过装置与影像讨论人与城市的关系。</p>
                <p>现场还设置互动体验区域，观众可以参与作品的共同创作。</p>
              </div>
            </body></html>
        """, "html.parser")

        description = onsiteclub_calendar._extract_body_description(soup)

        self.assertIn("本次展览围绕城市公共空间展开", description)
        self.assertIn("现场还设置互动体验区域", description)
        self.assertNotIn("SEO", description)
        self.assertNotIn("推广文案", description)
        self.assertNotIn("English", description)

    def test_does_not_fall_back_to_meta_description(self):
        soup = BeautifulSoup(
            '<meta name="description" content="SEO 介绍"><div class="entry-content"><p>Short</p></div>',
            "html.parser",
        )
        self.assertEqual(onsiteclub_calendar._extract_body_description(soup), "")

    def test_current_cc_container_excludes_source_disclaimers(self):
        soup = BeautifulSoup("""
            <div class="cc">
              <p>2026年08月04日-2026年08月20日</p>
              <p>项目以城市声音为线索，通过互动装置构建可以共同参与的公共空间。</p>
              <p>图片及内容来自网络及品牌公开信息，出处见网络</p>
              <p>文字来自 AI 编辑完成，AI信息自我辨识</p>
            </div>
            <div class="info"><p>另一个推荐案例的介绍绝不能混入当前项目。</p></div>
        """, "html.parser")

        description = onsiteclub_calendar._extract_body_description(soup)

        self.assertIn("项目以城市声音为线索", description)
        self.assertNotIn("图片及内容来自", description)
        self.assertNotIn("AI信息", description)
        self.assertNotIn("另一个推荐案例", description)

    def test_each_detail_response_stays_with_its_own_event(self):
        def response_for(url, **_kwargs):
            response = mock.Mock()
            response.raise_for_status.return_value = None
            unique_text = "北京项目通过声音装置呈现城市记忆与公共生活。" if "beijing" in url else \
                "上海项目围绕可持续材料展开沉浸式空间实验。"
            response.text = f'<div class="entry-content"><p>{unique_text}</p></div>'
            return response

        first = {"url": "https://www.onsiteclub.com/case/beijing", "title": "北京项目"}
        second = {"url": "https://www.onsiteclub.com/case/shanghai", "title": "上海项目"}
        with mock.patch("crawlers.onsiteclub_calendar.requests.get", side_effect=response_for):
            onsiteclub_calendar.enrich_event_detail(first)
            onsiteclub_calendar.enrich_event_detail(second)

        self.assertIn("北京项目", first["description"])
        self.assertNotIn("上海项目", first["description"])
        self.assertIn("上海项目", second["description"])
        self.assertNotIn("北京项目", second["description"])
        self.assertEqual(first["description_source"], "entry_content")
        self.assertEqual(second["description_source"], "entry_content")

    def test_old_meta_cache_is_refreshed_and_replaced(self):
        events = [{"id": 7, "url": "https://example.com/case/7", "title": "案例七"}]
        state = {"cache": {"7": {"description": "旧的 SEO 简介"}}}

        def enrich(item):
            item["description"] = "这是来自详情页正文的项目介绍，内容与案例七直接相关。"
            item["description_source"] = "entry_content"
            return item

        with mock.patch("crawlers.onsiteclub_calendar.enrich_event_detail", side_effect=enrich):
            dd_monitor.enrich_new_events(events, state, max_workers=1)

        self.assertNotIn("SEO", events[0]["description"])
        self.assertEqual(state["cache"]["7"]["description_source"], "entry_content")


class ActionCardDescriptionTests(unittest.TestCase):
    def test_summary_collapses_whitespace_and_truncates_naturally(self):
        text = "第一段介绍。\n\n" + "内容" * 45
        summary = summarize_description(text, limit=80)
        self.assertNotIn("\n", summary)
        self.assertTrue(summary.endswith("…"))
        self.assertLessEqual(len(summary), 81)

    def test_card_orders_cover_title_description_and_metadata(self):
        item = {
            "title": "城市艺术展",
            "image_url": "https://example.com/cover.jpg",
            "description": "展览通过影像和装置讨论城市更新中的公共生活。",
            "city": "上海",
            "brand": "示例品牌",
            "type": "艺术展览",
            "industry": "文化艺术",
            "start_date": "2026-08-04",
            "end_date": "2026-08-20",
        }

        text = build_actioncard_text(item)

        cover_pos = text.index("![城市艺术展]")
        title_pos = text.index("### 城市艺术展")
        description_pos = text.index("**项目简介**")
        meta_pos = text.index("**地点** 上海 · **日期** 2026-08-04 — 2026-08-20")
        self.assertLess(cover_pos, title_pos)
        self.assertLess(title_pos, description_pos)
        self.assertLess(description_pos, meta_pos)
        self.assertIn("**品牌** 示例品牌 · **类型** 艺术展览 · **行业** 文化艺术", text)

    def test_card_limits_description_to_about_150_characters(self):
        description = ("完整正文" * 40) + "\n" + ("第二段内容" * 30)
        text = build_actioncard_text({"title": "正文测试", "description": description})
        summary = summarize_description(description, limit=150)
        self.assertIn(summary, text)
        self.assertTrue(summary.endswith("…"))
        self.assertLessEqual(len(summary), 151)
        self.assertNotIn(description, text)

    def test_description_prefers_a_natural_sentence_break(self):
        description = ("前段内容连续展开，" * 12) + "这里形成完整句子。" + ("后续内容" * 30)
        summary = summarize_description(description, limit=150)
        self.assertTrue(summary.endswith("。…"))
        self.assertNotIn("后续内容", summary)

    def test_actioncard_banner_uses_shallow_landscape_ratio(self):
        source = io.BytesIO()
        Image.new("RGB", (500, 900), "red").save(source, format="PNG")

        banner_data = _build_actioncard_banner(source.getvalue())

        with Image.open(io.BytesIO(banner_data)) as banner:
            self.assertEqual(banner.size, (1200, 540))
            self.assertEqual(banner.format, "JPEG")

    def test_card_omits_missing_metadata_without_empty_placeholders(self):
        text = build_actioncard_text({
            "title": "单日活动",
            "description": "这是一段与活动内容直接相关的简短项目介绍。",
            "start": "2026-08-04",
            "end": "2026-08-04",
            "brand": "待定",
            "type": "其他",
        })
        self.assertIn("**日期** 2026-08-04", text)
        self.assertNotIn("**地点**", text)
        self.assertNotIn("**品牌**", text)
        self.assertNotIn("**类型**", text)


if __name__ == "__main__":
    unittest.main()
