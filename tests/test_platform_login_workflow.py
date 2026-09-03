"""Login recovery through real Web controllers, with simulated browser I/O."""
from __future__ import annotations

import tempfile
import threading
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

from playwright.sync_api import Error as PlaywrightError, TimeoutError

from alarkive_publisher import toutiao_micro, wechat, wechat_article
from alarkive_publisher.content import ContentVariant, PostContent
from alarkive_publisher.web.publish_state import load_publish_state
from alarkive_publisher.workflow import run_publisher_workflow, run_single_platform_workflow
from alarkive_publisher.workflow_controller import WebWorkflowController


class PlatformLoginWorkflowTests(unittest.TestCase):
    def test_login_and_early_continue_in_single_and_full_workflows(self):
        for target in ("wechat_article", "wechat_image", "toutiao_micro"):
            for single in (True, False):
                stages = ("home", "popup", "same_tab") if target.startswith("wechat") else ("editor",)
                for redirect_at in stages:
                    with self.subTest(target=target, single=single, redirect_at=redirect_at):
                        self._exercise_login(target, single, redirect_at)

    def _exercise_login(self, target, single, redirect_at):
        with tempfile.TemporaryDirectory() as temp, ExitStack() as stack:
            folder = Path(temp)
            content = ContentVariant("标题", "正文", ())
            post = PostContent(folder, "id", "test", "date", public_long=content,
                               wechat_long=content, wechat_short=content, toutiao_short=content)
            controller = WebWorkflowController(folder)
            browser, context = Mock(), Mock()
            session = {"stage": "login", "required": redirect_at in ("home", "editor")}
            navigations, errors = [], []

            def make_page():
                result = Mock()
                result.auth_state = "home"
                result.url = wechat.HOME_URL

                def goto(url, **kwargs):
                    result.auth_state = session["stage"] if session["required"] else "home"
                    result.url = url
                    if target == "toutiao_micro":
                        result.url = ("https://mp.toutiao.com/auth/page/login/" if session["stage"] == "login"
                                      else toutiao_micro.EDITOR_URL)
                        result.auth_state = "editor" if session["stage"] == "authenticated" else "login"
                    elif result.auth_state == "authenticated":
                        result.auth_state = "home"
                    navigations.append(result)

                def locator(selector):
                    found = Mock()
                    found.count.return_value = 0
                    found.is_visible.return_value = False
                    if selector == "body":
                        found.inner_text.return_value = {
                            "login": "微信扫一扫，选择公众平台账号登录",
                            "account_selection": "请选择公众号 Alark知新录",
                            "home": "新的创作 内容管理",
                            "editor": "编辑器",
                        }[result.auth_state]
                    if "ProseMirror" in selector and result.auth_state == "editor":
                        found.count.return_value = 1
                        found.is_visible.return_value = True
                    found.first = found
                    return found

                result.goto.side_effect = goto
                result.locator.side_effect = locator
                # No QR-login label: the micro login page may show phone/password login.
                result.get_by_text.side_effect = lambda *a, **kw: locator("text")
                return result

            page, popup = make_page(), make_page()

            def enter(source):
                session["required"] = True
                destination = popup if redirect_at == "popup" else source
                destination.auth_state = ("editor" if session["stage"] == "authenticated"
                                          else session["stage"])
                return destination

            stack.enter_context(patch("alarkive_publisher.workflow.start_browser", return_value=(browser, context, page)))
            targets = ("baijiahao", "toutiao_article", "wechat_article", "wechat_image", "toutiao_micro")
            runners = {}
            for other in targets:
                if other != target:
                    name = "run_wechat" if other == "wechat_image" else "run_" + other
                    runners[other] = stack.enter_context(patch("alarkive_publisher.workflow." + name, return_value=page))
            if target == "wechat_article":
                stack.enter_context(patch.object(wechat_article, "_enter_editor", side_effect=enter))
                fill = stack.enter_context(patch.object(wechat_article, "_prepare_content"))
                mutations = [fill]
            elif target == "wechat_image":
                stack.enter_context(patch.object(wechat, "_enter_sticker_editor", side_effect=enter))
                stack.enter_context(patch.object(wechat, "_validate_sticker_editor"))
                fill = stack.enter_context(patch.object(wechat, "_fill_text"))
                mutations = [fill, stack.enter_context(patch.object(wechat, "_upload_images"))]
            else:
                stack.enter_context(patch.object(toutiao_micro, "_read_text", return_value=""))
                stack.enter_context(patch.object(toutiao_micro, "_attached_signatures", return_value=[]))
                fill = stack.enter_context(patch.object(toutiao_micro, "_fill_text"))
                mutations = [fill, stack.enter_context(patch.object(toutiao_micro, "_upload_images"))]
                stack.enter_context(patch.object(toutiao_micro, "_verify_ready"))
                stack.enter_context(patch.object(toutiao_micro, "capture_debug_snapshot"))

            def run():
                try:
                    if single:
                        run_single_platform_workflow(post, folder, target, controller)
                    else:
                        run_publisher_workflow(post, folder, controller)
                except BaseException as exc:
                    errors.append(exc)

            thread = threading.Thread(target=run, daemon=True)
            thread.start()

            def wait_for(platform, step, after_navigation=-1):
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    if errors:
                        raise errors[0]
                    state = load_publish_state(folder)["workflow"]
                    if (state["status"] == "waiting" and state["current_platform"] == platform
                            and state["current_step"] == step and len(navigations) > after_navigation):
                        return state
                    time.sleep(0.01)
                self.fail(f"Did not wait for {platform}/{step}")

            preceding = () if single else targets[:targets.index(target)]
            following = () if single else targets[targets.index(target) + 1:]
            try:
                for other in preceding:
                    wait_for(other, "ready")
                    self.assertTrue(controller.continue_if_waiting())
                steps = ("login", "account_selection") if target.startswith("wechat") else ("login",)
                for step in steps:
                    state = wait_for(target, step)
                    self.assertIsNone(state["error"])
                    self.assertEqual(state["platforms"][target]["status"], "waiting")
                    for other in preceding:
                        self.assertEqual(state["platforms"][other]["status"], "ready")
                    for mutation in mutations:
                        mutation.assert_not_called()
                    for other in following:
                        runners[other].assert_not_called()
                    context.close.assert_not_called()
                    # Repeated early clicks must remain recoverable at both login and account selection.
                    for _ in range(2):
                        previous = len(navigations)
                        self.assertTrue(controller.continue_if_waiting())
                        wait_for(target, step, previous)
                        for mutation in mutations:
                            mutation.assert_not_called()
                    session["stage"] = "account_selection" if step == "login" and len(steps) == 2 else "authenticated"
                    self.assertTrue(controller.continue_if_waiting())

                wait_for(target, "ready")
                destination = popup if redirect_at == "popup" else page
                self.assertIs(fill.call_args.args[0], destination)
                for mutation in mutations:
                    mutation.assert_called_once()
                context.close.assert_not_called()
                self.assertTrue(controller.continue_if_waiting())
                for other in following:
                    wait_for(other, "ready")
                    self.assertTrue(controller.continue_if_waiting())
                thread.join(timeout=3)
                self.assertFalse(thread.is_alive())
                self.assertEqual(errors, [])
                self.assertEqual(load_publish_state(folder)["workflow"]["status"], "completed")
                for runner in runners.values():
                    self.assertEqual(runner.call_count, 0 if single else 1)
                context.close.assert_called_once()
                browser.stop.assert_called_once()
            finally:
                session["stage"] = "authenticated"
                deadline = time.monotonic() + 3
                while thread.is_alive() and time.monotonic() < deadline:
                    controller.continue_if_waiting()
                    thread.join(timeout=0.02)

    def test_network_error_is_not_misreported_as_login(self):
        for check in (wechat._check_login, wechat_article._check_login, toutiao_micro._open_editor):
            with self.subTest(check=check):
                page, controller = Mock(), Mock()
                page.goto.side_effect = PlaywrightError("net::ERR_CONNECTION_RESET")
                with self.assertRaisesRegex(PlaywrightError, "ERR_CONNECTION_RESET"):
                    check(page, controller)
                controller.wait_for_user.assert_not_called()

    def test_wechat_entry_supports_popup_and_same_tab(self):
        for module, enter in ((wechat, wechat._enter_sticker_editor), (wechat_article, wechat_article._enter_editor)):
            for same_tab in (True, False):
                with self.subTest(module=module.__name__, same_tab=same_tab), ExitStack() as stack:
                    page, popup, entry = Mock(), Mock(), Mock()
                    manager = Mock()
                    manager.__enter__ = Mock(return_value=Mock(value=popup))
                    manager.__exit__ = Mock(return_value=False, side_effect=TimeoutError("no popup") if same_tab else None)
                    page.expect_popup.return_value = manager
                    page.get_by_text.return_value = entry
                    if module is wechat:
                        stack.enter_context(patch.object(wechat, "_close_known_popups"))
                        stack.enter_context(patch.object(wechat, "_first_interactable", return_value=entry))
                    self.assertIs(enter(page), page if same_tab else popup)
                    entry.click.assert_called_once()


if __name__ == "__main__":
    unittest.main()
