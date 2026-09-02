from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from alarkive_publisher.content import ContentVariant, PostContent
from alarkive_publisher.inline_images import ImageBlock, TextBlock
from alarkive_publisher.toutiao_article import (
    EDITOR_URL,
    _fill_body_with_inline_images,
    _fill_title,
    _inline_image_blocks,
    _insert_images_at_end,
    run_toutiao_article,
)
from alarkive_publisher.workflow_controller import WorkflowController


class _RecordingController(WorkflowController):
    def __init__(self) -> None:
        self.steps: list[tuple[str, str]] = []

    def start_platform(self, platform: str) -> None:
        del platform

    def step(self, platform: str, step: str, message: str) -> None:
        del message
        self.steps.append((platform, step))

    def wait_for_user(self, platform: str, step: str, message: str, prompt: str) -> None:
        del platform, step, message, prompt

    def ready(self, platform: str, message: str, prompt: str) -> None:
        del platform, message, prompt

    def system_step(self, step: str, message: str) -> None:
        del step, message

    def completed(self, message: str) -> None:
        del message

    def failed(self, platform: str | None, step: str, exc: BaseException) -> None:
        del platform, step, exc


def _post(content: ContentVariant | None) -> PostContent:
    return PostContent(
        folder=Path("."),
        id="20260902-170000-a7c3",
        name="测试任务",
        created_at="2026-09-02T17:00:00+08:00",
        public_long=content,
    )


class ToutiaoArticleRoutingTests(unittest.TestCase):
    def test_editor_entrypoint_is_the_current_graphic_publish_page(self) -> None:
        self.assertEqual(EDITOR_URL, "https://mp.toutiao.com/profile_v4/graphic/publish")

    def test_marker_plan_reads_public_long_and_appends_unused_images_in_order(self) -> None:
        content = ContentVariant(
            title="头条标题",
            body="A\n\n[[image:3]]\n\nB\n\n[[image:1]]\n\nC",
            images=(Path("01.png"), Path("02.png"), Path("03.png")),
        )

        blocks, has_markers = _inline_image_blocks(content)

        self.assertTrue(has_markers)
        self.assertEqual(
            [block for block in blocks if isinstance(block, ImageBlock)],
            [ImageBlock(3), ImageBlock(1), ImageBlock(2)],
        )

    def test_marker_blocks_are_inserted_in_source_order_with_public_long_paths(self) -> None:
        content = ContentVariant(
            title="头条标题",
            body="A\n\n[[image:3]]\n\nB\n\n[[image:1]]\n\nC\n\n[[image:2]]",
            images=(Path("01.png"), Path("02.png"), Path("03.png")),
        )
        blocks = (
            TextBlock("A\n\n"),
            ImageBlock(3),
            TextBlock("\n\nB\n\n"),
            ImageBlock(1),
            TextBlock("\n\nC\n\n"),
            ImageBlock(2),
        )
        events: list[tuple[str, str]] = []

        with patch(
            "alarkive_publisher.toutiao_article._append_rendered_html",
            side_effect=lambda body, rendered: events.append(("text", rendered.text)),
        ), patch(
            "alarkive_publisher.toutiao_article._insert_image",
            side_effect=lambda page, body, image: events.append(("image", image.name)),
        ), patch(
            "alarkive_publisher.toutiao_article._inline_text_blocks_are_present",
            return_value=True,
        ):
            _fill_body_with_inline_images(object(), object(), content, blocks)  # type: ignore[arg-type]

        self.assertEqual(
            events,
            [
                ("text", "A"),
                ("image", "03.png"),
                ("text", "B"),
                ("image", "01.png"),
                ("text", "C"),
                ("image", "02.png"),
            ],
        )

    def test_no_marker_mode_appends_all_images_in_manifest_order(self) -> None:
        images = (Path("01.png"), Path("02.png"), Path("03.png"))
        calls: list[Path] = []
        with patch(
            "alarkive_publisher.toutiao_article._focus_editor_for_image_insertion"
        ), patch(
            "alarkive_publisher.toutiao_article._insert_image",
            side_effect=lambda page, body, image: calls.append(image),
        ):
            _insert_images_at_end(object(), object(), images)  # type: ignore[arg-type]
        self.assertEqual(calls, list(images))


class ToutiaoArticlePublisherTests(unittest.TestCase):
    def test_title_filler_uses_public_long_title_exactly(self) -> None:
        class Title:
            def __init__(self) -> None:
                self.value = "默认标题"

            def fill(self, value: str, *, timeout: int) -> None:
                del timeout
                self.value = value

            def input_value(self) -> str:
                return self.value

        title = Title()
        content = ContentVariant("public_long title", "body", ())
        with patch("alarkive_publisher.toutiao_article._title_locator", return_value=title):
            _fill_title(object(), content)  # type: ignore[arg-type]
        self.assertEqual(title.value, "public_long title")

    def test_missing_public_long_fails_before_using_page_or_starting_browser(self) -> None:
        controller = _RecordingController()
        with self.assertRaisesRegex(Exception, "does not contain public_long"):
            run_toutiao_article(object(), _post(None), controller)  # type: ignore[arg-type]
        self.assertEqual(controller.steps, [])

    def test_run_uses_only_public_long_and_never_invokes_other_publishers(self) -> None:
        content = ContentVariant(
            title="同一标题",
            body="同一正文",
            images=(Path("01.png"),),
        )
        post = _post(content)
        controller = _RecordingController()
        body = object()
        with patch("alarkive_publisher.toutiao_article._check_login"), patch(
            "alarkive_publisher.toutiao_article._open_editor"
        ), patch(
            "alarkive_publisher.toutiao_article._inline_image_blocks",
            return_value=((TextBlock("同一正文"),), False),
        ) as plan, patch(
            "alarkive_publisher.toutiao_article._fill_title"
        ) as fill_title, patch(
            "alarkive_publisher.toutiao_article._body_locator", return_value=body
        ), patch(
            "alarkive_publisher.toutiao_article._fill_body"
        ) as fill_body, patch(
            "alarkive_publisher.toutiao_article._insert_images_at_end"
        ) as insert_images:
            run_toutiao_article(object(), post, controller)  # type: ignore[arg-type]

        plan.assert_called_once_with(content)
        fill_title.assert_called_once_with(unittest.mock.ANY, content)
        fill_body.assert_called_once_with(unittest.mock.ANY, body, content)
        insert_images.assert_called_once_with(unittest.mock.ANY, body, content.images)
        self.assertEqual(
            controller.steps,
            [
                ("toutiao_article", "checking_login"),
                ("toutiao_article", "opening_editor"),
                ("toutiao_article", "filling_content"),
                ("toutiao_article", "uploading_images"),
                ("toutiao_article", "final_check"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
