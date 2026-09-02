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

    def test_single_platform_writes_only_that_platform(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            saved = self._save(
                root,
                titles={"baijiahao": "百家号标题"},
                bodies={"baijiahao": "百家号正文"},
            )

            self.assertEqual(set(saved.manifest["platforms"]), {"baijiahao"})
            self.assertTrue((saved.directory / "content" / "baijiahao.md").is_file())
            self.assertFalse((saved.directory / "content" / "xiaohongshu.md").exists())
            self.assertFalse((saved.directory / "content" / "wechat.md").exists())
            post = load_post(saved.directory)
            self.assertIsNone(post.xiaohongshu)
            self.assertIsNotNone(post.baijiahao)
            self.assertIsNone(post.wechat)
            self.assertEqual(
                [item["key"] for item in get_post_detail(saved.id, root)["platform_contents"]],
                ["baijiahao"],
            )

    def test_two_platforms_are_saved_without_empty_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            saved = self._save(
                root,
                titles={"baijiahao": "百家号标题", "wechat": "微信标题"},
                bodies={"baijiahao": "百家号正文", "wechat": "微信正文"},
            )

            self.assertEqual(set(saved.manifest["platforms"]), {"baijiahao", "wechat"})
            self.assertFalse((saved.directory / "content" / "xiaohongshu.md").exists())

    def test_all_platforms_empty_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "至少需要填写一个平台"):
                save_post(
                    "测试任务",
                    {platform: "" for platform in PLATFORMS},
                    {platform: "" for platform in PLATFORMS},
                    [ImageData("image.png", PNG)],
                    posts_root=Path(temp),
                )

    def test_partial_platform_content_is_rejected(self) -> None:
        cases = (
            ({"baijiahao": "标题"}, {"baijiahao": ""}),
            ({"baijiahao": ""}, {"baijiahao": "正文"}),
            (
                {"baijiahao": "标题", "wechat": "微信标题"},
                {"baijiahao": "", "wechat": "微信正文"},
            ),
        )
        for titles, bodies in cases:
            with self.subTest(titles=titles, bodies=bodies), tempfile.TemporaryDirectory() as temp:
                with self.assertRaisesRegex(ValueError, "百家号的标题和正文需要同时填写"):
                    save_post(
                        "测试任务",
                        titles,
                        bodies,
                        [ImageData("image.png", PNG)],
                        posts_root=Path(temp),
                    )

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
