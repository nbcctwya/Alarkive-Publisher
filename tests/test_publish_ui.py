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

from alarkive_publisher.web import app as web_app
from alarkive_publisher.web.publish_manager import PublishManager
from alarkive_publisher.web.publish_state import mark_unpublished
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
    def test_create_page_exposes_inline_image_prompt_and_marker_status(self) -> None:
        template = web_app.templates.get_template("create.html")
        rendered = template.render(request=_TemplateRequest(), form={})
        script = (web_app.STATIC_DIR / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="copy-ai-prompt"', rendered)
        self.assertIn("复制 AI 生成 Prompt", rendered)
        self.assertIn('id="baijiahao-marker-status"', rendered)
        self.assertIn('id="prompt-status"', rendered)
        self.assertIn('id="ai-prompt-fallback"', rendered)
        self.assertIn("selectedFiles.length", script)
        self.assertIn("[[image:", script)
        self.assertIn("{ length: count }", script)
        self.assertIn("index + 1", script)
        self.assertIn("如果我在这条消息中附上了图片", script)
        self.assertIn("如果这条消息没有附图", script)
        self.assertIn('"第 " + (index + 1) + " 张图片 → [[image:"', script)
        self.assertIn("markerMapping", script)
        self.assertIn("不要输出标题", script)
        self.assertIn("不要使用代码块", script)
        self.assertIn("优先使用全部图片", script)
        self.assertIn("不要为了平均分布图片而机械插入", script)
        self.assertIn("不要仅根据图片文件名猜测图片内容", script)
        self.assertIn("只能使用以下 Alarkive Publisher 图片占位符", script)
        self.assertNotIn("const imageOrder", script)
        self.assertIn("navigator.clipboard.writeText", script)
        self.assertIn("复制失败，请手动复制下方 Prompt", script)
        self.assertIn("被重复引用", script)
        self.assertIn("未使用：图片", script)

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
