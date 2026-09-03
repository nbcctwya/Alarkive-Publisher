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
VARIANTS = ("public_long", "wechat_long", "wechat_short", "toutiao_short")


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
    def test_create_page_has_four_variant_prompts_and_no_xiaohongshu(self) -> None:
        template = web_app.templates.get_template("create.html")
        rendered = template.render(request=_TemplateRequest(), form={})
        script = (web_app.STATIC_DIR / "app.js").read_text(encoding="utf-8")

        for key in VARIANTS:
            self.assertIn(f'name="{key}_title"', rendered)
            self.assertIn(f'name="{key}_body"', rendered)
        for prompt_id in (
            "copy-public-long-prompt",
            "copy-wechat-long-prompt",
            "copy-wechat-short-prompt",
            "copy-toutiao-short-prompt",
        ):
            self.assertIn(f'id="{prompt_id}"', rendered)
        self.assertIn("公域长文（百家号 + 今日头条）", rendered)
        self.assertIn("微信长文", rendered)
        self.assertIn("微信图文 / 小绿书", rendered)
        self.assertIn("微头条", rendered)
        self.assertNotIn("小红书", rendered)
        self.assertNotIn("xiaohongshu", rendered)
        self.assertNotIn("暂未接入发布器", rendered)
        self.assertIn("function buildPublicLongPrompt()", script)
        self.assertIn("function buildWechatLongPrompt()", script)
        self.assertIn("function buildWechatShortPrompt()", script)
        self.assertIn("function buildToutiaoShortPrompt()", script)
        self.assertIn("[[image:", script)
        self.assertIn("navigator.clipboard.writeText", script)
        self.assertIn("document.execCommand(\"copy\")", script)

    def _detail_context(self, root: Path, variants: tuple[str, ...] = VARIANTS):
        package = save_post(
            "测试任务",
            {variant: "标题" for variant in variants},
            {variant: "正文" for variant in variants},
            [ImageData("image.png", PNG)],
            posts_root=root,
        ).directory
        post = get_post_detail(package.name, root)
        post["publish_state"] = default_publish_state()
        post["browser_open"] = False
        post["publisher_active"] = False
        return package, post

    def test_detail_separates_variant_content_and_platform_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            _, post = self._detail_context(Path(temp))
            rendered = web_app.templates.get_template("detail.html").render(
                request=_TemplateRequest(), post=post
            )

        for label in ("公域长文", "微信长文", "微信图文", "微头条"):
            self.assertIn(label, rendered)
        for label in ("发布百家号", "发布今日头条文章", "发布微信公众号长文", "发布微信图文", "发布微头条"):
            self.assertIn(label, rendered)
        self.assertIn("今日头条文章", rendered)
        self.assertIn("微信公众号长文", rendered)
        self.assertNotIn("Publisher 待接入", rendered)
        self.assertNotIn("小红书", rendered)
        self.assertNotIn("xiaohongshu", rendered)
        self.assertNotIn('>发布</button>', rendered)

    def test_detail_marks_targets_without_variant_as_no_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            _, post = self._detail_context(Path(temp), ("public_long",))
            rendered = web_app.templates.get_template("detail.html").render(
                request=_TemplateRequest(), post=post
            )

        self.assertIn("公域长文", rendered)
        self.assertIn("发布百家号", rendered)
        self.assertIn("发布今日头条文章", rendered)
        self.assertIn("微信图文", rendered)
        self.assertIn("无内容", rendered)
        self.assertNotIn("发布微信图文</button>", rendered)

    def test_wechat_long_alone_enables_single_and_full_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            _, post = self._detail_context(Path(temp), ("wechat_long",))
            rendered = web_app.templates.get_template("detail.html").render(
                request=_TemplateRequest(), post=post
            )

        self.assertIn("发布全部", rendered)
        self.assertIn("发布微信公众号长文</button>", rendered)
        self.assertNotIn("Publisher 待接入", rendered)
        self.assertNotIn("小红书", rendered)

    def test_detail_hides_actions_while_publisher_is_active(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            _, post = self._detail_context(Path(temp))
            post["publisher_active"] = True
            post["publish_state"]["workflow"]["status"] = "waiting"
            post["publish_state"]["workflow"]["current_platform"] = "baijiahao"
            rendered = web_app.templates.get_template("detail.html").render(
                request=_TemplateRequest(), post=post
            )

        self.assertIn("发布流程进行中", rendered)
        self.assertNotIn('action="/publish_post"', rendered)

    def test_micro_only_has_publish_action_and_single_workflow_label(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            _, post = self._detail_context(Path(temp), ("toutiao_short",))
            post["publish_state"]["workflow"].update(
                workflow_mode="single", target_platform="toutiao_micro"
            )
            rendered = web_app.templates.get_template("detail.html").render(
                request=_TemplateRequest(), post=post
            )
        self.assertTrue(post["has_available_publisher"])
        self.assertIn("发布微头条</button>", rendered)
        self.assertIn("单平台：微头条", rendered)
        self.assertIn('data-platform-implemented="true"', rendered)

    def test_published_detail_keeps_reset_action(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            _, post = self._detail_context(Path(temp), ("public_long", "wechat_short"))
            post["publish_state"]["published"] = True
            rendered = web_app.templates.get_template("detail.html").render(
                request=_TemplateRequest(), post=post
            )
        self.assertIn("重新置为未发布", rendered)
        self.assertNotIn("发布全部", rendered)

    def test_single_ready_detail_uses_end_browser_action(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            _, post = self._detail_context(Path(temp), ("public_long",))
            post["publisher_active"] = True
            post["browser_open"] = True
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
        # app.js owns the live continuation label; the server-side action is
        # still present and targets the shared continue endpoint.
        self.assertIn("continue_publish", rendered)

    def test_interrupted_detail_offers_resume_without_repeating_ready_platforms(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            _, post = self._detail_context(Path(temp))
            post["workflow_resumable"] = True
            workflow = post["publish_state"]["workflow"]
            workflow["status"] = "interrupted"
            workflow["platforms"]["baijiahao"]["status"] = "ready"
            rendered = web_app.templates.get_template("detail.html").render(
                request=_TemplateRequest(), post=post
            )

        self.assertIn("继续未完成流程", rendered)

    def test_active_interrupted_task_keeps_cancel_action(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            _, post = self._detail_context(Path(temp))
            post["publisher_active"] = True
            post["publisher_owns_post"] = True
            post["publish_state"]["workflow"]["status"] = "interrupted"
            rendered = web_app.templates.get_template("detail.html").render(
                request=_TemplateRequest(), post=post
            )
        self.assertIn("取消发布准备", rendered)
        self.assertIn('action="/cancel_publish"', rendered)

    def test_single_platform_route_passes_canonical_target_to_manager(self) -> None:
        with patch.object(web_app.publish_manager, "start_platform_publish") as start:
            response = asyncio.run(
                web_app.publish_platform(None, "post-id", "wechat_image")  # type: ignore[arg-type]
            )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/posts/post-id")
        start.assert_called_once_with("post-id", "wechat_image")

    def test_single_platform_route_rejects_unknown_target(self) -> None:
        error_response = Response(status_code=400)
        with patch.object(
            web_app.publish_manager,
            "start_platform_publish",
            side_effect=PublisherUnsupportedPlatformError("该平台 Publisher 尚未接入。"),
        ), patch.object(web_app, "_render_detail_error", return_value=error_response) as render_error:
            response = asyncio.run(
                web_app.publish_platform(None, "post-id", "unknown")  # type: ignore[arg-type]
            )
        self.assertIs(response, error_response)
        render_error.assert_called_once_with(
            None, "post-id", "该平台 Publisher 尚未接入。", status_code=400
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
                    public_long_title="标题",
                    public_long_body="正文",
                    wechat_long_title="",
                    wechat_long_body="",
                    wechat_short_title="",
                    wechat_short_body="",
                    toutiao_short_title="",
                    toutiao_short_body="",
                    images=images,
                )
            )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/posts/20260830-123456-abcd")
        self.assertEqual([image.filename for image in captured["images"]], ["a.png"])
        self.assertEqual(set(captured["titles"]), {"public_long", "wechat_long", "wechat_short", "toutiao_short"})

    def test_api_exposes_active_workflow_after_local_reset(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = save_post(
                "测试任务",
                {"public_long": "标题"},
                {"public_long": "正文"},
                [ImageData("image.png", PNG)],
                posts_root=root,
            ).directory
            release = threading.Event()

            def runner(post, project_root, controller):
                del post, project_root
                controller.step("baijiahao", "uploading_images", "上传")
                release.wait(timeout=2)
                controller.completed("完成")

            manager = PublishManager(root, workflow_runner=runner)
            manager.start_publish(package.name)
            wait_for(manager.has_active_workflow)
            with patch.object(web_app, "publish_manager", manager), patch.object(
                web_app, "get_post_detail", side_effect=lambda post_id: get_post_detail(post_id, root)
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
