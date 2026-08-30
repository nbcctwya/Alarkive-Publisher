from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from alarkive_publisher.content import load_post
from alarkive_publisher.web.storage import (
    ImageData,
    MAX_IMAGE_COUNT,
    MAX_IMAGE_SIZE_BYTES,
    PNG_SIGNATURE,
    get_post_detail,
    save_post,
)
from alarkive_publisher.web import storage


PLATFORMS = ("xiaohongshu", "baijiahao", "wechat")
PNG = PNG_SIGNATURE + b"minimal test data"


class StorageValidationTests(unittest.TestCase):
    def _save(
        self,
        root: Path,
        *,
        titles: dict[str, str] | None = None,
        bodies: dict[str, str] | None = None,
        images: list[ImageData] | None = None,
    ):
        return save_post(
            "测试任务",
            titles or {platform: "标题" for platform in PLATFORMS},
            bodies or {platform: "正文" for platform in PLATFORMS},
            images or [ImageData("image.png", PNG)],
            posts_root=root,
        )

    def test_titles_are_trimmed_but_markdown_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            body = "第一行\r\n\r\n第二行 🚀\r\n"
            saved = self._save(
                root,
                titles={platform: "  Test Title  " for platform in PLATFORMS},
                bodies={platform: body for platform in PLATFORMS},
            )

            self.assertEqual(
                {value["title"] for value in saved.manifest["platforms"].values()},
                {"Test Title"},
            )
            self.assertEqual(load_post(saved.directory).xiaohongshu.title, "Test Title")
            detail = get_post_detail(saved.id, root)
            self.assertEqual(detail["platform_contents"][0]["title"], "Test Title")
            self.assertEqual(detail["platform_contents"][0]["body"], body)

    def test_fake_png_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "不是有效的 PNG"):
                self._save(Path(temp), images=[ImageData("image.png", b"not png")])

    def test_single_image_size_limit_is_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            oversized = PNG_SIGNATURE + b"x" * MAX_IMAGE_SIZE_BYTES
            with self.assertRaisesRegex(ValueError, "单张图片不能超过 20 MB"):
                self._save(Path(temp), images=[ImageData("large.png", oversized)])

    def test_total_image_size_limit_is_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(storage, "MAX_TOTAL_IMAGE_SIZE_BYTES", 20):
                images = [
                    ImageData("one.png", PNG_SIGNATURE + b"12345678"),
                    ImageData("two.png", PNG_SIGNATURE + b"12345678"),
                ]
                with self.assertRaisesRegex(ValueError, "图片总大小超过限制"):
                    self._save(Path(temp), images=images)

    def test_image_count_limit_is_enforced_without_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            images = [ImageData(f"{index:02d}.png", PNG) for index in range(MAX_IMAGE_COUNT + 1)]
            with self.assertRaisesRegex(ValueError, "最多上传 20 张"):
                self._save(Path(temp), images=images)


if __name__ == "__main__":
    unittest.main()
