from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import patch

from alarkive_publisher.content import PostContent
from alarkive_publisher.workflow import (
    run_publisher_workflow,
    run_single_platform_workflow,
)
from alarkive_publisher.workflow_controller import WorkflowController


class _RecordingController:
    def __init__(self) -> None:
        self.events: list[tuple] = []

    def system_step(self, step: str, message: str) -> None:
        self.events.append(("system", step, message))

    def start_platform(self, platform: str) -> None:
        self.events.append(("start", platform))

    def ready(self, platform: str, message: str, prompt: str) -> None:
        self.events.append(("ready", platform, message, prompt))

    def completed(self, message: str) -> None:
        self.events.append(("completed", message))

    def failed(self, platform: str | None, step: str, exc: BaseException) -> None:
        self.events.append(("failed", platform, step, str(exc)))


class _FakeContext:
    def __init__(self) -> None:
        self.pages: list[object] = []
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


class _FakePlaywright:
    def __init__(self) -> None:
        self.stop_calls = 0

    def stop(self) -> None:
        self.stop_calls += 1


class SinglePlatformWorkflowTests(unittest.TestCase):
    @staticmethod
    def _post_with_platforms(*platforms: str) -> PostContent:
        enabled = set(platforms)
        return PostContent(
            folder=Path("."),
            id="20260902-170000-a7c3",
            name="测试任务",
            created_at="2026-09-02T17:00:00+08:00",
            xiaohongshu=object() if "xiaohongshu" in enabled else None,  # type: ignore[arg-type]
            baijiahao=object() if {"baijiahao", "toutiao_article"} & enabled else None,  # type: ignore[arg-type]
            wechat=object() if "wechat" in enabled else None,  # type: ignore[arg-type]
        )

    def test_each_single_workflow_only_runs_its_target_and_closes_once(self) -> None:
        for target in ("xiaohongshu", "baijiahao", "toutiao_article", "wechat"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as temp:
                context = _FakeContext()
                playwright = _FakePlaywright()
                recording = _RecordingController()
                controller = cast(WorkflowController, recording)
                post = self._post_with_platforms(target)
                page = object()
                calls: list[str] = []

                def record_xiaohongshu(*args) -> None:
                    del args
                    calls.append("xiaohongshu")

                def record_baijiahao(*args) -> None:
                    del args
                    calls.append("baijiahao")

                def record_toutiao(*args) -> None:
                    del args
                    calls.append("toutiao_article")

                def record_wechat(*args):
                    del args
                    calls.append("wechat")
                    return page

                with patch(
                    "alarkive_publisher.workflow.start_browser",
                    return_value=(playwright, context, page),
                ), patch(
                    "alarkive_publisher.workflow.run_xiaohongshu",
                    side_effect=record_xiaohongshu,
                ), patch(
                    "alarkive_publisher.workflow.run_baijiahao",
                    side_effect=record_baijiahao,
                ), patch(
                    "alarkive_publisher.workflow.run_toutiao_article",
                    side_effect=record_toutiao,
                ), patch(
                    "alarkive_publisher.workflow.run_wechat",
                    side_effect=record_wechat,
                ):
                    run_single_platform_workflow(
                        post, Path(temp), target, controller
                    )

                self.assertEqual(calls, [target])
                self.assertEqual(context.close_calls, 1)
                self.assertEqual(playwright.stop_calls, 1)
                self.assertEqual(
                    [event[0] for event in recording.events],
                    ["system", "start", "ready", "completed"],
                )
                self.assertIn("结束流程并关闭浏览器", recording.events[2][3])

    def test_invalid_single_platform_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported single-platform"):
            run_single_platform_workflow(
                cast(PostContent, object()),
                Path("."),
                "invalid",
                cast(WorkflowController, _RecordingController()),
            )

    def test_all_platform_workflow_keeps_existing_order(self) -> None:
        context = _FakeContext()
        playwright = _FakePlaywright()
        page = object()
        controller = cast(WorkflowController, _RecordingController())
        post = self._post_with_platforms("baijiahao", "wechat")
        calls: list[str] = []

        def record(name: str):
            def runner(*args):
                del args
                calls.append(name)
                return page if name == "wechat" else None

            return runner

        with patch(
            "alarkive_publisher.workflow.start_browser",
            return_value=(playwright, context, page),
        ), patch(
            "alarkive_publisher.workflow.run_xiaohongshu",
            side_effect=record("xiaohongshu"),
        ), patch(
            "alarkive_publisher.workflow.run_baijiahao",
            side_effect=record("baijiahao"),
        ), patch(
            "alarkive_publisher.workflow.run_toutiao_article",
            side_effect=record("toutiao_article"),
        ), patch(
            "alarkive_publisher.workflow.run_wechat",
            side_effect=record("wechat"),
        ):
            run_publisher_workflow(post, Path("."), controller)

        self.assertEqual(calls, ["baijiahao", "toutiao_article", "wechat"])
        self.assertEqual(context.close_calls, 1)
        self.assertEqual(playwright.stop_calls, 1)

    def test_all_platform_workflow_runs_only_present_supported_targets(self) -> None:
        for present, expected in (
            (("baijiahao", "wechat"), ["baijiahao", "toutiao_article", "wechat"]),
            (("baijiahao",), ["baijiahao", "toutiao_article"]),
            (("wechat",), ["wechat"]),
        ):
            with self.subTest(present=present):
                context = _FakeContext()
                playwright = _FakePlaywright()
                recording = _RecordingController()
                page = object()
                calls: list[str] = []

                def record_baijiahao(*args) -> None:
                    del args
                    calls.append("baijiahao")

                def record_wechat(*args):
                    del args
                    calls.append("wechat")
                    return page

                def record_toutiao(*args) -> None:
                    del args
                    calls.append("toutiao_article")

                with patch(
                    "alarkive_publisher.workflow.start_browser",
                    return_value=(playwright, context, page),
                ), patch(
                    "alarkive_publisher.workflow.run_baijiahao",
                    side_effect=record_baijiahao,
                ), patch(
                    "alarkive_publisher.workflow.run_toutiao_article",
                    side_effect=record_toutiao,
                ), patch(
                    "alarkive_publisher.workflow.run_wechat",
                    side_effect=record_wechat,
                ):
                    run_publisher_workflow(
                        self._post_with_platforms(*present),
                        Path("."),
                        cast(WorkflowController, recording),
                    )

                self.assertEqual(calls, expected)
                self.assertEqual(context.close_calls, 1)
                self.assertEqual(playwright.stop_calls, 1)

    def test_all_platform_workflow_rejects_xiaohongshu_only_package(self) -> None:
        with patch("alarkive_publisher.workflow.start_browser") as start_browser:
            with self.assertRaisesRegex(ValueError, "没有可用于完整发布流程"):
                run_publisher_workflow(
                    self._post_with_platforms("xiaohongshu"),
                    Path("."),
                    cast(WorkflowController, _RecordingController()),
                )
        start_browser.assert_not_called()


if __name__ == "__main__":
    unittest.main()
