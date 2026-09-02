from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import Error as PlaywrightError

from alarkive_publisher.content import ContentVariant, PostContent
from alarkive_publisher.inline_images import ImageBlock, TextBlock
from alarkive_publisher.toutiao_article import (
    EDITOR_URL,
    ToutiaoImageUploadContext,
    _navigate,
    _image_file_inputs,
    _fill_body_with_inline_images,
    _fill_title,
    _inline_image_blocks,
    _insert_images_at_end,
    _insert_image,
    _select_uploaded_toutiao_image,
    _wait_for_upload_complete,
    _wait_for_upload_started,
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

    def test_navigation_tolerates_aborted_same_domain_spa_redirect(self) -> None:
        class Page:
            url = EDITOR_URL

            def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
                self.args = (url, wait_until, timeout)
                raise PlaywrightError("Page.goto: net::ERR_ABORTED at " + url)

        page = Page()
        with patch("alarkive_publisher.toutiao_article._wait_for_dom") as wait_for_dom:
            _navigate(page, EDITOR_URL)  # type: ignore[arg-type]

        self.assertEqual(page.args, (EDITOR_URL, "commit", 60_000))
        wait_for_dom.assert_called_once_with(page)

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
        ), patch(
            "alarkive_publisher.toutiao_article._editor_image_count",
            side_effect=[0, 3],
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
        ), patch(
            "alarkive_publisher.toutiao_article._editor_image_count",
            side_effect=[0, 3],
        ):
            _insert_images_at_end(object(), object(), images)  # type: ignore[arg-type]
        self.assertEqual(calls, list(images))

    def test_insert_image_focuses_editor_before_finding_trigger(self) -> None:
        events: list[str] = []

        class Chooser:
            def set_files(self, path: str, *, timeout: int) -> None:
                del path, timeout

        context = ToutiaoImageUploadContext(
            frame=object(), root=None, mode="native", chooser=Chooser()  # type: ignore[arg-type]
        )
        with patch(
            "alarkive_publisher.toutiao_article._editor_image_count",
            return_value=0,
        ), patch(
            "alarkive_publisher.toutiao_article._focus_editor_for_image_insertion",
            side_effect=lambda editor: events.append("focus"),
        ), patch(
            "alarkive_publisher.toutiao_article._image_trigger",
            side_effect=lambda page, editor: events.append("trigger") or object(),
        ), patch(
            "alarkive_publisher.toutiao_article._open_inline_image_upload",
            return_value=context,
        ), patch(
            "alarkive_publisher.toutiao_article._wait_for_upload_started"
        ), patch(
            "alarkive_publisher.toutiao_article._wait_for_upload_complete"
        ), patch(
            "alarkive_publisher.toutiao_article._wait_for_editor_images"
        ):
            _insert_image(object(), object(), Path("01.png"))  # type: ignore[arg-type]

        self.assertEqual(events[:2], ["focus", "trigger"])

    def test_image_inputs_never_use_page_global_fallback(self) -> None:
        with patch(
            "alarkive_publisher.toutiao_article._visible_inline_upload_contexts",
            return_value=[],
        ):
            self.assertEqual(_image_file_inputs(object()), [])  # type: ignore[arg-type]

    def test_image_input_is_scoped_to_open_inline_upload_context(self) -> None:
        class Input:
            def __init__(self, accept: str, input_id: str | None = None) -> None:
                self.accept = accept
                self.input_id = input_id

            def get_attribute(self, name: str) -> str | None:
                if name == "accept":
                    return self.accept
                if name == "id":
                    return self.input_id
                return None

        class Collection:
            def __init__(self, values: list[Input]) -> None:
                self.values = values

            def count(self) -> int:
                return len(self.values)

            def nth(self, index: int) -> Input:
                return self.values[index]

        class Root:
            def __init__(self) -> None:
                self.inputs = Collection(
                    [
                        Input("image/png", "upload-drag-input"),
                        Input("image/png", "article-upload-input"),
                    ]
                )

            def locator(self, selector: str) -> Collection:
                self.selector = selector
                return self.inputs

        root = Root()
        context = ToutiaoImageUploadContext(
            frame=object(), root=root, mode="dialog"  # type: ignore[arg-type]
        )
        inputs = _image_file_inputs(object(), context)  # type: ignore[arg-type]
        self.assertEqual(len(inputs), 1)
        self.assertIs(inputs[0], root.inputs.values[1])

    def test_image_input_prefers_toutiao_change_handler_over_drag_input(self) -> None:
        class Input:
            def __init__(self, input_id: str) -> None:
                self.input_id = input_id

            def get_attribute(self, name: str) -> str | None:
                if name == "accept":
                    return "image/*"
                if name == "id":
                    return self.input_id
                return None

        class Collection:
            def __init__(self, values: list[Input]) -> None:
                self.values = values

            def count(self) -> int:
                return len(self.values)

            def nth(self, index: int) -> Input:
                return self.values[index]

        class Root:
            def __init__(self) -> None:
                self.inputs = Collection(
                    [Input("upload-drag-input"), Input("normal-upload-input")]
                )

            def locator(self, selector: str) -> Collection:
                del selector
                return self.inputs

        context = ToutiaoImageUploadContext(
            frame=object(), root=Root(), mode="drawer"  # type: ignore[arg-type]
        )
        inputs = _image_file_inputs(object(), context)  # type: ignore[arg-type]
        self.assertEqual([item.get_attribute("id") for item in inputs], ["normal-upload-input"])

    def test_upload_start_does_not_accept_first_idle_observation(self) -> None:
        class Page:
            def __init__(self) -> None:
                self.waits = 0

            def wait_for_timeout(self, milliseconds: int) -> None:
                del milliseconds
                self.waits += 1

        page = Page()
        context = ToutiaoImageUploadContext(frame=object(), root=object(), mode="dialog")  # type: ignore[arg-type]
        with patch(
            "alarkive_publisher.toutiao_article._raise_if_upload_failed"
        ), patch(
            "alarkive_publisher.toutiao_article._editor_image_count",
            return_value=0,
        ), patch(
            "alarkive_publisher.toutiao_article._context_is_busy",
            side_effect=[False, True],
        ), patch(
            "alarkive_publisher.toutiao_article._new_thumbnail_present",
            return_value=False,
        ):
            _wait_for_upload_started(page, context, object(), 0, timeout=1_000)  # type: ignore[arg-type]

        self.assertEqual(page.waits, 1)

    def test_upload_completion_requires_ready_state_and_stability(self) -> None:
        class Page:
            def __init__(self) -> None:
                self.waits = 0

            def wait_for_timeout(self, milliseconds: int) -> None:
                del milliseconds
                self.waits += 1

        page = Page()
        context = ToutiaoImageUploadContext(frame=object(), root=object(), mode="dialog")  # type: ignore[arg-type]
        with patch(
            "alarkive_publisher.toutiao_article._raise_if_upload_failed"
        ), patch(
            "alarkive_publisher.toutiao_article._editor_image_count",
            return_value=0,
        ), patch(
            "alarkive_publisher.toutiao_article._context_is_busy",
            side_effect=[False, False],
        ), patch(
            "alarkive_publisher.toutiao_article._successful_upload_present",
            side_effect=[False, False, True, True],
        ), patch(
            "alarkive_publisher.toutiao_article._context_text",
            return_value="",
        ):
            _wait_for_upload_complete(page, context, object(), 0, timeout=1_000)  # type: ignore[arg-type]

        self.assertEqual(page.waits, 3)

    def test_select_uploaded_toutiao_image_chooses_new_thumbnail(self) -> None:
        class Thumbnail:
            def __init__(self, src: str) -> None:
                self.src = src
                self.clicked = False

            def get_attribute(self, name: str) -> str | None:
                return self.src if name == "src" else "thumbnail"

            def inner_text(self, *, timeout: int) -> str:
                del timeout
                return ""

            def text_content(self, *, timeout: int) -> str:
                del timeout
                return ""

            def click(self, *, force: bool) -> None:
                del force
                self.clicked = True

        class Collection:
            def __init__(self, values: list[Thumbnail]) -> None:
                self.values = values

            def count(self) -> int:
                return len(self.values)

            def nth(self, index: int) -> Thumbnail:
                return self.values[index]

        old = Thumbnail("old.png")
        new = Thumbnail("new.png")

        class Root:
            def locator(self, selector: str) -> Collection:
                del selector
                return Collection([old, new])

        context = ToutiaoImageUploadContext(
            frame=object(),
            root=Root(),  # type: ignore[arg-type]
            mode="dialog",
            before_thumbnails=("old.png|thumbnail|thumbnail|thumbnail|thumbnail|",),
        )
        _select_uploaded_toutiao_image(context)
        self.assertFalse(old.clicked)
        self.assertTrue(new.clicked)


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
