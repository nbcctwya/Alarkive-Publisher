from __future__ import annotations

import copy
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Callable
from unittest.mock import patch

from alarkive_publisher.web.publish_manager import (
    PublishManager,
    PublisherBusyError,
    PublisherNotWaitingError,
    PublisherUnsupportedPlatformError,
)
from alarkive_publisher.web.publish_state import (
    load_publish_state,
    mark_interrupted,
    mark_published,
    mark_unpublished,
    update_workflow,
)
from alarkive_publisher.web.storage import ImageData, save_post


PLATFORMS = ("xiaohongshu", "baijiahao", "wechat")
PNG = b"\x89PNG\r\n\x1a\nminimal test data"


def wait_for(predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not reached before timeout")


class PublishManagerTests(unittest.TestCase):
    def _make_post(self, root: Path, name: str = "测试任务") -> Path:
        return save_post(
            name,
            {platform: f"{platform} title" for platform in PLATFORMS},
            {platform: "正文" for platform in PLATFORMS},
            [ImageData("image.png", PNG)],
            posts_root=root,
        ).directory

    def test_fake_workflow_runs_running_waiting_continue_completed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = self._make_post(root)

            def fake_runner(post, project_root, controller):
                controller.step("xiaohongshu", "checking_login", "检查登录")
                controller.wait_for_user(
                    "xiaohongshu", "login", "需要登录", "继续"
                )
                controller.completed("完成")

            manager = PublishManager(root, workflow_runner=fake_runner)
            manager.start_publish(package.name)
            wait_for(
                lambda: manager.get_publish_state(package.name)["workflow"]["status"]
                == "waiting"
            )
            manager.continue_publish(package.name)
            with self.assertRaises(PublisherNotWaitingError):
                manager.continue_publish(package.name)
            wait_for(
                lambda: manager.get_publish_state(package.name)["workflow"]["status"]
                == "completed"
            )

            state = manager.get_publish_state(package.name)
            self.assertTrue(state["published"])
            self.assertEqual(state["workflow"]["status"], "completed")

    def test_single_platform_workflow_only_targets_one_platform(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = self._make_post(root)
            calls: list[str] = []

            def fake_platform_runner(post, project_root, platform, controller):
                del post, project_root
                calls.append(platform)
                controller.ready(platform, f"{platform} ready", "结束")
                controller.completed("完成")

            manager = PublishManager(root, platform_workflow_runner=fake_platform_runner)
            manager.start_platform_publish(package.name, "baijiahao")
            wait_for(
                lambda: manager.get_publish_state(package.name)["workflow"]["status"]
                == "waiting"
            )

            state = manager.get_publish_state(package.name)
            self.assertEqual(calls, ["baijiahao"])
            self.assertFalse(state["published"])
            self.assertEqual(state["workflow"]["workflow_mode"], "single")
            self.assertEqual(state["workflow"]["target_platform"], "baijiahao")
            self.assertEqual(
                [
                    state["workflow"]["platforms"][platform]["status"]
                    for platform in PLATFORMS
                ],
                ["pending", "ready", "pending"],
            )
            self.assertTrue(manager.has_active_workflow())
            with self.assertRaises(PublisherBusyError):
                manager.start_publish(package.name)
            with self.assertRaises(PublisherBusyError):
                manager.start_platform_publish(package.name, "wechat")

            manager.continue_publish(package.name)
            wait_for(lambda: not manager.has_active_workflow())
            self.assertEqual(
                manager.get_publish_state(package.name)["workflow"]["status"],
                "completed",
            )
            self.assertFalse(manager.get_publish_state(package.name)["published"])

    def test_interrupted_wait_is_restored_for_the_live_job(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = self._make_post(root)

            def fake_runner(post, project_root, controller):
                del post, project_root
                controller.ready("baijiahao", "百家号已准备完成", "继续")
                controller.completed("完成")

            manager = PublishManager(root, workflow_runner=fake_runner)
            manager.start_publish(package.name)
            wait_for(lambda: manager.get_publish_state(package.name)["workflow"]["status"] == "waiting")
            mark_interrupted(package)

            restored = manager.reconcile_post_if_needed(package.name)
            self.assertEqual(restored["workflow"]["status"], "waiting")
            self.assertIsNone(restored["workflow"]["error"])
            manager.continue_publish(package.name)
            wait_for(lambda: not manager.has_active_workflow())

    def test_failed_full_workflow_resumes_without_repeating_ready_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            variants = ("public_long", "wechat_long", "wechat_short", "toutiao_short")
            package = save_post(
                "恢复流程",
                {variant: variant for variant in variants},
                {variant: "正文" for variant in variants},
                [ImageData("image.png", PNG)],
                posts_root=root,
            ).directory
            manager = PublishManager(root)
            mark_published(package)
            for target in ("baijiahao", "toutiao_article"):
                update_workflow(
                    package, platform=target, platform_status="ready",
                    platform_message=target + " ready",
                )
            update_workflow(
                package, status="failed", current_platform="wechat_article",
                current_step="login", message="browser closed",
                platform="wechat_article", platform_status="failed",
                error={"platform": "wechat_article", "step": "login", "type": "Error", "message": "browser closed"},
            )
            calls = []

            def resumed(post, project_root, controller, *, on_browser_started, skip_targets):
                del post, project_root, on_browser_started
                calls.append(skip_targets)
                controller.completed("完成")

            with patch("alarkive_publisher.workflow.run_publisher_workflow", resumed):
                manager.start_publish(package.name)
                wait_for(lambda: not manager.has_active_workflow())

            self.assertEqual(calls, [("baijiahao", "toutiao_article")])
            state = manager.get_publish_state(package.name)
            self.assertEqual(state["workflow"]["platforms"]["baijiahao"]["status"], "ready")
            self.assertEqual(state["workflow"]["platforms"]["toutiao_article"]["status"], "ready")
            self.assertTrue(state["published"])

    def test_single_platform_does_not_require_or_change_full_publish_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = self._make_post(root)
            mark_published(package)

            def fake_platform_runner(post, project_root, platform, controller):
                del post, project_root
                controller.completed(f"{platform} 完成")

            manager = PublishManager(root, platform_workflow_runner=fake_platform_runner)
            with self.assertRaisesRegex(PublisherUnsupportedPlatformError, "从 Web 发布入口移除"):
                manager.start_platform_publish(package.name, "xiaohongshu")
            self.assertFalse(manager.has_active_workflow())
            state = manager.get_publish_state(package.name)
            self.assertTrue(state["published"])
            self.assertIsNotNone(state["published_at"])

    def test_single_platform_rejects_unknown_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = self._make_post(root)
            manager = PublishManager(root, workflow_runner=lambda *args: None)

            with self.assertRaises(PublisherUnsupportedPlatformError):
                manager.start_platform_publish(package.name, "unknown")
            self.assertFalse(manager.has_active_workflow())
            self.assertFalse(manager.get_publish_state(package.name)["published"])

    def test_single_platform_rejects_target_missing_from_package(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = save_post(
                "只发百家号",
                {"baijiahao": "标题"},
                {"baijiahao": "正文"},
                [ImageData("image.png", PNG)],
                posts_root=root,
            ).directory
            manager = PublishManager(root, workflow_runner=lambda *args: None)

            with self.assertRaisesRegex(PublisherUnsupportedPlatformError, "不包含微信图文"):
                manager.start_platform_publish(package.name, "wechat")
            self.assertFalse(manager.has_active_workflow())

    def test_full_workflow_rejects_xiaohongshu_only_package(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = save_post(
                "只发小红书",
                {"xiaohongshu": "标题"},
                {"xiaohongshu": "正文"},
                [ImageData("image.png", PNG)],
                posts_root=root,
            ).directory
            manager = PublishManager(root, workflow_runner=lambda *args: None)

            with self.assertRaisesRegex(PublisherUnsupportedPlatformError, "没有可用于完整发布流程"):
                manager.start_publish(package.name)
            self.assertFalse(manager.has_active_workflow())
            self.assertFalse(load_publish_state(package)["published"])

    def test_single_platform_failure_marks_only_target_and_releases_job(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = self._make_post(root)

            def fake_platform_runner(post, project_root, platform, controller):
                del post, project_root
                controller.step(platform, "uploading_images", "上传图片")
                raise RuntimeError("百家号准备失败")

            manager = PublishManager(root, platform_workflow_runner=fake_platform_runner)
            manager.start_platform_publish(package.name, "baijiahao")
            wait_for(
                lambda: manager.get_publish_state(package.name)["workflow"]["status"]
                == "failed"
            )
            wait_for(lambda: not manager.has_active_workflow())

            state = manager.get_publish_state(package.name)
            self.assertFalse(state["published"])
            self.assertEqual(state["workflow"]["platforms"]["baijiahao"]["status"], "failed")
            self.assertEqual(state["workflow"]["platforms"]["xiaohongshu"]["status"], "pending")
            self.assertEqual(state["workflow"]["platforms"]["wechat"]["status"], "pending")
            self.assertEqual(state["workflow"]["error"]["message"], "百家号准备失败")

    def test_single_platform_manual_browser_close_releases_job(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = self._make_post(root)

            class FakePage:
                def __init__(self) -> None:
                    self.closed = False

                def is_closed(self) -> bool:
                    return self.closed

            class FakeContext:
                def __init__(self, page) -> None:
                    self.pages = [page]
                    self.close_calls = 0

                def close(self) -> None:
                    self.close_calls += 1

            class FakePlaywright:
                def __init__(self) -> None:
                    self.stop_calls = 0

                def stop(self) -> None:
                    self.stop_calls += 1

            page = FakePage()
            context = FakeContext(page)
            playwright = FakePlaywright()

            def fake_single_workflow(
                post,
                project_root,
                platform,
                controller,
                *,
                on_browser_started,
            ):
                del post, project_root
                on_browser_started(playwright, context, page)
                controller.wait_for_user(platform, "ready", "检查", "结束")

            manager = PublishManager(root)
            with patch(
                "alarkive_publisher.workflow.run_single_platform_workflow",
                fake_single_workflow,
            ):
                manager.start_platform_publish(package.name, "wechat")
                wait_for(
                    lambda: manager.get_publish_state(package.name)["workflow"]["status"]
                    == "waiting"
                )
                page.closed = True
                wait_for(
                    lambda: manager.get_publish_state(package.name)["workflow"]["status"]
                    == "failed"
                )
                wait_for(lambda: not manager.has_active_workflow())

            self.assertFalse(manager.browser_open_for(package.name))
            self.assertEqual(context.close_calls, 0)
            self.assertEqual(playwright.stop_calls, 1)

    def test_only_one_task_can_own_the_publisher(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package_a = self._make_post(root, "任务 A")
            package_b = self._make_post(root, "任务 B")

            def fake_runner(post, project_root, controller):
                controller.wait_for_user("xiaohongshu", "ready", "检查", "继续")
                controller.completed("完成")

            manager = PublishManager(root, workflow_runner=fake_runner)
            manager.start_publish(package_a.name)
            wait_for(
                lambda: manager.get_publish_state(package_a.name)["workflow"]["status"]
                == "waiting"
            )

            with self.assertRaises(PublisherBusyError):
                manager.start_publish(package_b.name)

            self.assertFalse(load_publish_state(package_b)["published"])
            manager.continue_publish(package_a.name)
            wait_for(lambda: not manager.has_active_workflow())

    def test_continue_has_no_effect_when_workflow_is_not_waiting(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = self._make_post(root)
            release = threading.Event()

            def fake_runner(post, project_root, controller):
                controller.step("xiaohongshu", "uploading_images", "上传图片")
                release.wait(timeout=2)
                controller.completed("完成")

            manager = PublishManager(root, workflow_runner=fake_runner)
            manager.start_publish(package.name)
            wait_for(
                lambda: manager.get_publish_state(package.name)["workflow"]["current_step"]
                == "uploading_images"
            )
            before = manager.get_publish_state(package.name)

            with self.assertRaises(PublisherNotWaitingError):
                manager.continue_publish(package.name)

            self.assertEqual(manager.get_publish_state(package.name), before)
            release.set()
            wait_for(lambda: not manager.has_active_workflow())

    def test_workflow_failure_keeps_published_true(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = self._make_post(root)

            def fake_runner(post, project_root, controller):
                controller.step("baijiahao", "uploading_images", "上传图片")
                raise RuntimeError("test failure")

            manager = PublishManager(root, workflow_runner=fake_runner)
            manager.start_publish(package.name)
            wait_for(
                lambda: manager.get_publish_state(package.name)["workflow"]["status"]
                == "failed"
            )

            state = manager.get_publish_state(package.name)
            self.assertTrue(state["published"])
            self.assertEqual(state["workflow"]["status"], "failed")
            self.assertEqual(state["workflow"]["error"]["message"], "test failure")

    def test_manual_browser_close_after_failure_releases_active_job(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package_a = self._make_post(root, "任务 A")
            package_b = self._make_post(root, "任务 B")

            class FakePage:
                def __init__(self) -> None:
                    self.closed = False

                def is_closed(self) -> bool:
                    return self.closed

            class FakeContext:
                def __init__(self, page) -> None:
                    self.pages = [page]
                    self.close_calls = 0

                def close(self) -> None:
                    self.close_calls += 1

            class FakePlaywright:
                def __init__(self) -> None:
                    self.stop_calls = 0

                def stop(self) -> None:
                    self.stop_calls += 1

            page = FakePage()
            context = FakeContext(page)
            playwright = FakePlaywright()

            def fake_workflow(post, project_root, controller, *, on_browser_started):
                del project_root
                if post.id == package_a.name:
                    on_browser_started(playwright, context, page)
                    raise RuntimeError("browser-backed failure")
                controller.completed("第二个任务完成")

            manager = PublishManager(root)
            with patch("alarkive_publisher.workflow.run_publisher_workflow", fake_workflow):
                manager.start_publish(package_a.name)
                wait_for(
                    lambda: manager.get_publish_state(package_a.name)["workflow"]["status"]
                    == "failed"
                )
                self.assertTrue(manager.browser_open_for(package_a.name))

                # Simulate Chrome's window being closed directly, without the
                # Web close-browser action.
                page.closed = True
                wait_for(lambda: not manager.has_active_workflow())

                self.assertFalse(manager.browser_open_for(package_a.name))
                self.assertEqual(context.close_calls, 0)
                self.assertEqual(playwright.stop_calls, 1)

                manager.start_publish(package_b.name)
                wait_for(lambda: not manager.has_active_workflow())
                self.assertEqual(
                    manager.get_publish_state(package_b.name)["workflow"]["status"],
                    "completed",
                )

    def test_closing_browser_while_waiting_fails_workflow_and_releases_job(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = self._make_post(root)

            def fake_runner(post, project_root, controller):
                controller.set_browser_probe(lambda: False)
                controller.wait_for_user("xiaohongshu", "ready", "检查", "继续")

            manager = PublishManager(root, workflow_runner=fake_runner)
            manager.start_publish(package.name)

            wait_for(
                lambda: manager.get_publish_state(package.name)["workflow"]["status"]
                == "failed"
            )
            wait_for(lambda: not manager.has_active_workflow())

            state = manager.get_publish_state(package.name)
            self.assertTrue(state["published"])
            self.assertEqual(state["workflow"]["status"], "failed")
            self.assertIn("共享浏览器已被关闭", state["workflow"]["error"]["message"])

    def test_reset_does_not_change_running_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = self._make_post(root)
            release = threading.Event()

            def fake_runner(post, project_root, controller):
                controller.step("xiaohongshu", "uploading_images", "上传图片")
                release.wait(timeout=2)
                controller.completed("完成")

            manager = PublishManager(root, workflow_runner=fake_runner)
            manager.start_publish(package.name)
            wait_for(
                lambda: (
                    manager.get_publish_state(package.name)["workflow"]["status"]
                    == "running"
                    and manager.get_publish_state(package.name)["workflow"]["current_step"]
                    == "uploading_images"
                )
            )
            before = copy.deepcopy(manager.get_publish_state(package.name)["workflow"])

            result = mark_unpublished(package)

            self.assertFalse(result["published"])
            self.assertEqual(result["workflow"], before)
            release.set()
            wait_for(lambda: not manager.has_active_workflow())

    def test_stale_workflow_is_interrupted_on_manager_start(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = self._make_post(root)
            manager = PublishManager(root, workflow_runner=lambda *args: None)
            manager.start_publish(package.name)
            wait_for(lambda: not manager.has_active_workflow())
            # Simulate a previous process writing a live state after the first
            # manager has gone away.
            from alarkive_publisher.web.publish_state import initialize_workflow

            initialize_workflow(package)
            restarted = PublishManager(root, workflow_runner=lambda *args: None)
            self.assertEqual(
                restarted.get_publish_state(package.name)["workflow"]["status"],
                "running",
            )
            restarted.reconcile_interrupted_workflows()

            self.assertEqual(
                restarted.get_publish_state(package.name)["workflow"]["status"],
                "interrupted",
            )
