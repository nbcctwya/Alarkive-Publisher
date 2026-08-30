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
)
from alarkive_publisher.web.publish_state import (
    load_publish_state,
    mark_unpublished,
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
                "interrupted",
            )
