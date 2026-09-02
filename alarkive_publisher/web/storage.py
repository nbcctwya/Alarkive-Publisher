from __future__ import annotations

import json
import logging
import re
import secrets
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from os import PathLike
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..content import CONTENT_VARIANTS, ContentError, load_post
from ..routing import (
    CONTENT_VARIANT_LABELS,
    PUBLISH_TARGETS,
    PUBLISHER_REGISTRY,
    LEGACY_PLATFORM_VARIANT_MAP,
)
from .publish_state import PublishStateError, default_publish_state, load_publish_state


LOGGER = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
POSTS_DIR = PROJECT_ROOT / "posts"
LEGACY_PLATFORMS = ("xiaohongshu", "baijiahao", "wechat")
PLATFORMS = CONTENT_VARIANTS
PLATFORM_LABELS = {
    **CONTENT_VARIANT_LABELS,
    "xiaohongshu": "小红书",
    "baijiahao": "百家号",
    "wechat": "微信公众号",
}
TASK_ID_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4}$")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_IMAGE_SIZE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_IMAGE_SIZE_BYTES = 100 * 1024 * 1024
MAX_IMAGE_COUNT = 20


class StorageError(ValueError):
    """An expected content-package validation or storage error."""


@dataclass(frozen=True)
class ImageData:
    """An image in the order selected by the user."""

    filename: str
    data: bytes


@dataclass(frozen=True)
class SavedPost:
    id: str
    name: str
    created_at: str
    directory: Path
    manifest: dict[str, Any]


def generate_task_id(now: datetime | None = None) -> str:
    current = now.astimezone() if now is not None else datetime.now().astimezone()
    return f"{current:%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"


def _posts_root(posts_root: Path | str | None) -> Path:
    root = Path(posts_root) if posts_root is not None else POSTS_DIR
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _write_utf8(path: Path, text: str) -> None:
    # newline="" keeps the exact line endings supplied by the textarea.
    with path.open("w", encoding="utf-8", newline="") as file:
        file.write(text)


def _normalise_image(
    image: ImageData | tuple[str, bytes] | PathLike[str] | str,
) -> ImageData:
    if isinstance(image, ImageData):
        return image
    if isinstance(image, tuple) and len(image) == 2:
        filename, data = image
        return ImageData(filename=str(filename), data=bytes(data))
    image_path = Path(image)
    try:
        return ImageData(filename=image_path.name, data=image_path.read_bytes())
    except OSError as exc:
        raise StorageError(f"无法读取图片：{image_path}") from exc


def _validate_required_fields(
    name: str,
    titles: Mapping[str, str],
    bodies: Mapping[str, str],
    *,
    keys: Sequence[str] = CONTENT_VARIANTS,
    labels: Mapping[str, str] = CONTENT_VARIANT_LABELS,
) -> tuple[str, dict[str, str], tuple[str, ...]]:
    clean_name = name.strip()
    if not clean_name:
        raise StorageError("任务名称不能为空。")
    unknown_keys = (set(titles) | set(bodies)) - set(keys)
    if unknown_keys:
        raise StorageError(
            "包含不支持的内容模块：" + "、".join(sorted(unknown_keys))
        )

    clean_titles: dict[str, str] = {}
    active_keys: list[str] = []
    for key in keys:
        title = titles.get(key, "")
        body = bodies.get(key, "")
        title_filled = isinstance(title, str) and bool(title.strip())
        body_filled = isinstance(body, str) and bool(body.strip())
        if title_filled != body_filled:
            raise StorageError(
                f"{labels[key]}的标题和正文需要同时填写，或者同时留空。"
            )
        if title_filled:
            clean_titles[key] = title.strip()
            active_keys.append(key)

    if not active_keys:
        raise StorageError("至少需要填写一个平台的标题和正文（至少一个内容模块完整）。")
    return clean_name, clean_titles, tuple(active_keys)


def _validate_images(images: Sequence[ImageData]) -> None:
    if not images:
        raise StorageError("至少需要上传 1 张 PNG 图片。")
    if len(images) > MAX_IMAGE_COUNT:
        raise StorageError(f"图片数量超过限制，单个任务最多上传 {MAX_IMAGE_COUNT} 张图片。")

    total_size = 0
    for image in images:
        filename = Path(image.filename).name
        if not filename or Path(filename).suffix.lower() != ".png":
            raise StorageError("当前版本仅支持 PNG 图片。")
        if not image.data:
            raise StorageError(f"图片为空：{filename}")
        if not image.data.startswith(PNG_SIGNATURE):
            raise StorageError(f"图片不是有效的 PNG 文件：{filename}")
        if len(image.data) > MAX_IMAGE_SIZE_BYTES:
            raise StorageError(
                f"图片过大：{filename}。单张图片不能超过 {MAX_IMAGE_SIZE_BYTES // (1024 * 1024)} MB。"
            )
        total_size += len(image.data)

    if total_size > MAX_TOTAL_IMAGE_SIZE_BYTES:
        raise StorageError(
            "图片总大小超过限制：单个任务的图片总大小不能超过 "
            f"{MAX_TOTAL_IMAGE_SIZE_BYTES // (1024 * 1024)} MB。"
        )


def _safe_manifest_path(relative_path: str) -> Path:
    """Validate package-relative paths found in a manifest."""

    if not isinstance(relative_path, str) or not relative_path:
        raise StorageError("manifest 包含无效路径。")
    path = Path(relative_path)
    normalised_parts = tuple(relative_path.replace("\\", "/").split("/"))
    if (
        path.is_absolute()
        or ".." in path.parts
        or path.parts != normalised_parts
        or any(not part for part in normalised_parts)
    ):
        raise StorageError(f"manifest 包含不安全路径：{relative_path}")
    return path


def _validate_metadata(manifest: dict[str, Any], expected_id: str | None) -> None:
    required = {"schema_version", "id", "name", "created_at"}
    if not required.issubset(manifest):
        raise StorageError("manifest 缺少必要字段。")
    if manifest["schema_version"] not in {"0.1", "0.2"}:
        raise StorageError(
            "manifest schema_version 不受支持：" f"{manifest['schema_version']}。"
        )
    task_id = manifest["id"]
    if not isinstance(task_id, str) or not TASK_ID_RE.fullmatch(task_id):
        raise StorageError("manifest id 格式无效。")
    if expected_id is not None and task_id != expected_id:
        raise StorageError("manifest id 与任务目录不一致。")
    if not isinstance(manifest["name"], str) or not manifest["name"].strip():
        raise StorageError("manifest name 无效。")
    if not isinstance(manifest["created_at"], str):
        raise StorageError("manifest created_at 无效。")
    try:
        created_at = datetime.fromisoformat(manifest["created_at"])
    except ValueError as exc:
        raise StorageError("manifest created_at 不是有效的 ISO 8601 时间。") from exc
    if created_at.tzinfo is None or created_at.utcoffset() is None:
        raise StorageError("manifest created_at 必须包含时区。")


def _validate_content_entries(
    entries: Any,
    *,
    container_name: str,
    allowed_keys: Sequence[str],
) -> None:
    if not isinstance(entries, dict) or not entries:
        raise StorageError(f"manifest {container_name} 至少需要包含一个内容。")
    unknown = sorted(set(entries) - set(allowed_keys))
    if unknown:
        raise StorageError(
            f"manifest {container_name} 包含不支持的内容：" + "、".join(unknown)
        )
    for key in allowed_keys:
        if key not in entries:
            continue
        value = entries[key]
        unknown_fields = set(value) - {"title", "content_file", "images"} if isinstance(value, dict) else set()
        if unknown_fields:
            raise StorageError(
                f"manifest 的 {key} 包含不支持字段：" + "、".join(sorted(unknown_fields))
            )
        if (
            not isinstance(value, dict)
            or not isinstance(value.get("title"), str)
            or not value["title"].strip()
        ):
            raise StorageError(f"manifest 的 {key} 内容数据无效。")
        value["title"] = value["title"].strip()
        content_file = value.get("content_file")
        images = value.get("images")
        if not isinstance(content_file, str) or not content_file.startswith("content/"):
            raise StorageError(f"manifest 的 {key} 正文路径无效。")
        _safe_manifest_path(content_file)
        if not isinstance(images, list) or not images:
            raise StorageError(f"manifest 的 {key} 图片列表无效。")
        for image in images:
            if not isinstance(image, str) or not image.startswith("images/"):
                raise StorageError(f"manifest 的 {key} 图片路径无效。")
            _safe_manifest_path(image)


def _validate_manifest(manifest: Any, expected_id: str | None = None) -> None:
    """Validate either the current v0.2 or legacy v0.1 manifest."""

    if not isinstance(manifest, dict):
        raise StorageError("manifest 不是 JSON 对象。")
    _validate_metadata(manifest, expected_id)
    if manifest["schema_version"] == "0.2":
        unknown_root_fields = set(manifest) - {
            "schema_version", "id", "name", "created_at", "content"
        }
        if unknown_root_fields:
            raise StorageError(
                "manifest 包含不支持字段：" + "、".join(sorted(unknown_root_fields))
            )
        if "content" not in manifest:
            raise StorageError("manifest 缺少 content 字段。")
        _validate_content_entries(
            manifest["content"],
            container_name="content",
            allowed_keys=CONTENT_VARIANTS,
        )
    else:
        if "platforms" not in manifest:
            raise StorageError("manifest 缺少 platforms 字段。")
        _validate_content_entries(
            manifest["platforms"],
            container_name="platforms",
            allowed_keys=LEGACY_PLATFORMS,
        )


def _legacy_input(titles: Mapping[str, str], bodies: Mapping[str, str]) -> bool:
    keys = set(titles) | set(bodies)
    return bool(keys) and keys <= set(LEGACY_PLATFORMS) and not keys.intersection(CONTENT_VARIANTS)


def _write_package(
    *,
    root: Path,
    clean_name: str,
    clean_titles: Mapping[str, str],
    active_keys: Sequence[str],
    bodies: Mapping[str, str],
    images: Sequence[ImageData],
    schema_version: str,
) -> SavedPost:
    created_at = datetime.now().astimezone().isoformat(timespec="seconds")
    container_key = "platforms" if schema_version == "0.1" else "content"

    for _ in range(10):
        task_id = generate_task_id()
        task_directory = root / task_id
        temporary_directory = Path(tempfile.mkdtemp(prefix=f".{task_id}-", dir=str(root)))
        try:
            content_directory = temporary_directory / "content"
            images_directory = temporary_directory / "images"
            content_directory.mkdir()
            images_directory.mkdir()

            image_references = [
                f"images/{index:02d}.png" for index in range(1, len(images) + 1)
            ]
            entries: dict[str, Any] = {}
            for key in active_keys:
                content_file = f"content/{key}.md"
                _write_utf8(content_directory / f"{key}.md", bodies[key])
                entries[key] = {
                    "title": clean_titles[key],
                    "content_file": content_file,
                    "images": image_references,
                }

            for index, image in enumerate(images, start=1):
                (images_directory / f"{index:02d}.png").write_bytes(image.data)

            manifest: dict[str, Any] = {
                "schema_version": schema_version,
                "id": task_id,
                "name": clean_name,
                "created_at": created_at,
                container_key: entries,
            }
            _validate_manifest(manifest, expected_id=task_id)
            _write_utf8(
                temporary_directory / "manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            )
            temporary_directory.rename(task_directory)
            return SavedPost(task_id, clean_name, created_at, task_directory, manifest)
        except FileExistsError:
            continue
        except OSError as exc:
            raise StorageError(f"保存图文失败：{exc}") from exc
        finally:
            if temporary_directory.exists():
                shutil.rmtree(temporary_directory, ignore_errors=True)

    raise StorageError("生成唯一任务 ID 失败，请稍后重试。")


def save_post(
    name: str,
    titles: Mapping[str, str] | None = None,
    bodies: Mapping[str, str] | None = None,
    images: Sequence[ImageData | tuple[str, bytes] | PathLike[str] | str] = (),
    *,
    posts_root: Path | str | None = None,
    public_long_title: str = "",
    public_long_body: str = "",
    wechat_long_title: str = "",
    wechat_long_body: str = "",
    wechat_short_title: str = "",
    wechat_short_body: str = "",
    toutiao_short_title: str = "",
    toutiao_short_body: str = "",
) -> SavedPost:
    """Write a v0.2 package from Content Variant title/body mappings.

    The old three-platform mapping is still accepted as a small compatibility
    writer for integrations that have not migrated. The Web Manager always
    supplies variant keys and therefore always writes v0.2.
    """

    explicit_titles = {
        "public_long": public_long_title,
        "wechat_long": wechat_long_title,
        "wechat_short": wechat_short_title,
        "toutiao_short": toutiao_short_title,
    }
    explicit_bodies = {
        "public_long": public_long_body,
        "wechat_long": wechat_long_body,
        "wechat_short": wechat_short_body,
        "toutiao_short": toutiao_short_body,
    }
    if titles is None:
        titles = explicit_titles
    elif any(value for value in explicit_titles.values()):
        titles = {**titles, **explicit_titles}
    if bodies is None:
        bodies = explicit_bodies
    elif any(value for value in explicit_bodies.values()):
        bodies = {**bodies, **explicit_bodies}

    legacy = _legacy_input(titles, bodies)
    if legacy:
        clean_name, clean_titles, active_keys = _validate_required_fields(
            name,
            titles,
            bodies,
            keys=LEGACY_PLATFORMS,
            labels=PLATFORM_LABELS,
        )
    else:
        clean_name, clean_titles, active_keys = _validate_required_fields(
            name, titles, bodies
        )

    normalised_images = [_normalise_image(image) for image in images]
    _validate_images(normalised_images)
    return _write_package(
        root=_posts_root(posts_root),
        clean_name=clean_name,
        clean_titles=clean_titles,
        active_keys=active_keys,
        bodies=bodies,
        images=normalised_images,
        schema_version="0.1" if legacy else "0.2",
    )


def _read_manifest(directory: Path) -> dict[str, Any]:
    manifest_path = directory / "manifest.json"
    try:
        with manifest_path.open("r", encoding="utf-8") as file:
            manifest = json.load(file)
    except FileNotFoundError as exc:
        raise StorageError("manifest.json 缺失。") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StorageError(f"manifest.json 无法读取或解析：{exc}") from exc
    _validate_manifest(manifest, expected_id=directory.name)
    return manifest


def _created_datetime(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.min


def _created_display(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return value
    return parsed.strftime("%Y-%m-%d %H:%M")


def _manifest_entries(manifest: dict[str, Any]) -> dict[str, Any]:
    if manifest["schema_version"] == "0.2":
        return manifest["content"]
    return manifest["platforms"]


def _variant_key_for_legacy(key: str) -> str | None:
    return LEGACY_PLATFORM_VARIANT_MAP.get(key)


def _summary(directory: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    entries = _manifest_entries(manifest)
    first_key = next(iter(entries))
    first_images = entries[first_key]["images"]
    if manifest["schema_version"] == "0.2":
        variant_keys = list(entries)
    else:
        variant_keys = [
            variant
            for platform in LEGACY_PLATFORMS
            if (variant := _variant_key_for_legacy(platform)) is not None
            and platform in entries
        ]
    try:
        publish_state = load_publish_state(directory)
    except PublishStateError as exc:
        LOGGER.warning("任务 %s 的发布状态损坏，按默认状态显示：%s", directory, exc)
        publish_state = default_publish_state()
    labels = [CONTENT_VARIANT_LABELS[key] for key in variant_keys]
    return {
        "id": manifest["id"],
        "name": manifest["name"],
        "created_at": manifest["created_at"],
        "created_display": _created_display(manifest["created_at"]),
        "image_count": len(first_images),
        "content_variants": labels,
        # Keep the old template/API key as a compatibility alias. Its values
        # now describe active variants rather than old platform assumptions.
        "platforms": labels,
        "manifest": manifest,
        "directory": directory,
        "published": publish_state["published"],
        "published_at": publish_state["published_at"],
    }


def list_post_summaries(posts_root: Path | str | None = None) -> list[dict[str, Any]]:
    """Read valid task manifests, skipping malformed tasks with a warning."""

    root = _posts_root(posts_root)
    summaries: list[dict[str, Any]] = []
    for directory in root.iterdir():
        if not directory.is_dir() or directory.name.startswith("."):
            continue
        try:
            manifest = _read_manifest(directory)
        except StorageError as exc:
            LOGGER.warning("跳过损坏任务 %s：%s", directory, exc)
            continue
        summaries.append(_summary(directory, manifest))
    summaries.sort(key=lambda item: _created_datetime(item["created_at"]), reverse=True)
    return summaries


def _validated_task_directory(post_id: str, posts_root: Path | str | None = None) -> Path:
    if not TASK_ID_RE.fullmatch(post_id):
        raise StorageError("任务 ID 格式无效。")
    root = _posts_root(posts_root)
    directory = (root / post_id).resolve()
    try:
        directory.relative_to(root)
    except ValueError as exc:
        raise StorageError("任务路径无效。") from exc
    if not directory.is_dir():
        raise StorageError("任务不存在。")
    return directory


def get_post_folder(post_id: str, posts_root: Path | str | None = None) -> Path:
    return _validated_task_directory(post_id, posts_root)


def _read_detail_content(
    directory: Path,
    manifest: dict[str, Any],
    key: str,
    *,
    source_key: str | None = None,
) -> dict[str, str]:
    entries = _manifest_entries(manifest)
    data = entries[source_key or key]
    content_path = directory / _safe_manifest_path(data["content_file"])
    try:
        with content_path.open("r", encoding="utf-8", newline="") as file:
            body = file.read()
    except (OSError, UnicodeDecodeError) as exc:
        raise StorageError(f"无法读取 {key} Markdown 正文：{exc}") from exc
    return {
        "key": key,
        "variant": key,
        "label": CONTENT_VARIANT_LABELS.get(key, PLATFORM_LABELS.get(key, key)),
        "title": data["title"],
        "body": body,
    }


def _publish_target_details(post) -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = []
    for target in PUBLISH_TARGETS:
        spec = PUBLISHER_REGISTRY[target]
        has_content = post.has_content(spec.variant)
        targets.append(
            {
                "target": target,
                "variant": spec.variant,
                "label": spec.label,
                "implemented": spec.implemented,
                "has_content": has_content,
                "status": (
                    "available"
                    if has_content and spec.implemented
                    else "not_implemented"
                    if has_content
                    else "no_content"
                ),
            }
        )
    return targets


def get_post_detail(post_id: str, posts_root: Path | str | None = None) -> dict[str, Any]:
    directory = _validated_task_directory(post_id, posts_root)
    manifest = _read_manifest(directory)
    try:
        post = load_post(directory)
    except ContentError as exc:
        raise StorageError(str(exc)) from exc

    variant_contents = []
    for key in CONTENT_VARIANTS:
        if post.has_content(key):
            source_key = key
            if manifest["schema_version"] == "0.1":
                source_key = next(
                    platform
                    for platform, variant in LEGACY_PLATFORM_VARIANT_MAP.items()
                    if variant == key and platform in manifest["platforms"]
                )
            variant_contents.append(
                _read_detail_content(
                    directory,
                    manifest,
                    key,
                    source_key=source_key,
                )
            )

    # v0.1 UI/API compatibility: expose old platform cards only for legacy
    # manifests. New Web templates use variant_contents exclusively.
    if manifest["schema_version"] == "0.1":
        legacy_contents = []
        for platform in LEGACY_PLATFORMS:
            if platform in manifest["platforms"]:
                legacy_contents.append(_read_detail_content(directory, manifest, platform))
    else:
        legacy_contents = variant_contents

    first_content = next(
        (getattr(post, key) for key in CONTENT_VARIANTS if getattr(post, key) is not None),
        post.xiaohongshu,
    )
    if first_content is None:
        raise StorageError("任务没有可展示的内容。")
    images = []
    for image_path in first_content.images:
        image_reference = f"images/{image_path.name}"
        if not image_path.is_file():
            raise StorageError(f"任务图片缺失：{image_reference}")
        images.append({"reference": image_reference, "filename": image_path.name})

    targets = _publish_target_details(post)
    available_targets = [target["target"] for target in targets if target["status"] == "available"]
    return {
        "id": manifest["id"],
        "name": manifest["name"],
        "created_at": manifest["created_at"],
        "created_display": _created_display(manifest["created_at"]),
        "manifest": manifest,
        "variant_contents": variant_contents,
        "content_variants": variant_contents,
        "platform_contents": legacy_contents,
        "images": images,
        "directory": directory,
        "publish_targets": targets,
        "full_workflow_targets": available_targets,
        "has_available_publisher": bool(available_targets),
        "publish_state": load_publish_state(directory),
    }


def get_image_path(
    post_id: str,
    image_name: str,
    posts_root: Path | str | None = None,
) -> Path:
    directory = _validated_task_directory(post_id, posts_root)
    if not re.fullmatch(r"\d{2,}\.png", image_name):
        raise StorageError("图片路径无效。")
    image_path = (directory / "images" / image_name).resolve()
    images_directory = (directory / "images").resolve()
    try:
        image_path.relative_to(images_directory)
    except ValueError as exc:
        raise StorageError("图片路径无效。") from exc
    if not image_path.is_file():
        raise StorageError("图片不存在。")
    return image_path


__all__ = [
    "CONTENT_VARIANTS",
    "ImageData",
    "MAX_IMAGE_COUNT",
    "MAX_IMAGE_SIZE_BYTES",
    "MAX_TOTAL_IMAGE_SIZE_BYTES",
    "PNG_SIGNATURE",
    "PLATFORMS",
    "SavedPost",
    "StorageError",
    "get_image_path",
    "get_post_detail",
    "get_post_folder",
    "list_post_summaries",
    "save_post",
]
