"""Exercise the real Web wait/continue path without posting to any platform."""

from __future__ import annotations

import tempfile
import threading
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

from alarkive_publisher.content import ContentVariant, PostContent
from alarkive_publisher.web.publish_state import load_publish_state
from alarkive_publisher.workflow import run_publisher_workflow, run_single_platform_workflow
from alarkive_publisher.workflow_controller import WebWorkflowController


class ToutiaoDraftWorkflowTests(unittest.TestCase):
    def test_busy_draft_waits_for_web_continue_then_runs_remaining_platforms(self):
        self._run_and_continue(single=False, draft_idle=False)

    def test_single_article_keeps_warning_and_waits_before_closing(self):
        self._run_and_continue(single=True, draft_idle=False)

    def test_idle_draft_keeps_normal_ready_flow(self):
        self._run_and_continue(single=True, draft_idle=True)

    def _run_and_continue(self, *, single, draft_idle):
        with tempfile.TemporaryDirectory() as temp, ExitStack() as stack:
            folder = Path(temp)
            content = ContentVariant("标题", "正文", ())
            post = PostContent(folder, "id", "test", "date", public_long=content,
                               wechat_short=content, toutiao_short=content)
            controller = WebWorkflowController(folder)
            browser, context, page, title, body = (Mock() for _ in range(5))
            stack.enter_context(patch("alarkive_publisher.workflow.start_browser", return_value=(browser, context, page)))
            baijiahao = stack.enter_context(patch("alarkive_publisher.workflow.run_baijiahao"))
            wechat = stack.enter_context(patch("alarkive_publisher.workflow.run_wechat", return_value=page))
            micro = stack.enter_context(patch("alarkive_publisher.workflow.run_toutiao_micro"))
            prefix = "alarkive_publisher.toutiao_article."
            # Run the actual article publisher and final checks; fake only the
            # browser I/O and the already-reproduced persistent saving indicator.
            for helper in ("_check_login", "_open_editor", "_fill_title", "_fill_body", "_insert_images_at_end"):
                stack.enter_context(patch(prefix + helper))
            stack.enter_context(patch(prefix + "_title_locator", return_value=title))
            stack.enter_context(patch(prefix + "_body_locator", return_value=body))
            stack.enter_context(patch(prefix + "_read_locator_value", side_effect=lambda el: "标题" if el is title else "正文"))
            stack.enter_context(patch(prefix + "_editor_image_count", return_value=0))
            stack.enter_context(patch(prefix + "_visible_final_publish_controls", return_value=("发布",)))
            stack.enter_context(patch(prefix + "_wait_for_draft_idle", return_value=draft_idle))
            errors = []

            def run():
                try:
                    if single:
                        run_single_platform_workflow(post, folder, "toutiao_article", controller)
                    else:
                        run_publisher_workflow(post, folder, controller)
                except BaseException as exc:
                    errors.append(exc)

            thread = threading.Thread(target=run)
            thread.start()

            def wait_for_platform(target):
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    workflow = load_publish_state(folder)["workflow"]
                    if workflow["status"] == "waiting" and workflow["current_platform"] == target:
                        return workflow
                    if errors:
                        raise errors[0]
                    time.sleep(0.01)
                self.fail(f"Did not pause at {target}")

            try:
                if not single:
                    wait_for_platform("baijiahao")
                    self.assertTrue(controller.continue_if_waiting())
                workflow = wait_for_platform("toutiao_article")
                self.assertEqual(workflow["current_step"], "ready")
                self.assertEqual(workflow["platforms"]["toutiao_article"]["status"], "ready")
                self.assertIsNone(workflow["error"])
                self.assertEqual("尚未确认保存成功" in workflow["message"], not draft_idle)
                wechat.assert_not_called()
                micro.assert_not_called()
                context.close.assert_not_called()
                self.assertTrue(controller.continue_if_waiting())
                if not single:
                    wait_for_platform("wechat_image")
                    micro.assert_not_called()
                    self.assertTrue(controller.continue_if_waiting())
                    wait_for_platform("toutiao_micro")
                    self.assertTrue(controller.continue_if_waiting())
                thread.join(timeout=3)
                self.assertFalse(thread.is_alive())
                self.assertEqual(errors, [])
                final = load_publish_state(folder)["workflow"]
                self.assertEqual(final["status"], "completed")
                self.assertEqual("尚未确认保存成功" in final["platforms"]["toutiao_article"]["message"], not draft_idle)
                context.close.assert_called_once()
                browser.stop.assert_called_once()
                if single:
                    baijiahao.assert_not_called()
                else:
                    wechat.assert_called_once()
                    micro.assert_called_once()
                page.click.assert_not_called()
            finally:
                # Release any unexpected later wait if an assertion fails.
                deadline = time.monotonic() + 3
                while thread.is_alive() and time.monotonic() < deadline:
                    controller.continue_if_waiting()
                    thread.join(timeout=0.02)


if __name__ == "__main__":
    unittest.main()
