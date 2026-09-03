from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from playwright.sync_api import Error as PlaywrightError

from alarkive_publisher import browser
from alarkive_publisher.publisher_common import PublisherError


class FakePlaywright:
    def __init__(self) -> None:
        self.stop_calls = 0

    def stop(self) -> None:
        self.stop_calls += 1


class FakeContext:
    pages: list[object] = []

    def __init__(self) -> None:
        self.close_calls = 0

    def new_page(self) -> object:
        raise RuntimeError("new page failed")

    def close(self) -> None:
        self.close_calls += 1


class FakeSyncPlaywright:
    def __init__(self, playwright: FakePlaywright) -> None:
        self.playwright = playwright

    def start(self) -> FakePlaywright:
        return self.playwright


class BrowserStartupTests(unittest.TestCase):
    def test_profile_path_defaults_to_project_and_accepts_override(self) -> None:
        root = Path("project")
        with patch.dict(browser.os.environ, {}, clear=True):
            self.assertEqual(browser._automation_user_data_dir(root), root / ".browser-data")
        with patch.dict(browser.os.environ, {"ALARKIVE_BROWSER_DATA_DIR": "custom-profile"}):
            self.assertEqual(browser._automation_user_data_dir(root), Path("custom-profile"))

    def test_launch_keeps_persistent_headed_chrome_settings(self) -> None:
        playwright = Mock()
        with tempfile.TemporaryDirectory() as temp, patch.dict(browser.os.environ, {}, clear=True):
            profile = Path(temp) / "profile"
            context = browser._launch_context(playwright, profile)
            self.assertTrue(profile.is_dir())
            playwright.chromium.launch_persistent_context.assert_called_once_with(
                str(profile), headless=False, channel="chrome", timeout=30_000,
                viewport=None, args=["--new-window", "--window-size=1200,800"],
            )
            self.assertIs(context, playwright.chromium.launch_persistent_context.return_value)

    def test_launch_preserves_explicit_channel_override(self) -> None:
        playwright = Mock()
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            browser.os.environ, {"ALARKIVE_BROWSER_CHANNEL": "chromium"}
        ):
            browser._launch_context(playwright, Path(temp))
        self.assertEqual(playwright.chromium.launch_persistent_context.call_args.kwargs["channel"], "chromium")

    def test_profile_lock_reports_shared_publisher_error(self) -> None:
        playwright = Mock()
        cause = PlaywrightError("user data directory already in use")
        playwright.chromium.launch_persistent_context.side_effect = cause
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(PublisherError) as caught:
                browser._launch_context(playwright, Path(temp))
        self.assertEqual(caught.exception.step, "Starting browser")
        self.assertIs(caught.exception.__cause__, cause)

    def test_unrelated_launch_error_is_not_relabelled(self) -> None:
        playwright = Mock()
        cause = PlaywrightError("executable missing")
        playwright.chromium.launch_persistent_context.side_effect = cause
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(PlaywrightError) as caught:
                browser._launch_context(playwright, Path(temp))
        self.assertIs(caught.exception, cause)

    def test_start_browser_preserves_page_setup_and_returns_handles(self) -> None:
        for existing_page in (True, False):
            with self.subTest(existing_page=existing_page):
                playwright, context, page = Mock(), Mock(), Mock()
                context.pages = [page] if existing_page else []
                context.new_page.return_value = page
                root = Path("project")
                with patch.object(browser, "sync_playwright") as sync, patch.object(
                    browser, "_launch_context", return_value=context
                ) as launch, patch.object(browser, "_automation_user_data_dir", return_value=root / ".browser-data"), patch.object(
                    browser, "_chrome_window_snapshot", return_value={7}
                ), patch.object(browser, "_restore_new_chrome_window") as restore:
                    sync.return_value.start.return_value = playwright
                    result = browser.start_browser(root)
                self.assertEqual(result, (playwright, context, page))
                launch.assert_called_once_with(playwright, root / ".browser-data")
                page.bring_to_front.assert_called_once_with()
                restore.assert_called_once_with({7})
                page.set_default_timeout.assert_called_once_with(15_000)
                self.assertEqual(context.new_page.call_count, int(not existing_page))
                context.close.assert_not_called()
                playwright.stop.assert_not_called()

    def test_start_browser_stops_playwright_when_launch_fails(self) -> None:
        playwright = Mock()
        cause = RuntimeError("launch failed")
        with patch.object(browser, "sync_playwright") as sync, patch.object(
            browser, "_launch_context", side_effect=cause
        ), patch.object(browser, "_chrome_window_snapshot", return_value=set()):
            sync.return_value.start.return_value = playwright
            with self.assertRaises(RuntimeError) as caught:
                browser.start_browser(Path("project"))
        self.assertIs(caught.exception, cause)
        playwright.stop.assert_called_once_with()

    def test_start_browser_cleans_context_when_page_setup_fails(self) -> None:
        playwright = FakePlaywright()
        context = FakeContext()
        sync = FakeSyncPlaywright(playwright)
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(browser, "sync_playwright", return_value=sync), patch.object(
                browser, "_launch_context", return_value=context
            ), patch.object(browser, "_chrome_window_snapshot", return_value=set()), patch.object(
                browser, "_restore_new_chrome_window"
            ):
                with self.assertRaisesRegex(RuntimeError, "new page failed"):
                    browser.start_browser(Path(temp))

        self.assertEqual(context.close_calls, 1)
        self.assertEqual(playwright.stop_calls, 1)


if __name__ == "__main__":
    unittest.main()
