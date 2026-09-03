from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from alarkive_publisher.content import ContentVariant, PostContent
from alarkive_publisher.publisher_common import PublisherError
from alarkive_publisher.inline_images import ImageBlock, InlineImageError, TextBlock
from alarkive_publisher.wechat_article import _article_blocks, _ensure_empty, _upload_image, _verify
from alarkive_publisher.workflow import run_single_platform_workflow


class WechatArticleTests(unittest.TestCase):
    def test_markers_follow_document_order_then_append_unused_in_package_order(self):
        content = ContentVariant("标题", "开头\n[[image:4]]\n中间\n[[image:2]]\n结尾", tuple(Path(f"{n}.png") for n in range(1, 5)))
        blocks = _article_blocks(content)
        self.assertEqual([b.index for b in blocks if isinstance(b, ImageBlock)], [4, 2, 1, 3])
        self.assertEqual("".join(b.text for b in blocks if isinstance(b, TextBlock)), "开头\n中间\n结尾")

    def test_no_markers_adjacent_markers_and_no_images(self):
        images = (Path("one.png"), Path("two.png"))
        for body, paths, expected in (("正文", images, [1, 2]), ("[[image:2]]\n[[image:1]]\n正文", images, [2, 1]), ("正文", (), [])):
            with self.subTest(body=body):
                blocks = _article_blocks(ContentVariant("标题", body, paths))
                self.assertEqual([b.index for b in blocks if isinstance(b, ImageBlock)], expected)

    def test_duplicate_and_out_of_range_markers_are_rejected(self):
        for body in ("正文\n[[image:1]]\n[[image:1]]", "正文\n[[image:0]]", "正文\n[[image:2]]"):
            with self.subTest(body=body), self.assertRaises(InlineImageError):
                _article_blocks(ContentVariant("标题", body, (Path("one.png"),)))

    def test_inline_verifier_checks_exact_text_prefix_of_each_image(self):
        state = {"text": "开头中间结尾", "images": [
            {"src": "four", "loaded": True, "before": "开头"},
            {"src": "two", "loaded": True, "before": "开头中间"},
            {"src": "one", "loaded": True, "before": "开头中间结尾"},
        ]}
        with patch("alarkive_publisher.wechat_article._title_value", return_value="标题"), patch(
            "alarkive_publisher.wechat_article._snapshot", return_value=state
        ):
            _verify(Mock(), "标题", "开头中间结尾", ["four", "two", "one"], ["开头", "开头中间", "开头中间结尾"])
            with self.assertRaisesRegex(PublisherError, "正文位置"):
                _verify(Mock(), "标题", "开头中间结尾", ["four", "two", "one"], ["开头", "开头", "开头中间结尾"])

    def test_upload_waits_for_later_request_even_when_preview_is_stable(self):
        page = Mock()
        callbacks = {}
        now = [0.0]
        second_finished = [False]
        requests = [Mock(url="https://mp.weixin.qq.com/cgi-bin/filetransfer?action=upload_material", method="POST") for _ in range(2)]
        for index, request in enumerate(requests, 1):
            request.response.return_value.ok = True
            request.response.return_value.json.return_value = {"base_resp": {"ret": 0}, "content": str(index)}
        page.on.side_effect = lambda event, callback: callbacks.update({event: callback})
        page.remove_listener.side_effect = lambda event, callback: callbacks.pop(event)

        def choose(value, **kwargs):
            if value:
                for request in requests:
                    callbacks["request"](request)
                callbacks["requestfinished"](requests[0])

        def tick(milliseconds):
            now[0] += milliseconds / 1000
            if now[0] >= 5 and not second_finished[0]:
                second_finished[0] = True
                callbacks["requestfinished"](requests[1])

        page.locator.return_value.set_input_files.side_effect = choose
        page.wait_for_timeout.side_effect = tick

        def snapshot(_):
            current = "2" if second_finished[0] else "1"
            return {"images": [{"file_id": current, "src": "https://image/" + current, "loaded": True}]}

        with patch("alarkive_publisher.wechat_article.time.monotonic", side_effect=lambda: now[0]), patch(
            "alarkive_publisher.wechat_article._snapshot", side_effect=snapshot
        ), patch("alarkive_publisher.wechat_article._title", return_value=Mock()):
            source = _upload_image(page, Path("image.png"), 1, [])
        self.assertEqual(source, "https://image/2")
        self.assertGreaterEqual(now[0], 7)
        self.assertEqual(callbacks, {})

    def test_single_platform_uses_only_wechat_article_and_waits_before_close(self):
        content = ContentVariant("微信长文", "正文", ())
        post = PostContent(Path("."), "id", "name", "date", wechat_long=content)
        browser, context, page, editor, controller = (Mock() for _ in range(5))
        events = []
        controller.ready.side_effect = lambda *args: events.append("ready")
        context.close.side_effect = lambda: events.append("close")
        with patch("alarkive_publisher.workflow.start_browser", return_value=(browser, context, page)), patch(
            "alarkive_publisher.workflow.run_wechat_article", return_value=editor
        ) as article, patch("alarkive_publisher.workflow.run_wechat") as sticker, patch(
            "alarkive_publisher.workflow.run_baijiahao"
        ) as baijiahao, patch("alarkive_publisher.workflow.run_toutiao_article") as toutiao:
            run_single_platform_workflow(post, Path("."), "wechat_article", controller)
        article.assert_called_once_with(page, post, controller)
        sticker.assert_not_called()
        baijiahao.assert_not_called()
        toutiao.assert_not_called()
        self.assertEqual(events, ["ready", "close"])
        self.assertEqual(controller.ready.call_args.args[0], "wechat_article")

    def test_missing_wechat_long_fails_before_browser(self):
        post = PostContent(Path("."), "id", "name", "date", public_long=ContentVariant("公域", "正文", ()))
        with patch("alarkive_publisher.workflow.start_browser") as start:
            with self.assertRaisesRegex(ValueError, "不包含微信公众号长文"):
                run_single_platform_workflow(post, Path("."), "wechat_article", Mock())
        start.assert_not_called()

    def test_restored_article_is_never_overwritten(self):
        for title, text, images in (("旧标题", "", []), ("", "旧正文", []), ("", "", [{}])):
            with self.subTest(title=title, text=text), patch(
                "alarkive_publisher.wechat_article._title_value", return_value=title
            ), patch("alarkive_publisher.wechat_article._snapshot", return_value={"text": text, "images": images}):
                with self.assertRaisesRegex(PublisherError, "已有内容"):
                    _ensure_empty(Mock())

    def test_verifier_rejects_missing_extra_reordered_or_unloaded_content(self):
        good = {"text": "首段尾段", "images": [{"src": "one", "loaded": True, "before": "首段尾段"}]}
        cases = [
            {**good, "text": "首段"},
            {**good, "text": "首段尾段额外"},
            {**good, "images": []},
            {**good, "images": good["images"] * 2},
            {**good, "images": [{"src": "wrong", "loaded": True, "before": "首段尾段"}]},
            {**good, "images": [{"src": "one", "loaded": False, "before": "首段尾段"}]},
            {**good, "images": [{"src": "one", "loaded": True, "before": "首段"}]},
        ]
        with patch("alarkive_publisher.wechat_article._title_value", return_value="标题"):
            with patch("alarkive_publisher.wechat_article._snapshot", return_value=good):
                _verify(Mock(), "标题", "首段尾段", ["one"])
            for state in cases:
                with self.subTest(state=state), patch("alarkive_publisher.wechat_article._snapshot", return_value=state):
                    with self.assertRaises(PublisherError):
                        _verify(Mock(), "标题", "首段尾段", ["one"])


if __name__ == "__main__":
    unittest.main()
