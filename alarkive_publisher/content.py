from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


PNG_NAME_RE = re.compile(r"^(\d+)\.png$")


class ContentError(Exception):
    """An expected, user-correctable content-folder error."""


@dataclass(frozen=True)
class PlatformContent:
    title: str
    body: str


@dataclass(frozen=True)
class PostContent:
    folder: Path
    xiaohongshu: PlatformContent
    baijiahao: PlatformContent
    wechat: PlatformContent
    images: tuple[Path, ...]


def _load_platform_content(post_folder: Path, platform: str) -> PlatformContent:
    platform_dir = post_folder / platform
    platform_names = {
        "xiaohongshu": "Xiaohongshu",
        "baijiahao": "Baijiahao",
        "wechat": "WeChat",
    }
    platform_name = platform_names.get(platform, platform)
    if not platform_dir.is_dir():
        raise ContentError(f"Error: {platform_name} content directory not found.")

    txt_files = sorted(
        path
        for path in platform_dir.iterdir()
        if path.is_file() and path.suffix.lower() == ".txt"
    )
    if not txt_files:
        raise ContentError(f"Error: No {platform_name} .txt file found.")
    if len(txt_files) > 1:
        names = "\n".join(f"- {path.name}" for path in txt_files)
        raise ContentError(
            f"Error: Multiple {platform_name} .txt files found:\n{names}"
        )

    content_file = txt_files[0]
    try:
        body = content_file.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ContentError(
            f"Error: Could not read {platform_name} file "
            f"'{content_file.name}' as UTF-8: {exc}"
        ) from exc
    except OSError as exc:
        raise ContentError(
            f"Error: Could not read {platform_name} file "
            f"'{content_file.name}': {exc}"
        ) from exc

    return PlatformContent(title=content_file.stem, body=body)


def _load_images(post_folder: Path) -> tuple[Path, ...]:
    images_dir = post_folder / "images"
    if not images_dir.is_dir():
        raise ContentError("Error: Images directory not found.")

    numbered_images: list[tuple[int, Path]] = []
    for path in images_dir.iterdir():
        if not path.is_file():
            continue
        match = PNG_NAME_RE.fullmatch(path.name)
        if match:
            numbered_images.append((int(match.group(1)), path))

    if not numbered_images:
        raise ContentError("Error: No PNG images found.")

    numbered_images.sort(key=lambda item: (item[0], item[1].name))
    duplicate_numbers: dict[int, list[str]] = {}
    for number, path in numbered_images:
        duplicate_numbers.setdefault(number, []).append(path.name)
    duplicates = [names for names in duplicate_numbers.values() if len(names) > 1]
    if duplicates:
        details = "\n".join(f"- {', '.join(names)}" for names in duplicates)
        raise ContentError(
            f"Error: Duplicate numeric image order found:\n{details}"
        )

    return tuple(path for _, path in numbered_images)


def load_post(post_folder: Path) -> PostContent:
    if not post_folder.exists():
        raise ContentError(f"Error: Post folder not found: {post_folder}")
    if not post_folder.is_dir():
        raise ContentError(f"Error: Post folder is not a directory: {post_folder}")

    xiaohongshu = _load_platform_content(post_folder, "xiaohongshu")
    baijiahao = _load_platform_content(post_folder, "baijiahao")
    wechat = _load_platform_content(post_folder, "wechat")
    return PostContent(
        folder=post_folder,
        xiaohongshu=xiaohongshu,
        baijiahao=baijiahao,
        wechat=wechat,
        images=_load_images(post_folder),
    )
