"""Exercise Web wait/continue and publisher routing with simulated browser I/O."""
from __future__ import annotations

import tempfile
import threading
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

from playwright.sync_api import Error as PlaywrightError

from alarkive_publisher.content import ContentVariant, PostContent
from alarkive_publisher.toutiao_article import EDITOR_URL, _open_editor
from alarkive_publisher.web.publish_state import load_publish_state
from alarkive_publisher.workflow import run_publisher_workflow, run_single_platform_workflow
from alarkive_publisher.workflow_controller import WebWorkflowController


class ToutiaoLoginWorkflowTests(unittest.TestCase):
    def test_login_pause_resumes_single_and_full_workflows(self):
        for single in (True, False):
            for redirect_at in ("home", "editor"):
                with self.subTest(single=single, redirect_at=redirect_at):
                    self._exercise_login(single, redirect_at)

    def _exercise_login(self, single, redirect_at):
        with tempfile.TemporaryDirectory() as temp, ExitStack() as stack:
            folder = Path(temp)
            content = ContentVariant("标题", "正文", ())
            post = PostContent(folder, "id", "test", "date", public_long=content,
                               wechat_long=content, wechat_short=content, toutiao_short=content)
            controller = WebWorkflowController(folder)
            browser, context, page, body = (Mock() for _ in range(4))
            authenticated = threading.Event()
            errors = []
            prefix = "alarkive_publisher.toutiao_article."

            def navigate(_, url):
                needs_login = not authenticated.is_set() and (redirect_at == "home" or url == EDITOR_URL)
                page.url = "https://mp.toutiao.com/auth/page/login/" if needs_login else url

            navigation = stack.enter_context(patch(prefix + "_navigate", side_effect=navigate))
            stack.enter_context(patch(prefix + "_first_interactable", return_value=None))
            stack.enter_context(patch(prefix + "_close_dialogs"))
            fill_title = stack.enter_context(patch(prefix + "_fill_title"))
            stack.enter_context(patch(prefix + "_fill_body"))
            stack.enter_context(patch(prefix + "_body_locator", return_value=body))
            upload = stack.enter_context(patch(prefix + "_insert_images_at_end"))
            stack.enter_context(patch(prefix + "_verify_ready_state", return_value=None))
            stack.enter_context(patch("alarkive_publisher.workflow.start_browser", return_value=(browser, context, page)))
            baijiahao = stack.enter_context(patch("alarkive_publisher.workflow.run_baijiahao"))
            following = [stack.enter_context(patch("alarkive_publisher.workflow." + name, return_value=page))
                         for name in ("run_wechat_article", "run_wechat", "run_toutiao_micro")]

            def run():
                try:
                    if single:
                        run_single_platform_workflow(post, folder, "toutiao_article", controller)
                    else:
                        run_publisher_workflow(post, folder, controller)
                except BaseException as exc:
                    errors.append(exc)

            thread = threading.Thread(target=run, daemon=True)
            thread.start()

            def wait_for(target, step, *, after_navigation=-1):
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    if errors:
                        raise errors[0]
                    workflow = load_publish_state(folder)["workflow"]
                    if (workflow["status"] == "waiting" and workflow["current_platform"] == target
                            and workflow["current_step"] == step and navigation.call_count > after_navigation):
                        return workflow
                    time.sleep(0.01)
                self.fail(f"Did not wait for {target}/{step}")

            try:
                if not single:
                    wait_for("baijiahao", "ready")
                    self.assertTrue(controller.continue_if_waiting())
                state = wait_for("toutiao_article", "login")
                self.assertIsNone(state["error"])
                self.assertEqual(state["platforms"]["toutiao_article"]["status"], "waiting")
                if not single:
                    self.assertEqual(state["platforms"]["baijiahao"]["status"], "ready")
                fill_title.assert_not_called()
                upload.assert_not_called()
                context.close.assert_not_called()
                for runner in following:
                    runner.assert_not_called()

                # An early Continue is recoverable: still wait, with no content
                # entered and no loss of the earlier platform's ready state.
                previous_navigation = navigation.call_count
                self.assertTrue(controller.continue_if_waiting())
                state = wait_for("toutiao_article", "login", after_navigation=previous_navigation)
                self.assertIsNone(state["error"])
                fill_title.assert_not_called()

                authenticated.set()
                self.assertTrue(controller.continue_if_waiting())
                state = wait_for("toutiao_article", "ready")
                self.assertIsNone(state["error"])
                self.assertEqual(page.url, EDITOR_URL)
                fill_title.assert_called_once_with(page, content)
                upload.assert_called_once_with(page, body, ())
                context.close.assert_not_called()
                self.assertTrue(controller.continue_if_waiting())
                if not single:
                    for target in ("wechat_article", "wechat_image", "toutiao_micro"):
                        wait_for(target, "ready")
                        self.assertTrue(controller.continue_if_waiting())
                thread.join(timeout=3)
                self.assertFalse(thread.is_alive())
                self.assertEqual(errors, [])
                self.assertEqual(load_publish_state(folder)["workflow"]["status"], "completed")
                context.close.assert_called_once()
                browser.stop.assert_called_once()
                self.assertEqual(baijiahao.call_count, 0 if single else 1)
                for runner in following:
                    self.assertEqual(runner.call_count, 0 if single else 1)
                page.click.assert_not_called()
            finally:
                authenticated.set()
                deadline = time.monotonic() + 3
                while thread.is_alive() and time.monotonic() < deadline:
                    controller.continue_if_waiting()
                    thread.join(timeout=0.02)

    def test_network_error_is_not_misreported_as_login(self):
        controller = Mock()
        with patch("alarkive_publisher.toutiao_article._navigate", side_effect=PlaywrightError("net::ERR_CONNECTION_RESET")):
            with self.assertRaisesRegex(PlaywrightError, "ERR_CONNECTION_RESET"):
                _open_editor(Mock(), controller)
        controller.wait_for_user.assert_not_called()


if __name__ == "__main__":
    unittest.main()
