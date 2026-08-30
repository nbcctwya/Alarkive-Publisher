from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from alarkive_publisher import xiaohongshu


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
    def test_start_browser_cleans_context_when_page_setup_fails(self) -> None:
        playwright = FakePlaywright()
        context = FakeContext()
        sync = FakeSyncPlaywright(playwright)
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(xiaohongshu, "sync_playwright", return_value=sync), patch.object(
                xiaohongshu, "_launch_context", return_value=context
            ), patch.object(xiaohongshu, "_chrome_window_snapshot", return_value=set()), patch.object(
                xiaohongshu, "_restore_new_chrome_window"
            ):
                with self.assertRaisesRegex(RuntimeError, "new page failed"):
                    xiaohongshu.start_browser(Path(temp))

        self.assertEqual(context.close_calls, 1)
        self.assertEqual(playwright.stop_calls, 1)


if __name__ == "__main__":
    unittest.main()
