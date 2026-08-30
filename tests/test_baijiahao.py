from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from alarkive_publisher.baijiahao import _insert_images, _set_image_files


class _SingleFileInput:
    def __init__(self) -> None:
        self.selected: list[str] = []

    def get_attribute(self, name: str) -> str | None:
        self.requested_attribute = name
        return None

    def set_input_files(self, path: str, *, timeout: int) -> None:
        self.selected.append(path)
        self.timeout = timeout


class BaijiahaoUploadTests(unittest.TestCase):
    def test_single_file_input_accepts_multiple_images_one_by_one(self) -> None:
        file_input = _SingleFileInput()

        _set_image_files(file_input, ["01.png", "02.png", "03.png"])  # type: ignore[arg-type]

        self.assertEqual(file_input.selected, ["01.png", "02.png", "03.png"])
        self.assertEqual(file_input.requested_attribute, "multiple")
        self.assertEqual(file_input.timeout, 30_000)

    def test_insert_images_prefers_active_insert_menu(self) -> None:
        page = object()
        editor = object()

        with patch(
            "alarkive_publisher.baijiahao._editor_image_count", return_value=0
        ), patch(
            "alarkive_publisher.baijiahao._focus_editor_for_image_insertion"
        ) as focus_editor, patch(
            "alarkive_publisher.baijiahao._upload_from_insert_menu",
            return_value="dialog",
        ) as upload_from_menu, patch(
            "alarkive_publisher.baijiahao._wait_for_upload_finish"
        ) as wait_for_upload, patch(
            "alarkive_publisher.baijiahao._select_uploaded_thumbnails"
        ) as select_thumbnails, patch(
            "alarkive_publisher.baijiahao._confirm_image_dialog"
        ) as confirm_dialog, patch(
            "alarkive_publisher.baijiahao._wait_for_editor_images"
        ) as wait_for_editor, patch(
            "alarkive_publisher.baijiahao._image_trigger"
        ) as legacy_trigger:
            _insert_images(page, editor, (Path("image.png"),))  # type: ignore[arg-type]

        focus_editor.assert_called_once_with(editor)
        upload_from_menu.assert_called_once()
        wait_for_upload.assert_called_once_with(page, 1)
        select_thumbnails.assert_called_once_with(page, 1)
        confirm_dialog.assert_called_once_with(page)
        wait_for_editor.assert_called_once_with(page, editor, 1)
        legacy_trigger.assert_not_called()


if __name__ == "__main__":
    unittest.main()
