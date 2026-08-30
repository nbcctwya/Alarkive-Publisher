from __future__ import annotations

import asyncio
import io
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import UploadFile
from fastapi.responses import Response

from alarkive_publisher.web import app as web_app
from alarkive_publisher.web.publish_manager import (
    PublishManager,
    PublisherUnsupportedPlatformError,
)
from alarkive_publisher.web.publish_state import default_publish_state, mark_unpublished
from alarkive_publisher.web.storage import ImageData, get_post_detail, save_post


PNG = b"\x89PNG\r\n\x1a\nminimal test data"
PLATFORMS = ("xiaohongshu", "baijiahao", "wechat")


def wait_for(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not reached before timeout")


class _TemplateRequest:
    def url_for(self, name: str, **path_params: str) -> str:
        del path_params
        return "/" + name


class PublishUiStateTests(unittest.TestCase):
    def test_create_page_exposes_platform_prompts_and_marker_status(self) -> None:
        template = web_app.templates.get_template("create.html")
        rendered = template.render(request=_TemplateRequest(), form={})
        script = (web_app.STATIC_DIR / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="copy-xiaohongshu-prompt"', rendered)
        self.assertIn("复制小红书 Prompt", rendered)
        self.assertIn('id="copy-baijiahao-prompt"', rendered)
        self.assertIn("复制百家号 Prompt", rendered)
        self.assertIn('id="copy-wechat-prompt"', rendered)
        self.assertIn("复制小绿书 Prompt", rendered)
        self.assertNotIn('id="copy-ai-prompt"', rendered)
        self.assertNotIn("复制 AI 生成 Prompt", rendered)
        self.assertIn('id="baijiahao-marker-status"', rendered)
        for platform in ("xiaohongshu", "baijiahao", "wechat"):
            self.assertIn(f'id="{platform}-prompt-status"', rendered)
            self.assertIn(f'id="{platform}-prompt-fallback"', rendered)

        self.assertIn("selectedFiles.length", script)
        self.assertIn("function buildXiaohongshuPrompt()", script)
        self.assertIn("function buildBaijiahaoPrompt()", script)
        self.assertIn("function buildWechatPrompt()", script)
        self.assertIn("function copyPrompt(", script)
        self.assertIn("{ length: count }", script)
        self.assertIn("index + 1", script)
        self.assertIn("markerMapping", script)
        self.assertIn("第 \" + (index + 1) + \" 张图片 → [[image:", script)
        self.assertIn("[[image:", script)
        self.assertIn("如果我在这条消息中附上了图片", script)
        self.assertIn("如果这条消息没有附图", script)
        self.assertIn("优先使用全部图片", script)
        self.assertIn("不要为了平均分布图片而机械插入", script)
        self.assertIn("不要仅根据图片文件名猜测图片内容", script)
        self.assertIn("不要输出标题", script)
        self.assertIn("不要使用代码块", script)

        xiaohongshu_start = script.index("function buildXiaohongshuPrompt()")
        baijiahao_start = script.index("function buildBaijiahaoPrompt()")
        wechat_start = script.index("function buildWechatPrompt()")
        xiaohongshu_prompt = script[xiaohongshu_start:baijiahao_start]
        baijiahao_prompt = script[baijiahao_start:wechat_start]
        wechat_prompt = script[wechat_start:script.index("function fallbackCopy(")]

        for text in (
            "符合小红书的阅读习惯和注意力机制",
            "开头尽快进入主题",
            "段落尽量短",
            "像一个真正了解这件事的人在和读者分享",
            "不要输出任何图片占位符",
            "直接复制并粘贴进 Alarkive Publisher",
        ):
            self.assertIn(text, xiaohongshu_prompt)
        self.assertNotIn("[[image:", xiaohongshu_prompt)

        for text in (
            "微信公众号小绿书正文",
            "内容尽可能简洁、直观",
            "符合微信图文的阅读习惯",
            "不需要刻意追求小红书式的强情绪",
            "不要输出任何图片占位符",
            "直接复制并粘贴进 Alarkive Publisher",
        ):
            self.assertIn(text, wechat_prompt)
        self.assertNotIn("[[image:", wechat_prompt)

        self.assertIn("function buildBaijiahaoPrompt()", baijiahao_prompt)
        self.assertIn("markerList", baijiahao_prompt)
        self.assertIn("markerMapping", baijiahao_prompt)
        self.assertNotIn("[[image:4]]", baijiahao_prompt)
        self.assertIn("如果我在这条消息中附上了图片", script)
        self.assertIn("如果这条消息没有附图", script)
        self.assertIn("navigator.clipboard.writeText", script)
        self.assertIn("document.execCommand(\"copy\")", script)
        self.assertIn("复制失败，请手动复制下方 Prompt", script)
        self.assertIn("requiresImages && !selectedFiles.length", script)
        self.assertIn("✓ 小红书 Prompt 已复制", script)
        self.assertIn("✓ 百家号 Prompt 已复制", script)
        self.assertIn("✓ 小绿书 Prompt 已复制", script)
        self.assertIn("被重复引用", script)
        self.assertIn("未使用：图片", script)

    def _detail_context(self, root: Path) -> tuple[Path, dict]:
        package = save_post(
            "测试任务",
            {platform: "标题" for platform in PLATFORMS},
            {platform: "正文" for platform in PLATFORMS},
            [ImageData("image.png", PNG)],
            posts_root=root,
        ).directory
        post = get_post_detail(package.name, root)
        post["publish_state"] = default_publish_state()
        post["browser_open"] = False
        post["publisher_active"] = False
        return package, post

    def test_detail_shows_independent_publish_actions_and_renamed_all_action(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            _, post = self._detail_context(Path(temp))
            rendered = web_app.templates.get_template("detail.html").render(
                request=_TemplateRequest(), post=post
            )

        for label in ("发布小红书", "发布百家号", "发布小绿书", "发布全部"):
            self.assertIn(label, rendered)
        self.assertNotIn(">发布</button>", rendered)
        self.assertLess(rendered.index("发布全部"), rendered.index("平台内容"))
        self.assertGreater(rendered.index("发布小红书"), rendered.index("平台内容"))

    def test_detail_hides_new_actions_while_publisher_is_active(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            _, post = self._detail_context(Path(temp))
            post["publisher_active"] = True
            post["publish_state"]["published"] = True
            post["publish_state"]["workflow"]["status"] = "waiting"
            post["publish_state"]["workflow"]["current_platform"] = "baijiahao"
            post["publish_state"]["workflow"]["current_step"] = "uploading_images"
            rendered = web_app.templates.get_template("detail.html").render(
                request=_TemplateRequest(), post=post
            )

        self.assertIn("发布流程进行中", rendered)
        for label in ("发布小红书", "发布百家号", "发布小绿书", "发布全部"):
            self.assertNotIn(label, rendered)

    def test_single_platform_actions_remain_available_when_full_marker_is_set(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            _, post = self._detail_context(Path(temp))
            post["publish_state"]["published"] = True
            rendered = web_app.templates.get_template("detail.html").render(
                request=_TemplateRequest(), post=post
            )

        for label in ("发布小红书", "发布百家号", "发布小绿书"):
            self.assertIn(label, rendered)
        self.assertIn("重新置为未发布", rendered)
        self.assertNotIn("发布全部", rendered)

    def test_single_ready_detail_uses_end_browser_action(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            _, post = self._detail_context(Path(temp))
            post["publisher_active"] = True
            post["browser_open"] = True
            post["publish_state"]["published"] = True
            workflow = post["publish_state"]["workflow"]
            workflow["workflow_mode"] = "single"
            workflow["target_platform"] = "baijiahao"
            workflow["status"] = "waiting"
            workflow["current_platform"] = "baijiahao"
            workflow["current_step"] = "ready"
            workflow["platforms"]["baijiahao"]["status"] = "ready"
            rendered = web_app.templates.get_template("detail.html").render(
                request=_TemplateRequest(), post=post
            )

        self.assertIn("单平台：百家号", rendered)
        self.assertIn("结束流程并关闭浏览器", rendered)
        self.assertNotIn("继续到微信公众号", rendered)

    def test_single_platform_route_passes_target_to_manager(self) -> None:
        with patch.object(web_app.publish_manager, "start_platform_publish") as start:
            response = asyncio.run(
                web_app.publish_platform(None, "post-id", "xiaohongshu")  # type: ignore[arg-type]
            )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/posts/post-id")
        start.assert_called_once_with("post-id", "xiaohongshu")

    def test_single_platform_route_rejects_unknown_target(self) -> None:
        error_response = Response(status_code=400)
        with patch.object(
            web_app.publish_manager,
            "start_platform_publish",
            side_effect=PublisherUnsupportedPlatformError("不支持的平台：unknown"),
        ), patch.object(
            web_app,
            "_render_detail_error",
            return_value=error_response,
        ) as render_error:
            response = asyncio.run(
                web_app.publish_platform(None, "post-id", "unknown")  # type: ignore[arg-type]
            )

        self.assertIs(response, error_response)
        render_error.assert_called_once_with(
            None, "post-id", "不支持的平台：unknown", status_code=400
        )

    def test_mixed_upload_filters_unsupported_files(self) -> None:
        captured: dict = {}

        def fake_save_post(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(id="20260830-123456-abcd")

        images = [
            UploadFile(file=io.BytesIO(PNG), filename="a.png"),
            UploadFile(file=io.BytesIO(b"not an image"), filename="c.jpg"),
        ]
        with patch.object(web_app, "save_post", side_effect=fake_save_post):
            response = asyncio.run(
                web_app.create_post(
                    None,  # type: ignore[arg-type]
                    name="测试任务",
                    xiaohongshu_title="标题",
                    xiaohongshu_body="正文",
                    baijiahao_title="标题",
                    baijiahao_body="正文",
                    wechat_title="标题",
                    wechat_body="正文",
                    images=images,
                )
            )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/posts/20260830-123456-abcd")
        self.assertEqual([image.filename for image in captured["images"]], ["a.png"])

    def test_api_exposes_active_workflow_after_local_reset(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = save_post(
                "测试任务",
                {platform: "标题" for platform in PLATFORMS},
                {platform: "正文" for platform in PLATFORMS},
                [ImageData("image.png", PNG)],
                posts_root=root,
            ).directory
            release = threading.Event()

            def runner(post, project_root, controller):
                del post, project_root
                controller.step("xiaohongshu", "uploading_images", "上传")
                release.wait(timeout=2)
                controller.completed("完成")

            manager = PublishManager(root, workflow_runner=runner)
            manager.start_publish(package.name)
            wait_for(manager.has_active_workflow)

            with patch.object(web_app, "publish_manager", manager), patch.object(
                web_app,
                "get_post_detail",
                side_effect=lambda post_id: get_post_detail(post_id, root),
            ):
                state = asyncio.run(web_app.publish_state_api(package.name))
                self.assertTrue(state["publisher_active"])
                mark_unpublished(package)
                state = asyncio.run(web_app.publish_state_api(package.name))
                self.assertFalse(state["published"])
                self.assertTrue(state["publisher_active"])

                post = web_app._load_web_post(package.name)
                rendered = web_app.templates.get_template("detail.html").render(
                    request=_TemplateRequest(), post=post
                )
                self.assertIn("发布流程进行中", rendered)
                self.assertNotIn('action="/publish_post"', rendered)

            release.set()
            wait_for(lambda: not manager.has_active_workflow())
            with patch.object(web_app, "publish_manager", manager):
                state = asyncio.run(web_app.publish_state_api(package.name))
                self.assertFalse(state["publisher_active"])


if __name__ == "__main__":
    unittest.main()
