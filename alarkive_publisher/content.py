from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .inline_images import inline_image_error_for_label, validate_inline_image_text
from .routing import (
    CONTENT_PLATFORM_MAP,
    CONTENT_VARIANTS,
    CONTENT_VARIANT_LABELS,
    LEGACY_PLATFORM_VARIANT_MAP,
)


PACKAGE_SCHEMA_VERSION = "0.2"
LEGACY_PACKAGE_SCHEMA_VERSION = "0.1"
PACKAGE_ID_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4}$")
_IMAGE_MARKER_LINE_RE = re.compile(r"^[ \t]*\[\[image:\d+\]\][ \t]*$")

# Compatibility exports. New code should use CONTENT_VARIANTS and
# ContentVariant; these names keep old integrations importable.
PLATFORMS = ("xiaohongshu", "baijiahao", "wechat")
PLATFORM_NAMES = {
    "xiaohongshu": "Xiaohongshu",
    "baijiahao": "Baijiahao",
    "wechat": "WeChat",
}


class ContentError(Exception):
    """An expected, user-correctable Alarkive Package error."""


@dataclass(frozen=True)
class ContentVariant:
    title: str
    body: str
    images: tuple[Path, ...]


# The v0.1 publisher modules still import this name. The object is now a
# ContentVariant, not a platform-specific model.
PlatformContent = ContentVariant


@dataclass(frozen=True, init=False)
class PostContent:
    folder: Path
    id: str
    name: str
    created_at: str
    public_long: ContentVariant | None = None
    wechat_long: ContentVariant | None = None
    wechat_short: ContentVariant | None = None
    toutiao_short: ContentVariant | None = None
    # Legacy-only storage for v0.1 Xiaohongshu packages. It is never written
    # by the v0.2 Web Manager and is not part of the new routing model.
    xiaohongshu: ContentVariant | None = None

    def __init__(
        self,
        folder: Path,
        id: str,
        name: str,
        created_at: str,
        public_long: ContentVariant | None = None,
        wechat_long: ContentVariant | None = None,
        wechat_short: ContentVariant | None = None,
        toutiao_short: ContentVariant | None = None,
        xiaohongshu: ContentVariant | None = None,
        *,
        # Constructor aliases keep v0.1 integrations source-compatible while
        # the stored/read model remains variant-centric.
        baijiahao: ContentVariant | None = None,
        wechat: ContentVariant | None = None,
    ) -> None:
        if public_long is None:
            public_long = baijiahao
        if wechat_short is None:
            wechat_short = wechat
        object.__setattr__(self, "folder", folder)
        object.__setattr__(self, "id", id)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "public_long", public_long)
        object.__setattr__(self, "wechat_long", wechat_long)
        object.__setattr__(self, "wechat_short", wechat_short)
        object.__setattr__(self, "toutiao_short", toutiao_short)
        object.__setattr__(self, "xiaohongshu", xiaohongshu)

    @property
    def baijiahao(self) -> ContentVariant | None:
        """v0.1 publisher compatibility alias for public_long."""

        return self.public_long

    @property
    def wechat(self) -> ContentVariant | None:
        """v0.1 publisher compatibility alias for wechat_short."""

        return self.wechat_short

    def has_content(self, variant: str) -> bool:
        return variant in CONTENT_VARIANTS and getattr(self, variant) is not None

    def has_platform(self, platform: str) -> bool:
        """Compatibility helper for v0.1 callers."""

        if platform == "xiaohongshu":
            return self.xiaohongshu is not None
        variant = LEGACY_PLATFORM_VARIANT_MAP.get(platform)
        return variant is not None and self.has_content(variant)


def _load_manifest(package_folder: Path) -> dict[str, Any]:
    manifest_path = package_folder / "manifest.json"
    if not manifest_path.is_file():
        raise ContentError(
            "Error: manifest.json not found.\n"
            "This folder is not a valid Alarkive Package v0.2."
        )

    try:
        with manifest_path.open("r", encoding="utf-8") as file:
            manifest = json.load(file)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContentError(f"Error: Could not parse manifest.json.\n{exc}") from exc

    if not isinstance(manifest, dict):
        raise ContentError(
            "Error: Could not parse manifest.json.\nRoot must be a JSON object."
        )
    return manifest


def _resolve_package_resource(
    package_folder: Path,
    relative_path: Any,
    *,
    content_name: str,
    field_name: str,
) -> Path:
    if not isinstance(relative_path, str) or not relative_path:
        raise ContentError(f"Error: {content_name} {field_name} is missing or invalid.")

    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ContentError(
            f"Error: {content_name} {field_name} path is outside the package: "
            f"{relative_path}"
        )

    package_root = package_folder.resolve()
    resolved = (package_root / relative).resolve()
    try:
        resolved.relative_to(package_root)
    except ValueError as exc:
        raise ContentError(
            f"Error: {content_name} {field_name} path is outside the package: "
            f"{relative_path}"
        ) from exc
    return resolved


def _read_body(package_folder: Path, content_file: Any, content_name: str) -> str:
    content_path = _resolve_package_resource(
        package_folder,
        content_file,
        content_name=content_name,
        field_name="content_file",
    )
    try:
        # newline="" preserves CRLF/LF and the exact Markdown supplied by the user.
        with content_path.open("r", encoding="utf-8", newline="") as file:
            body = file.read()
    except (OSError, UnicodeDecodeError) as exc:
        raise ContentError(
            f"Error: Could not read {content_name} content file:\n"
            f"{content_file}\n{exc}"
        ) from exc
    if not body.strip():
        raise ContentError(f"Error: {content_name} content file is empty:\n{content_file}")
    return body


def _load_content_variant(
    package_folder: Path,
    key: str,
    data: Any,
    *,
    display_name: str | None = None,
    strict_fields: bool = False,
) -> ContentVariant:
    content_name = display_name or CONTENT_VARIANT_LABELS.get(key, key)
    if not isinstance(data, dict):
        raise ContentError(f"Error: {content_name} content data is missing or invalid.")
    if strict_fields:
        unknown_fields = set(data) - {"title", "content_file", "images"}
        if unknown_fields:
            raise ContentError(
                f"Error: {content_name} contains unsupported field(s): "
                + ", ".join(sorted(unknown_fields))
            )

    if "title" not in data:
        raise ContentError(f"Error: {content_name} title is missing.")
    title = data["title"]
    if not isinstance(title, str) or not title.strip():
        raise ContentError(f"Error: {content_name} title is empty or invalid.")
    title = title.strip()

    if "content_file" not in data:
        raise ContentError(f"Error: {content_name} content_file is missing.")
    body = _read_body(package_folder, data["content_file"], content_name)

    if "images" not in data:
        raise ContentError(f"Error: {content_name} images is missing.")
    image_references = data["images"]
    if not isinstance(image_references, list) or not image_references:
        raise ContentError(f"Error: {content_name} images must contain at least one image.")

    images: list[Path] = []
    for image_reference in image_references:
        image_path = _resolve_package_resource(
            package_folder,
            image_reference,
            content_name=content_name,
            field_name="images",
        )
        if (
            not isinstance(image_reference, str)
            or Path(image_reference).suffix.lower() != ".png"
        ):
            raise ContentError(
                f"Error: {content_name} image must be a PNG file: {image_reference}"
            )
        if not image_path.is_file():
            raise ContentError(
                f"Error: Could not find {content_name} image file:\n{image_reference}"
            )
        images.append(image_path)

    return ContentVariant(title=title, body=body, images=tuple(images))


def _validate_long_markers(
    key: str,
    content: ContentVariant,
    *,
    strict_syntax: bool = True,
) -> None:
    if key not in {"public_long", "wechat_long"}:
        return
    if strict_syntax:
        for line in content.body.splitlines():
            if "[[image:" in line and not _IMAGE_MARKER_LINE_RE.fullmatch(line):
                raise ContentError(
                    f"Error: {key} content contains malformed image marker. "
                    "Markers must be on their own line."
                )
    _, inline_validation = validate_inline_image_text(content.body, len(content.images))
    label = "public_long" if key == "public_long" else "wechat_long"
    inline_error = inline_image_error_for_label(inline_validation, label)
    if inline_error is not None:
        raise ContentError(f"Error: {inline_error}")


def _load_common_metadata(
    package_folder: Path, manifest: dict[str, Any]
) -> tuple[str, str, str]:
    package_id = manifest.get("id")
    if not isinstance(package_id, str) or not PACKAGE_ID_RE.fullmatch(package_id):
        raise ContentError(f"Error: Invalid Package ID: {package_id}")
    if package_id != package_folder.name:
        raise ContentError("Error: Package ID does not match directory name.")

    name = manifest.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ContentError("Error: Package name is missing or empty.")

    created_at = manifest.get("created_at")
    if not isinstance(created_at, str):
        raise ContentError("Error: Package created_at is missing or invalid.")
    try:
        parsed_created_at = datetime.fromisoformat(created_at)
    except ValueError as exc:
        raise ContentError(
            f"Error: Package created_at is not valid ISO 8601: {created_at}"
        ) from exc
    if parsed_created_at.tzinfo is None or parsed_created_at.utcoffset() is None:
        raise ContentError(
            "Error: Package created_at must include timezone: " f"{created_at}"
        )
    return package_id, name, created_at


def _load_v02(
    package_folder: Path, manifest: dict[str, Any]
) -> dict[str, ContentVariant]:
    content = manifest.get("content")
    if not isinstance(content, dict) or not content:
        raise ContentError(
            "Error: content must contain at least one supported Content Variant."
        )
    unknown = sorted(set(content) - set(CONTENT_VARIANTS))
    if unknown:
        raise ContentError("Error: Unsupported Content Variant(s): " + ", ".join(unknown))
    unknown_manifest_fields = set(manifest) - {
        "schema_version", "id", "name", "created_at", "content"
    }
    if unknown_manifest_fields:
        raise ContentError(
            "Error: Unsupported manifest field(s): "
            + ", ".join(sorted(unknown_manifest_fields))
        )

    loaded: dict[str, ContentVariant] = {}
    for key in CONTENT_VARIANTS:
        if key not in content:
            continue
        loaded[key] = _load_content_variant(
            package_folder, key, content[key], strict_fields=True
        )
        _validate_long_markers(key, loaded[key])
    return loaded


def _load_v01(
    package_folder: Path, manifest: dict[str, Any]
) -> tuple[dict[str, ContentVariant], ContentVariant | None]:
    platforms = manifest.get("platforms")
    if not isinstance(platforms, dict) or not platforms:
        raise ContentError(
            "Error: platforms must contain at least one supported platform."
        )
    unknown = sorted(set(platforms) - set(PLATFORMS))
    if unknown:
        raise ContentError("Error: Unsupported platform(s) in manifest: " + ", ".join(unknown))

    loaded: dict[str, ContentVariant] = {}
    legacy_xiaohongshu: ContentVariant | None = None
    for platform in PLATFORMS:
        if platform not in platforms:
            continue
        content = _load_content_variant(
            package_folder,
            platform,
            platforms[platform],
            display_name=PLATFORM_NAMES[platform],
        )
        variant = LEGACY_PLATFORM_VARIANT_MAP[platform]
        if variant is None:
            legacy_xiaohongshu = content
        else:
            loaded[variant] = content
            if variant == "public_long":
                # Keep v0.1's established parser behavior for literal inline
                # marker-looking text while retaining duplicate/range checks.
                _validate_long_markers(variant, content, strict_syntax=False)
    return loaded, legacy_xiaohongshu


def load_post(post_folder: Path | str) -> PostContent:
    """Load an Alarkive Package v0.2 or a legacy v0.1 package read-only."""

    package_folder = Path(post_folder).expanduser()
    if not package_folder.exists() or not package_folder.is_dir():
        raise ContentError(f"Error: Package folder not found: {package_folder}")
    package_folder = package_folder.resolve()
    manifest = _load_manifest(package_folder)

    schema_version = manifest.get("schema_version")
    if schema_version not in {PACKAGE_SCHEMA_VERSION, LEGACY_PACKAGE_SCHEMA_VERSION}:
        raise ContentError(
            "Error: Unsupported Alarkive Package schema version: " f"{schema_version}"
        )
    package_id, name, created_at = _load_common_metadata(package_folder, manifest)

    legacy_xiaohongshu = None
    if schema_version == PACKAGE_SCHEMA_VERSION:
        loaded = _load_v02(package_folder, manifest)
    else:
        loaded, legacy_xiaohongshu = _load_v01(package_folder, manifest)

    return PostContent(
        folder=package_folder,
        id=package_id,
        name=name,
        created_at=created_at,
        public_long=loaded.get("public_long"),
        wechat_long=loaded.get("wechat_long"),
        wechat_short=loaded.get("wechat_short"),
        toutiao_short=loaded.get("toutiao_short"),
        xiaohongshu=legacy_xiaohongshu,
    )


__all__ = [
    "CONTENT_PLATFORM_MAP",
    "CONTENT_VARIANTS",
    "ContentError",
    "ContentVariant",
    "PACKAGE_SCHEMA_VERSION",
    "PLATFORMS",
    "PlatformContent",
    "PostContent",
    "load_post",
]
