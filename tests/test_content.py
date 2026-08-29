from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from alarkive_publisher.content import ContentError, load_post
from alarkive_publisher.web.storage import ImageData, save_post


PLATFORMS = ("xiaohongshu", "baijiahao", "wechat")


class PackageLoaderTests(unittest.TestCase):
    def _make_package(self, root: Path) -> Path:
        body = "第一段\r\n\r\n**64GB：甜点**\r\n\r\n🚀 Emoji\r\n"
        titles = {platform: f"{platform} manifest title" for platform in PLATFORMS}
        bodies = {platform: body for platform in PLATFORMS}
        return save_post(
            "Bridge 测试",
            titles,
            bodies,
            [ImageData("first.png", b"first"), ImageData("second.png", b"second")],
            posts_root=root,
        ).directory

    @staticmethod
    def _read_manifest(package: Path) -> dict:
        return json.loads((package / "manifest.json").read_text(encoding="utf-8"))

    @staticmethod
    def _write_manifest(package: Path, manifest: dict) -> None:
        (package / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def test_web_writer_package_loads_with_independent_image_orders(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            package = self._make_package(Path(temp))
            manifest = self._read_manifest(package)
            manifest["platforms"]["xiaohongshu"]["images"] = [
                "images/02.png",
                "images/01.png",
            ]
            manifest["platforms"]["baijiahao"]["images"] = ["images/01.png"]
            manifest["platforms"]["wechat"]["images"] = ["images/02.png"]
            self._write_manifest(package, manifest)
            before = (package / "manifest.json").read_bytes()

            post = load_post(package)

            self.assertEqual(post.name, "Bridge 测试")
            self.assertEqual(post.id, package.name)
            self.assertEqual(
                post.xiaohongshu.body,
                "第一段\r\n\r\n**64GB：甜点**\r\n\r\n🚀 Emoji\r\n",
            )
            self.assertEqual(
                [path.name for path in post.xiaohongshu.images],
                ["02.png", "01.png"],
            )
            self.assertEqual([path.name for path in post.baijiahao.images], ["01.png"])
            self.assertEqual([path.name for path in post.wechat.images], ["02.png"])
            self.assertEqual((package / "manifest.json").read_bytes(), before)

    def test_missing_manifest_is_rejected_before_browser_use(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            package = Path(temp) / "20260829-153400-a7c3"
            package.mkdir()

            with self.assertRaisesRegex(ContentError, "manifest.json not found"):
                load_post(package)

    def test_invalid_manifest_and_resources_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)

            malformed = root / "20260829-153400-a7c3"
            malformed.mkdir()
            (malformed / "manifest.json").write_text("{broken", encoding="utf-8")
            with self.assertRaisesRegex(ContentError, "Could not parse manifest.json"):
                load_post(malformed)

            unsupported = self._make_package(root)
            manifest = self._read_manifest(unsupported)
            manifest["schema_version"] = "9.9"
            self._write_manifest(unsupported, manifest)
            with self.assertRaisesRegex(ContentError, "schema version: 9.9"):
                load_post(unsupported)

            traversal = self._make_package(root)
            manifest = self._read_manifest(traversal)
            manifest["platforms"]["xiaohongshu"]["content_file"] = "../secret.md"
            self._write_manifest(traversal, manifest)
            with self.assertRaisesRegex(ContentError, "outside the package"):
                load_post(traversal)
