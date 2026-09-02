from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .inline_images import inline_image_error, validate_inline_image_text


PACKAGE_SCHEMA_VERSION = "0.1"
PACKAGE_ID_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4}$")
PLATFORMS = ("xiaohongshu", "baijiahao", "wechat")
PLATFORM_NAMES = {
    "xiaohongshu": "Xiaohongshu",
    "baijiahao": "Baijiahao",
    "wechat": "WeChat",
}


class ContentError(Exception):
    """An expected, user-correctable Alarkive Package error."""


@dataclass(frozen=True)
class PlatformContent:
    title: str
    body: str
    images: tuple[Path, ...]


@dataclass(frozen=True)
class PostContent:
    folder: Path
    id: str
    name: str
    created_at: str
    xiaohongshu: PlatformContent | None
    baijiahao: PlatformContent | None
    wechat: PlatformContent | None

    def has_platform(self, platform: str) -> bool:
        """Return whether this Package contains content for ``platform``."""

        return platform in PLATFORMS and getattr(self, platform) is not None


def _load_manifest(package_folder: Path) -> dict[str, Any]:
    manifest_path = package_folder / "manifest.json"
    if not manifest_path.is_file():
        raise ContentError(
            "Error: manifest.json not found.\n"
            "This folder is not a valid Alarkive Package v0.1."
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
    platform_name: str,
    field_name: str,
) -> Path:
    if not isinstance(relative_path, str) or not relative_path:
        raise ContentError(
            f"Error: {platform_name} {field_name} is missing or invalid."
        )

    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ContentError(
            f"Error: {platform_name} {field_name} path is outside the package: "
            f"{relative_path}"
        )

    package_root = package_folder.resolve()
    resolved = (package_root / relative).resolve()
    try:
        resolved.relative_to(package_root)
    except ValueError as exc:
        raise ContentError(
            f"Error: {platform_name} {field_name} path is outside the package: "
            f"{relative_path}"
        ) from exc
    return resolved


def _read_body(
    package_folder: Path,
    content_file: str,
    platform_name: str,
) -> str:
    content_path = _resolve_package_resource(
        package_folder,
        content_file,
        platform_name=platform_name,
        field_name="content_file",
    )
    try:
        # newline="" preserves CRLF/LF and the exact Markdown supplied by the user.
        with content_path.open("r", encoding="utf-8", newline="") as file:
            body = file.read()
    except (OSError, UnicodeDecodeError) as exc:
        raise ContentError(
            f"Error: Could not read {platform_name} content file:\n"
            f"{content_file}\n{exc}"
        ) from exc
    if not body.strip():
        raise ContentError(
            f"Error: {platform_name} content file is empty:\n{content_file}"
        )
    return body


def _load_platform_content(
    package_folder: Path,
    platform: str,
    platform_data: Any,
) -> PlatformContent:
    platform_name = PLATFORM_NAMES[platform]
    if not isinstance(platform_data, dict):
        raise ContentError(
            f"Error: {platform_name} platform data is missing or invalid."
        )

    if "title" not in platform_data:
        raise ContentError(f"Error: {platform_name} title is missing.")
    title = platform_data["title"]
    if not isinstance(title, str) or not title.strip():
        raise ContentError(f"Error: {platform_name} title is empty or invalid.")
    title = title.strip()

    if "content_file" not in platform_data:
        raise ContentError(f"Error: {platform_name} content_file is missing.")
    content_file = platform_data["content_file"]
    body = _read_body(package_folder, content_file, platform_name)

    if "images" not in platform_data:
        raise ContentError(f"Error: {platform_name} images is missing.")
    image_references = platform_data["images"]
    if not isinstance(image_references, list) or not image_references:
        raise ContentError(
            f"Error: {platform_name} images must contain at least one image."
        )

    images: list[Path] = []
    for image_reference in image_references:
        image_path = _resolve_package_resource(
            package_folder,
            image_reference,
            platform_name=platform_name,
            field_name="images",
        )
        if (
            not isinstance(image_reference, str)
            or Path(image_reference).suffix.lower() != ".png"
        ):
            raise ContentError(
                f"Error: {platform_name} image must be a PNG file: {image_reference}"
            )
        if not image_path.is_file():
            raise ContentError(
                f"Error: Could not find {platform_name} image file:\n"
                f"{image_reference}"
            )
        images.append(image_path)

    return PlatformContent(title=title, body=body, images=tuple(images))


def load_post(post_folder: Path | str) -> PostContent:
    """Load one Alarkive Package v0.1 without modifying any package files."""

    package_folder = Path(post_folder).expanduser()
    if not package_folder.exists() or not package_folder.is_dir():
        raise ContentError(f"Error: Package folder not found: {package_folder}")
    package_folder = package_folder.resolve()
    manifest = _load_manifest(package_folder)

    schema_version = manifest.get("schema_version")
    if schema_version != PACKAGE_SCHEMA_VERSION:
        raise ContentError(
            "Error: Unsupported Alarkive Package schema version: "
            f"{schema_version}"
        )

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
            "Error: Package created_at must include timezone: "
            f"{created_at}"
        )

    platforms = manifest.get("platforms")
    if not isinstance(platforms, dict):
        raise ContentError("Error: platforms is missing from manifest.")
    if not platforms:
        raise ContentError("Error: platforms must contain at least one supported platform.")
    unknown_platforms = sorted(set(platforms) - set(PLATFORMS))
    if unknown_platforms:
        raise ContentError(
            "Error: Unsupported platform(s) in manifest: "
            + ", ".join(unknown_platforms)
        )

    platform_contents = {
        platform: _load_platform_content(
            package_folder,
            platform,
            platforms[platform],
        )
        for platform in PLATFORMS
        if platform in platforms
    }
    if "baijiahao" in platform_contents:
        _, inline_validation = validate_inline_image_text(
            platform_contents["baijiahao"].body,
            len(platform_contents["baijiahao"].images),
        )
        inline_error = inline_image_error(inline_validation)
        if inline_error is not None:
            raise ContentError(f"Error: {inline_error}")
    return PostContent(
        folder=package_folder,
        id=package_id,
        name=name,
        created_at=created_at,
        xiaohongshu=platform_contents.get("xiaohongshu"),
        baijiahao=platform_contents.get("baijiahao"),
        wechat=platform_contents.get("wechat"),
    )
