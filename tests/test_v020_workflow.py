from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from alarkive_publisher.content import ContentVariant, PostContent
from alarkive_publisher.workflow import run_publisher_workflow, run_single_platform_workflow
from alarkive_publisher.workflow_controller import WorkflowController


class RecordingController(WorkflowController):
    def __init__(self) -> None:
        self.events: list[tuple] = []

    def start_platform(self, platform: str) -> None:
        self.events.append(("start", platform))

    def step(self, platform: str, step: str, message: str) -> None:
        self.events.append(("step", platform, step))

    def wait_for_user(self, platform: str, step: str, message: str, prompt: str) -> None:
        self.events.append(("wait", platform, step))

    def ready(self, platform: str, message: str, prompt: str) -> None:
        self.events.append(("ready", platform))

    def system_step(self, step: str, message: str) -> None:
        self.events.append(("system", step))

    def completed(self, message: str) -> None:
        self.events.append(("completed", message))

    def failed(self, platform: str | None, step: str, exc: BaseException) -> None:
        self.events.append(("failed", platform, step))


def post_with(*variants: str) -> PostContent:
    values = {
        variant: ContentVariant(
            title=variant,
            body="body",
            images=(Path("01.png"),),
        )
        for variant in variants
    }
    return PostContent(
        folder=Path("."),
        id="20260902-170000-a7c3",
        name="测试任务",
        created_at="2026-09-02T17:00:00+08:00",
        public_long=values.get("public_long"),
        wechat_long=values.get("wechat_long"),
        wechat_short=values.get("wechat_short"),
        toutiao_short=values.get("toutiao_short"),
    )


class ContentVariantWorkflowTests(unittest.TestCase):
    def test_public_and_wechat_short_run_in_target_order(self) -> None:
        controller = RecordingController()
        page = object()
        context = type("Context", (), {"close": lambda self: None})()
        playwright = type("Playwright", (), {"stop": lambda self: None})()
        calls: list[str] = []

        def baijiahao(*args) -> None:
            del args
            calls.append("baijiahao")

        def wechat(*args):
            del args
            calls.append("wechat_image")
            return page

        with patch("alarkive_publisher.workflow.start_browser", return_value=(playwright, context, page)), patch(
            "alarkive_publisher.workflow.run_baijiahao", side_effect=baijiahao
        ), patch("alarkive_publisher.workflow.run_wechat", side_effect=wechat):
            run_publisher_workflow(post_with("public_long", "wechat_short"), Path("."), controller)

        self.assertEqual(calls, ["baijiahao", "wechat_image"])
        self.assertEqual([event[1] for event in controller.events if event[0] == "start"], ["baijiahao", "wechat_image"])

    def test_single_supported_variant_runs_only_its_target(self) -> None:
        for variant, target, runner_name in (
            ("public_long", "baijiahao", "baijiahao"),
            ("wechat_short", "wechat_image", "wechat_image"),
        ):
            with self.subTest(variant=variant), tempfile.TemporaryDirectory():
                controller = RecordingController()
                page = object()
                context = type("Context", (), {"close": lambda self: None})()
                playwright = type("Playwright", (), {"stop": lambda self: None})()
                calls: list[str] = []

                def record(*args):
                    del args
                    calls.append(runner_name)
                    return page

                with patch("alarkive_publisher.workflow.start_browser", return_value=(playwright, context, page)), patch(
                    "alarkive_publisher.workflow.run_baijiahao", side_effect=record
                ), patch("alarkive_publisher.workflow.run_wechat", side_effect=record):
                    run_publisher_workflow(post_with(variant), Path("."), controller)
                self.assertEqual(calls, [runner_name])

    def test_unsupported_only_variants_do_not_start_browser(self) -> None:
        with patch("alarkive_publisher.workflow.start_browser") as start_browser:
            with self.assertRaisesRegex(ValueError, "没有已接入的可发布平台"):
                run_publisher_workflow(
                    post_with("wechat_long", "toutiao_short"),
                    Path("."),
                    RecordingController(),
                )
        start_browser.assert_not_called()

    def test_mixed_supported_and_unsupported_variants_only_run_supported(self) -> None:
        controller = RecordingController()
        page = object()
        context = type("Context", (), {"close": lambda self: None})()
        playwright = type("Playwright", (), {"stop": lambda self: None})()
        with patch("alarkive_publisher.workflow.start_browser", return_value=(playwright, context, page)), patch(
            "alarkive_publisher.workflow.run_baijiahao"
        ) as baijiahao, patch("alarkive_publisher.workflow.run_wechat") as wechat:
            run_publisher_workflow(
                post_with("public_long", "wechat_long", "toutiao_short"),
                Path("."),
                controller,
            )
        baijiahao.assert_called_once()
        wechat.assert_not_called()

    def test_single_missing_or_unimplemented_target_fails_before_browser(self) -> None:
        post = post_with("public_long")
        with patch("alarkive_publisher.workflow.start_browser") as start_browser:
            with self.assertRaisesRegex(ValueError, "不包含微信图文所需内容"):
                run_single_platform_workflow(
                    post, Path("."), "wechat_image", RecordingController()
                )
            with self.assertRaisesRegex(ValueError, "Publisher 尚未接入"):
                run_single_platform_workflow(
                    post, Path("."), "toutiao_article", RecordingController()
                )
        start_browser.assert_not_called()


if __name__ == "__main__":
    unittest.main()
