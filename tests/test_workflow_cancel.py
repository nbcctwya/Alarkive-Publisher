from __future__ import annotations

import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from alarkive_publisher.web.publish_manager import PublishManager, PublishManagerError
from alarkive_publisher.web.publish_state import initialize_workflow, load_publish_state, mark_interrupted, update_workflow
from alarkive_publisher.web.storage import ImageData, save_post


def wait_for(predicate):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Workflow did not reach the expected state")


class CancelWorkflowTests(unittest.TestCase):
    def make_post(self, root):
        return save_post(
            "取消测试", {"public_long": "标题"}, {"public_long": "正文"},
            [ImageData("image.png", b"\x89PNG\r\n\x1a\nimage")], posts_root=root,
        ).directory

    def test_importing_web_app_does_not_interrupt_existing_wait(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = self.make_post(root)
            initialize_workflow(package)
            update_workflow(package, status="waiting", current_platform="toutiao_article", current_step="ready")
            before = (package / "publish-state.json").read_bytes()
            # Exercise a real fresh import while redirecting the default manager
            # to this isolated directory, never the user's posts directory.
            script = """
import sys
from pathlib import Path
from unittest.mock import patch
from alarkive_publisher.web import publish_manager
with patch.object(publish_manager, 'POSTS_DIR', Path(sys.argv[1])):
    import alarkive_publisher.web.app
"""
            subprocess.run([sys.executable, "-c", script, str(root)], check=True, capture_output=True, text=True)
            self.assertEqual((package / "publish-state.json").read_bytes(), before)

    def test_cancel_releases_wait_even_when_persisted_status_is_interrupted(self):
        for stale in (False, True):
            with self.subTest(stale=stale), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                package = self.make_post(root)
                browser, context, page = Mock(), Mock(), Mock()
                page.is_closed.return_value = False
                context.pages = [page]
                closed_on = []
                context.close.side_effect = lambda: closed_on.append(threading.get_ident())
                continued = threading.Event()

                def runner(post, project_root, controller, *, on_browser_started):
                    on_browser_started(browser, context, page)
                    controller.ready("toutiao_article", "头条已准备完成", "继续")
                    continued.set()

                manager = PublishManager(root)
                with patch("alarkive_publisher.workflow.run_publisher_workflow", runner):
                    manager.start_publish(package.name)
                    wait_for(lambda: manager.get_publish_state(package.name)["workflow"]["status"] == "waiting")
                    if stale:
                        mark_interrupted(package)
                    manager.cancel_publish(package.name)
                    wait_for(lambda: not manager.has_active_workflow())

                state = load_publish_state(package)["workflow"]
                self.assertEqual(state["status"], "cancelled")
                self.assertEqual(state["platforms"]["toutiao_article"]["status"], "ready")
                self.assertIsNone(state["error"])
                self.assertFalse(continued.is_set())
                context.close.assert_called_once()
                browser.stop.assert_called_once()
                self.assertNotEqual(closed_on, [threading.get_ident()])

    def test_cancel_running_stops_before_next_step(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = self.make_post(root)
            finish_operation = threading.Event()
            next_step = threading.Event()

            def runner(post, project_root, controller):
                controller.step("toutiao_article", "content", "填写内容")
                finish_operation.wait(timeout=3)
                controller.step("toutiao_article", "verify", "核对")
                next_step.set()

            manager = PublishManager(root, workflow_runner=runner)
            manager.start_publish(package.name)
            wait_for(lambda: manager.get_publish_state(package.name)["workflow"]["current_step"] == "content")
            with self.assertRaises(PublishManagerError):
                manager.cancel_publish("another-post")
            manager.cancel_publish(package.name)
            self.assertTrue(manager.cancel_requested_for(package.name))
            finish_operation.set()
            wait_for(lambda: not manager.has_active_workflow())
            self.assertFalse(next_step.is_set())
            self.assertEqual(load_publish_state(package)["workflow"]["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
