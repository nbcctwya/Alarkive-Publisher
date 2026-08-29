from __future__ import annotations

import unittest

from alarkive_publisher.renderer import (
    render_for_platform,
    render_html,
    render_plain,
)


class MarkdownRendererTests(unittest.TestCase):
    def test_plain_renderer_removes_format_markers_but_keeps_structure(self) -> None:
        source = (
            "## 一个标题\n\n"
            "普通内容。\n\n"
            "**64GB：甜点**\n\n"
            "*轻量斜体* 和 `python main.py`\n\n"
            "- A\n- B\n\n"
            "1. 第一\n2. 第二\n\n"
            "> 引用内容\n\n"
            "详情见 [OpenAI](https://openai.com)。"
        )

        rendered = render_plain(source)

        self.assertEqual(
            rendered,
            "一个标题\n\n"
            "普通内容。\n\n"
            "64GB：甜点\n\n"
            "轻量斜体 和 python main.py\n\n"
            "• A\n• B\n\n"
            "1. 第一\n2. 第二\n\n"
            "「引用内容」\n\n"
            "详情见 OpenAI（https://openai.com）。",
        )
        self.assertNotIn("**", rendered)
        self.assertNotIn("##", rendered)

    def test_html_renderer_preserves_safe_semantics(self) -> None:
        source = (
            "## 一个标题\n\n"
            "**64GB：甜点** and *italic*\n\n"
            "- Qwen\n- GLM\n\n"
            "详情见 [OpenAI](https://openai.com)。"
        )

        rendered = render_html(source)

        self.assertIn("<h2>一个标题</h2>", rendered)
        self.assertIn("<strong>64GB：甜点</strong>", rendered)
        self.assertIn("<em>italic</em>", rendered)
        self.assertIn("<ul>", rendered)
        self.assertIn("<li>", rendered)
        self.assertIn('<a href="https://openai.com">OpenAI</a>', rendered)
        self.assertNotIn("**", rendered)

    def test_unicode_and_paragraph_breaks_are_preserved(self) -> None:
        source = "第一段。\n\n**重点 🚀**\n\n✅ 可以运行"

        self.assertEqual(render_plain(source), "第一段。\n\n重点 🚀\n\n✅ 可以运行")

    def test_raw_html_is_not_emitted_as_executable_html(self) -> None:
        source = '<script>alert("test")</script>\n\n**正常内容**'

        rendered = render_html(source)

        self.assertNotIn("<script>", rendered.lower())
        self.assertNotIn("</script>", rendered.lower())
        self.assertIn("正常内容", rendered)

    def test_platform_policy(self) -> None:
        source = "**重点**"

        xiaohongshu = render_for_platform("xiaohongshu", source)
        baijiahao = render_for_platform("baijiahao", source)
        wechat = render_for_platform("wechat", source)

        self.assertEqual(xiaohongshu.text, "重点")
        self.assertIsNone(xiaohongshu.html)
        self.assertEqual(baijiahao.text, "重点")
        self.assertEqual(baijiahao.html, "<p><strong>重点</strong></p>")
        self.assertEqual(wechat.text, "重点")
        self.assertIsNone(wechat.html)
