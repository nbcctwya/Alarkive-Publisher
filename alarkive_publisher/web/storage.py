from __future__ import annotations

import json
import logging
import re
import secrets
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from os import PathLike
from typing import Any, Mapping, Sequence


LOGGER = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
POSTS_DIR = PROJECT_ROOT / "posts"
PLATFORMS = ("xiaohongshu", "baijiahao", "wechat")
PLATFORM_LABELS = {
    "xiaohongshu": "小红书",
    "baijiahao": "百家号",
    "wechat": "微信公众号",
}
TASK_ID_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4}$")


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
    """Generate an ASCII-only task id in the v0.1 package format."""

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


def _normalise_image(image: ImageData | tuple[str, bytes] | PathLike[str] | str) -> ImageData:
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
) -> str:
    clean_name = name.strip()
    if not clean_name:
        raise StorageError("任务名称不能为空。")

    for platform in PLATFORMS:
        title = titles.get(platform)
        body = bodies.get(platform)
        if not isinstance(title, str) or not title.strip():
            raise StorageError(f"{PLATFORM_LABELS[platform]}标题不能为空。")
        if not isinstance(body, str) or not body.strip():
            raise StorageError(f"{PLATFORM_LABELS[platform]}正文不能为空。")
    return clean_name


def _validate_images(images: Sequence[ImageData]) -> None:
    if not images:
        raise StorageError("至少需要上传 1 张 PNG 图片。")
    for image in images:
        filename = Path(image.filename).name
        if not filename or Path(filename).suffix.lower() != ".png":
            raise StorageError("当前版本仅支持 PNG 图片。")
        if not image.data:
            raise StorageError(f"图片为空：{filename}")


def _safe_manifest_path(relative_path: str) -> Path:
    """Validate the package-relative paths written in a manifest."""

    path = Path(relative_path)
    if path.is_absolute() or ".." in path.parts or path.parts != tuple(relative_path.replace("\\", "/").split("/")):
        raise StorageError(f"manifest 包含不安全路径：{relative_path}")
    return path


def _validate_manifest(manifest: Any, expected_id: str | None = None) -> None:
    if not isinstance(manifest, dict):
        raise StorageError("manifest 不是 JSON 对象。")
    required = {"schema_version", "id", "name", "created_at", "platforms"}
    if not required.issubset(manifest):
        raise StorageError("manifest 缺少必要字段。")
    if manifest["schema_version"] != "0.1":
        raise StorageError("manifest schema_version 不是 0.1。")
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

    platforms = manifest["platforms"]
    if not isinstance(platforms, dict) or any(platform not in platforms for platform in PLATFORMS):
        raise StorageError("manifest platforms 不完整。")
    for platform in PLATFORMS:
        value = platforms[platform]
        if not isinstance(value, dict) or not isinstance(value.get("title"), str):
            raise StorageError(f"manifest 的 {platform} 平台数据无效。")
        content_file = value.get("content_file")
        images = value.get("images")
        if not isinstance(content_file, str) or not content_file.startswith("content/"):
            raise StorageError(f"manifest 的 {platform} 正文路径无效。")
        _safe_manifest_path(content_file)
        if not isinstance(images, list) or not images:
            raise StorageError(f"manifest 的 {platform} 图片列表无效。")
        for image in images:
            if not isinstance(image, str) or not image.startswith("images/"):
                raise StorageError(f"manifest 的 {platform} 图片路径无效。")
            _safe_manifest_path(image)


def save_post(
    name: str,
    titles: Mapping[str, str],
    bodies: Mapping[str, str],
    images: Sequence[ImageData | tuple[str, bytes] | PathLike[str] | str],
    *,
    posts_root: Path | str | None = None,
) -> SavedPost:
    """Save one complete v0.1 package atomically under ``posts_root``.

    The input image sequence is already in its final UI order. The function
    deliberately knows nothing about FastAPI or Playwright, which keeps the
    package format independently testable.
    """

    clean_name = _validate_required_fields(name, titles, bodies)
    normalised_images = [_normalise_image(image) for image in images]
    _validate_images(normalised_images)

    root = _posts_root(posts_root)
    created_at = datetime.now().astimezone().isoformat(timespec="seconds")

    for _ in range(10):
        task_id = generate_task_id()
        task_directory = root / task_id
        temporary_directory = Path(
            tempfile.mkdtemp(prefix=f".{task_id}-", dir=str(root))
        )
        try:
            content_directory = temporary_directory / "content"
            images_directory = temporary_directory / "images"
            content_directory.mkdir()
            images_directory.mkdir()

            content_files: dict[str, str] = {}
            image_references = [f"images/{index:02d}.png" for index in range(1, len(normalised_images) + 1)]
            for platform in PLATFORMS:
                content_file = f"content/{platform}.md"
                _write_utf8(content_directory / f"{platform}.md", bodies[platform])
                content_files[platform] = content_file

            for index, image in enumerate(normalised_images, start=1):
                (images_directory / f"{index:02d}.png").write_bytes(image.data)

            manifest: dict[str, Any] = {
                "schema_version": "0.1",
                "id": task_id,
                "name": clean_name,
                "created_at": created_at,
                "platforms": {
                    platform: {
                        "title": titles[platform],
                        "content_file": content_files[platform],
                        "images": image_references,
                    }
                    for platform in PLATFORMS
                },
            }
            _validate_manifest(manifest, expected_id=task_id)
            _write_utf8(
                temporary_directory / "manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            )

            # Directory rename is the commit point: callers only see a task
            # after manifest, markdown, and images have all been written.
            temporary_directory.rename(task_directory)
            return SavedPost(
                id=task_id,
                name=clean_name,
                created_at=created_at,
                directory=task_directory,
                manifest=manifest,
            )
        except FileExistsError:
            # A fantastically rare id collision must never overwrite a task.
            continue
        except OSError as exc:
            raise StorageError(f"保存图文失败：{exc}") from exc
        finally:
            if temporary_directory.exists():
                shutil.rmtree(temporary_directory, ignore_errors=True)

    raise StorageError("生成唯一任务 ID 失败，请稍后重试。")


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


def _summary(directory: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    first_images = manifest["platforms"][PLATFORMS[0]]["images"]
    return {
        "id": manifest["id"],
        "name": manifest["name"],
        "created_at": manifest["created_at"],
        "created_display": _created_display(manifest["created_at"]),
        "image_count": len(first_images),
        "platforms": [PLATFORM_LABELS[platform] for platform in PLATFORMS if platform in manifest["platforms"]],
        "manifest": manifest,
        "directory": directory,
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


def get_post_detail(post_id: str, posts_root: Path | str | None = None) -> dict[str, Any]:
    directory = _validated_task_directory(post_id, posts_root)
    manifest = _read_manifest(directory)
    platform_contents: list[dict[str, str]] = []
    for platform in PLATFORMS:
        platform_data = manifest["platforms"][platform]
        content_path = directory / _safe_manifest_path(platform_data["content_file"])
        try:
            body = content_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise StorageError(f"无法读取 {platform} Markdown 正文：{exc}") from exc
        platform_contents.append(
            {
                "key": platform,
                "label": PLATFORM_LABELS[platform],
                "title": platform_data["title"],
                "body": body,
            }
        )
    images = []
    for image_reference in manifest["platforms"][PLATFORMS[0]]["images"]:
        image_path = directory / _safe_manifest_path(image_reference)
        if not image_path.is_file():
            raise StorageError(f"任务图片缺失：{image_reference}")
        images.append({"reference": image_reference, "filename": image_path.name})
    return {
        "id": manifest["id"],
        "name": manifest["name"],
        "created_at": manifest["created_at"],
        "created_display": _created_display(manifest["created_at"]),
        "manifest": manifest,
        "platform_contents": platform_contents,
        "images": images,
        "directory": directory,
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
