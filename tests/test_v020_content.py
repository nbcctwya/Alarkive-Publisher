from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from alarkive_publisher.content import ContentError, load_post
from alarkive_publisher.web.storage import ImageData, PNG_SIGNATURE, get_post_detail, save_post


PNG = PNG_SIGNATURE + b"minimal test data"
VARIANTS = ("public_long", "wechat_long", "wechat_short", "toutiao_short")


class ContentVariantPackageTests(unittest.TestCase):
    @staticmethod
    def _save(root: Path, variants: tuple[str, ...]):
        return save_post(
            "Variant 测试",
            {variant: f"{variant} title" for variant in variants},
            {variant: f"{variant} body" for variant in variants},
            [ImageData("one.png", PNG), ImageData("two.png", PNG)],
            posts_root=root,
        )

    @staticmethod
    def _manifest(package: Path) -> dict:
        return json.loads((package / "manifest.json").read_text(encoding="utf-8"))

    @staticmethod
    def _write_manifest(package: Path, manifest: dict) -> None:
        (package / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def test_each_variant_can_be_saved_alone_without_empty_files(self) -> None:
        for variant in VARIANTS:
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as temp:
                saved = self._save(Path(temp), (variant,))
                self.assertEqual(saved.manifest["schema_version"], "0.2")
                self.assertEqual(set(saved.manifest["content"]), {variant})
                self.assertTrue((saved.directory / "content" / f"{variant}.md").is_file())
                for other in set(VARIANTS) - {variant}:
                    self.assertFalse((saved.directory / "content" / f"{other}.md").exists())
                post = load_post(saved.directory)
                self.assertTrue(post.has_content(variant))
                self.assertEqual(
                    [key for key in VARIANTS if post.has_content(key)], [variant]
                )

    def test_multiple_variants_and_detail_routing_are_available(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            saved = self._save(Path(temp), ("public_long", "wechat_short"))
            post = load_post(saved.directory)
            detail = get_post_detail(saved.id, Path(temp))

        self.assertIsNotNone(post.public_long)
        self.assertIsNotNone(post.wechat_short)
        self.assertIsNone(post.wechat_long)
        self.assertEqual(
            [item["key"] for item in detail["variant_contents"]],
            ["public_long", "wechat_short"],
        )
        statuses = {item["target"]: item["status"] for item in detail["publish_targets"]}
        self.assertEqual(statuses["baijiahao"], "available")
        self.assertEqual(statuses["wechat_image"], "available")
        self.assertEqual(statuses["toutiao_article"], "not_implemented")
        self.assertEqual(statuses["wechat_article"], "no_content")

    def test_all_variants_empty_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "至少需要填写一个平台"):
                save_post(
                    "空任务",
                    {variant: "" for variant in VARIANTS},
                    {variant: "" for variant in VARIANTS},
                    [ImageData("one.png", PNG)],
                    posts_root=Path(temp),
                )

    def test_any_half_filled_variant_is_rejected_even_with_another_complete(self) -> None:
        cases = (
            ({"public_long": "标题"}, {"public_long": ""}),
            ({"wechat_long": ""}, {"wechat_long": "正文"}),
            (
                {"public_long": "标题", "wechat_short": "完整标题"},
                {"public_long": "", "wechat_short": "完整正文"},
            ),
        )
        for titles, bodies in cases:
            with self.subTest(titles=titles, bodies=bodies), tempfile.TemporaryDirectory() as temp:
                with self.assertRaisesRegex(ValueError, "标题和正文需要同时填写"):
                    save_post(
                        "半填任务", titles, bodies, [ImageData("one.png", PNG)], posts_root=Path(temp)
                    )

    def test_empty_content_and_unknown_variant_are_rejected_by_loader(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            package = self._save(Path(temp), ("public_long",))
            manifest = self._manifest(package.directory)
            manifest["content"] = {}
            self._write_manifest(package.directory, manifest)
            with self.assertRaisesRegex(ContentError, "at least one supported"):
                load_post(package.directory)

            manifest["content"] = {"unknown_variant": {}}
            self._write_manifest(package.directory, manifest)
            with self.assertRaisesRegex(ContentError, "Unsupported Content Variant"):
                load_post(package.directory)

    def test_existing_variant_still_requires_complete_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            package = self._save(Path(temp), ("public_long",))
            manifest = self._manifest(package.directory)
            del manifest["content"]["public_long"]["images"]
            self._write_manifest(package.directory, manifest)
            with self.assertRaisesRegex(ContentError, "images is missing"):
                load_post(package.directory)

    def test_long_variants_validate_markers_and_short_variants_do_not(self) -> None:
        for variant, body, error in (
            ("public_long", "正文\n[[image:1]]\n[[image:1]]", "duplicate image marker"),
            ("wechat_long", "正文\n[[image:3]]", "invalid image marker"),
            ("public_long", "正文 [[image:1]]", "malformed image marker"),
        ):
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as temp:
                package = self._save(Path(temp), (variant,))
                (package.directory / "content" / f"{variant}.md").write_text(
                    body, encoding="utf-8"
                )
                with self.assertRaisesRegex(ContentError, error):
                    load_post(package.directory)

        with tempfile.TemporaryDirectory() as temp:
            package = self._save(Path(temp), ("wechat_short",))
            (package.directory / "content" / "wechat_short.md").write_text(
                "正文 [[image:999]]", encoding="utf-8"
            )
            self.assertIsNotNone(load_post(package.directory).wechat_short)

    def test_legacy_v01_package_is_read_as_new_variants_without_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            saved = save_post(
                "Legacy 测试",
                {"baijiahao": "百家号", "wechat": "微信"},
                {"baijiahao": "公域正文", "wechat": "图文正文"},
                [ImageData("one.png", PNG)],
                posts_root=Path(temp),
            )
            before = (saved.directory / "manifest.json").read_bytes()
            post = load_post(saved.directory)
            after = (saved.directory / "manifest.json").read_bytes()

        self.assertEqual(saved.manifest["schema_version"], "0.1")
        self.assertEqual(post.public_long.title, "百家号")
        self.assertEqual(post.wechat_short.title, "微信")
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
