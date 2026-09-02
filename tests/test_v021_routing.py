from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from alarkive_publisher.content import PostContent
from alarkive_publisher.routing import (
    AVAILABLE_PUBLISHERS,
    PUBLISHER_REGISTRY,
    WORKFLOW_TARGETS,
)
from alarkive_publisher.web.publish_state import default_publish_state, initialize_workflow


class V021RoutingTests(unittest.TestCase):
    def test_toutiao_article_is_an_implemented_public_long_consumer(self) -> None:
        spec = PUBLISHER_REGISTRY["toutiao_article"]
        self.assertEqual(spec.variant, "public_long")
        self.assertTrue(spec.implemented)
        self.assertEqual(spec.runner, "toutiao_article")
        self.assertIn("toutiao_article", AVAILABLE_PUBLISHERS)

    def test_available_publishers_and_default_order(self) -> None:
        self.assertEqual(
            AVAILABLE_PUBLISHERS,
            frozenset({"baijiahao", "toutiao_article", "wechat_image"}),
        )
        self.assertEqual(
            WORKFLOW_TARGETS,
            ("baijiahao", "toutiao_article", "wechat_image"),
        )

    def test_publish_state_keeps_canonical_toutiao_slot(self) -> None:
        state = default_publish_state()
        self.assertEqual(state["workflow"]["platforms"]["toutiao_article"]["status"], "pending")
        with tempfile.TemporaryDirectory() as temp:
            single = initialize_workflow(
                temp,
                workflow_mode="single",
                target_platform="toutiao_article",
            )
        self.assertEqual(single["workflow"]["target_platform"], "toutiao_article")

    def test_missing_public_long_rejects_toutiao_before_browser_start(self) -> None:
        post = PostContent(
            folder=Path("."),
            id="20260902-170000-a7c3",
            name="测试任务",
            created_at="2026-09-02T17:00:00+08:00",
        )
        from alarkive_publisher.workflow import run_single_platform_workflow

        with patch("alarkive_publisher.workflow.start_browser") as start_browser:
            with self.assertRaisesRegex(ValueError, "不包含今日头条文章所需内容"):
                run_single_platform_workflow(
                    post, Path("."), "toutiao_article", _NoopController()
                )
        start_browser.assert_not_called()


class _NoopController:
    def system_step(self, step: str, message: str) -> None:
        del step, message

    def start_platform(self, platform: str) -> None:
        del platform

    def failed(self, platform, step: str, exc: BaseException) -> None:
        del platform, step, exc


if __name__ == "__main__":
    unittest.main()
