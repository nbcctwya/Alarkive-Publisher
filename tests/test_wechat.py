from __future__ import annotations

import unittest
from unittest.mock import patch

from playwright.sync_api import Error as PlaywrightError

from alarkive_publisher import wechat


class WeChatNavigationTests(unittest.TestCase):
    def test_post_login_same_host_redirect_is_tolerated(self) -> None:
        class RedirectingPage:
            url = wechat.HOME_URL + "cgi-bin/home?t=home/index"

            def __init__(self) -> None:
                self.wait_until = None

            def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
                self.wait_until = wait_until
                raise PlaywrightError(
                    "Navigation to the home page is interrupted by another navigation"
                )

        page = RedirectingPage()
        with patch.object(wechat, "_wait_for_dom") as wait_for_dom:
            wechat._navigate_home(page)  # type: ignore[arg-type]

        self.assertEqual(page.wait_until, "commit")
        wait_for_dom.assert_called_once_with(page)

    def test_unrelated_navigation_error_is_not_hidden(self) -> None:
        class FailedPage:
            url = "https://example.com/"

            def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
                raise PlaywrightError(
                    "Navigation to the home page is interrupted by another navigation"
                )

        with self.assertRaises(PlaywrightError):
            wechat._navigate_home(FailedPage())  # type: ignore[arg-type]
