from __future__ import annotations

import unittest

from alarkive_publisher.inline_images import (
    ImageBlock,
    TextBlock,
    append_unused_images,
    inline_image_error,
    parse_inline_images,
    validate_inline_image_text,
)


class InlineImageParserTests(unittest.TestCase):
    def test_normal_markers_become_ordered_blocks(self) -> None:
        blocks = parse_inline_images(
            "正文 A\n\n[[image:1]]\n\n正文 B\n\n[[image:2]]\n\n正文 C"
        )

        self.assertEqual(
            blocks,
            (
                TextBlock("正文 A\n\n"),
                ImageBlock(1),
                TextBlock("\n正文 B\n\n"),
                ImageBlock(2),
                TextBlock("\n正文 C"),
            ),
        )

    def test_marker_must_occupy_its_own_line(self) -> None:
        blocks, validation = validate_inline_image_text(
            "正文 [[image:1]] 正文", 1
        )

        self.assertEqual(blocks, (TextBlock("正文 [[image:1]] 正文"),))
        self.assertFalse(validation.has_markers)
        self.assertEqual(validation.unused_images, (1,))

    def test_marker_allows_surrounding_whitespace_and_crlf(self) -> None:
        blocks = parse_inline_images("前文\r\n  [[image:1]]  \r\n后文")

        self.assertEqual(
            blocks,
            (TextBlock("前文\r\n"), ImageBlock(1), TextBlock("后文")),
        )

    def test_duplicate_marker_is_invalid(self) -> None:
        _, validation = validate_inline_image_text("[[image:1]]\n\n[[image:1]]", 1)

        self.assertEqual(validation.duplicate_images, (1,))
        self.assertIsNotNone(inline_image_error(validation))
        self.assertIn("duplicate image marker: [[image:1]]", str(inline_image_error(validation)))

    def test_out_of_range_marker_is_invalid(self) -> None:
        _, validation = validate_inline_image_text("正文\n[[image:4]]", 3)

        self.assertEqual(validation.invalid_images, (4,))
        self.assertIn("Only 3 images are available", str(inline_image_error(validation)))

    def test_unused_images_are_reported_and_appended(self) -> None:
        blocks, validation = validate_inline_image_text(
            "正文\n\n[[image:1]]\n\n正文\n\n[[image:3]]", 3
        )

        self.assertEqual(validation.unused_images, (2,))
        self.assertEqual(
            append_unused_images(blocks, validation)[-1],
            ImageBlock(2),
        )

    def test_no_marker_keeps_legacy_append_mode(self) -> None:
        blocks, validation = validate_inline_image_text("普通 Markdown 正文", 3)

        self.assertEqual(blocks, (TextBlock("普通 Markdown 正文"),))
        self.assertFalse(validation.has_markers)
        self.assertEqual(append_unused_images(blocks, validation), blocks)


if __name__ == "__main__":
    unittest.main()
