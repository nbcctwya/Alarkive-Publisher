from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from alarkive_publisher.web.publish_state import load_publish_state
from alarkive_publisher.workflow_controller import WebWorkflowController


PLATFORMS = ("xiaohongshu", "baijiahao", "wechat")


def wait_for_waiting(folder: Path) -> None:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if load_publish_state(folder)["workflow"]["status"] == "waiting":
            return
        time.sleep(0.01)
    raise AssertionError("controller did not reach waiting state")


class WebWorkflowControllerTests(unittest.TestCase):
    def test_intermediate_wait_resumes_running(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            controller = WebWorkflowController(folder)
            thread = threading.Thread(
                target=controller.wait_for_user,
                args=("xiaohongshu", "login", "需要登录", "继续"),
            )
            thread.start()
            wait_for_waiting(folder)
            self.assertTrue(controller.continue_if_waiting())
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
            self.assertEqual(
                load_publish_state(folder)["workflow"]["platforms"]["xiaohongshu"]["status"],
                "running",
            )

    def test_ready_platform_stays_ready_after_continue(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            controller = WebWorkflowController(folder)
            thread = threading.Thread(
                target=controller.ready,
                args=("xiaohongshu", "小红书已准备完成", "继续"),
            )
            thread.start()
            wait_for_waiting(folder)
            self.assertEqual(
                load_publish_state(folder)["workflow"]["platforms"]["xiaohongshu"]["status"],
                "ready",
            )

            self.assertTrue(controller.continue_if_waiting())
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
            self.assertEqual(
                load_publish_state(folder)["workflow"]["platforms"]["xiaohongshu"]["status"],
                "ready",
            )

    def test_completed_workflow_keeps_all_platforms_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            controller = WebWorkflowController(folder)
            for platform in PLATFORMS:
                thread = threading.Thread(
                    target=controller.ready,
                    args=(platform, f"{platform} ready", "继续"),
                )
                thread.start()
                wait_for_waiting(folder)
                self.assertTrue(controller.continue_if_waiting())
                thread.join(timeout=2)
                self.assertFalse(thread.is_alive())

            controller.completed("完成")
            state = load_publish_state(folder)
            self.assertEqual(state["workflow"]["status"], "completed")
            self.assertEqual(
                [state["workflow"]["platforms"][platform]["status"] for platform in PLATFORMS],
                ["ready", "ready", "ready"],
            )


if __name__ == "__main__":
    unittest.main()
