from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from alarkive_publisher.content import load_post
from alarkive_publisher.web.publish_state import (
    default_publish_state,
    load_publish_state,
    save_publish_state,
)
from alarkive_publisher.web.storage import (
    ImageData,
    PNG_SIGNATURE,
    StorageError,
    save_post,
    update_post,
)
from alarkive_publisher.web import storage


PNG = PNG_SIGNATURE + b"minimal test data"


class EditPackageTests(unittest.TestCase):
    @staticmethod
    def _manifest(package: Path) -> dict:
        return json.loads((package / "manifest.json").read_text(encoding="utf-8"))

    def test_v02_edit_is_in_place_and_preserves_metadata_state_and_images(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            saved = save_post(
                "原任务",
                {"public_long": "旧公域", "wechat_short": "旧微信"},
                {"public_long": "旧公域正文", "wechat_short": "旧微信正文"},
                [ImageData("one.png", PNG)],
                posts_root=root,
            )
            original_manifest = self._manifest(saved.directory)
            original_state = default_publish_state()
            original_state["published"] = True
            original_state["published_at"] = "2026-09-03T10:00:00+08:00"
            save_publish_state(saved.directory, original_state)

            updated = update_post(
                saved.id,
                {"public_long": "新公域", "wechat_long": "微信长文"},
                {"public_long": "新公域正文", "wechat_long": "微信长文正文"},
                posts_root=root,
            )

            manifest = self._manifest(saved.directory)
            self.assertEqual(updated.directory, saved.directory)
            self.assertEqual(updated.id, original_manifest["id"])
            self.assertEqual(manifest["id"], original_manifest["id"])
            self.assertEqual(manifest["name"], original_manifest["name"])
            self.assertEqual(manifest["created_at"], original_manifest["created_at"])
            self.assertEqual(set(manifest["content"]), {"public_long", "wechat_long"})
            self.assertEqual(
                (saved.directory / "content" / "public_long.md").read_text(encoding="utf-8"),
                "新公域正文",
            )
            self.assertEqual(
                (saved.directory / "content" / "wechat_long.md").read_text(encoding="utf-8"),
                "微信长文正文",
            )
            self.assertEqual(load_publish_state(saved.directory), original_state)
            self.assertEqual(load_post(saved.directory).public_long.title, "新公域")
            self.assertEqual(manifest["content"]["public_long"]["images"], ["images/01.png"])
            self.assertTrue((saved.directory / "images" / "01.png").is_file())

    def test_v01_edit_upgrades_in_place_and_retains_xiaohongshu_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            saved = save_post(
                "旧 Package",
                {
                    "xiaohongshu": "小红书标题",
                    "baijiahao": "百家号标题",
                    "wechat": "微信标题",
                },
                {
                    "xiaohongshu": "小红书正文",
                    "baijiahao": "百家号正文",
                    "wechat": "微信正文",
                },
                [ImageData("one.png", PNG)],
                posts_root=root,
            )
            original_manifest = self._manifest(saved.directory)
            xiaohongshu_file = saved.directory / "content" / "xiaohongshu.md"
            xiaohongshu_file.write_text("旧小红书正文", encoding="utf-8")

            update_post(
                saved.id,
                {
                    "public_long": "百家号的新标题",
                    "wechat_short": "微信的新标题",
                    "wechat_long": "新增微信长文",
                    "toutiao_short": "新增微头条",
                },
                {
                    "public_long": "百家号的新正文",
                    "wechat_short": "微信的新正文",
                    "wechat_long": "新增微信长文正文",
                    "toutiao_short": "新增微头条正文",
                },
                posts_root=root,
            )

            manifest = self._manifest(saved.directory)
            self.assertEqual(manifest["schema_version"], "0.2")
            self.assertEqual(manifest["id"], original_manifest["id"])
            self.assertEqual(manifest["name"], original_manifest["name"])
            self.assertEqual(manifest["created_at"], original_manifest["created_at"])
            self.assertEqual(
                set(manifest["content"]),
                {"public_long", "wechat_short", "wechat_long", "toutiao_short"},
            )
            self.assertTrue(xiaohongshu_file.is_file())
            self.assertEqual(xiaohongshu_file.read_text(encoding="utf-8"), "旧小红书正文")
            self.assertFalse((saved.directory / "content" / "baijiahao.md").exists())
            self.assertFalse((saved.directory / "content" / "wechat.md").exists())
            self.assertEqual(load_post(saved.directory).wechat_short.title, "微信的新标题")

    def test_invalid_edit_leaves_original_package_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            saved = save_post(
                "原任务",
                {"public_long": "旧标题"},
                {"public_long": "旧正文"},
                [ImageData("one.png", PNG)],
                posts_root=root,
            )
            original_manifest = (saved.directory / "manifest.json").read_bytes()
            original_body = (saved.directory / "content" / "public_long.md").read_bytes()

            with self.assertRaisesRegex(StorageError, "duplicate image marker"):
                update_post(
                    saved.id,
                    {"public_long": "新标题"},
                    {"public_long": "新正文\n[[image:1]]\n[[image:1]]"},
                    posts_root=root,
                )

            self.assertEqual((saved.directory / "manifest.json").read_bytes(), original_manifest)
            self.assertEqual((saved.directory / "content" / "public_long.md").read_bytes(), original_body)

    def test_windows_directory_lock_uses_manifest_commit_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            saved = save_post(
                "原任务",
                {"public_long": "旧标题"},
                {"public_long": "旧正文"},
                [ImageData("one.png", PNG)],
                posts_root=root,
            )
            denied = StorageError("目录被占用")
            denied.__cause__ = PermissionError(13, "目录被占用")

            with patch.object(storage, "_replace_directory_atomically", side_effect=denied), patch.object(
                storage, "_is_windows_access_denied", return_value=True
            ):
                update_post(
                    saved.id,
                    {"public_long": "新标题"},
                    {"public_long": "新正文"},
                    posts_root=root,
                )

            manifest = self._manifest(saved.directory)
            self.assertEqual(load_post(saved.directory).public_long.title, "新标题")
            self.assertEqual(
                (saved.directory / manifest["content"]["public_long"]["content_file"]).read_text(
                    encoding="utf-8"
                ),
                "新正文",
            )


if __name__ == "__main__":
    unittest.main()
