"""Parsing and validation for Alarkive's Baijiahao image markers.

The marker protocol is deliberately small and is only used by the Baijiahao
publisher. It is not part of Markdown and it does not change the Package
schema; the marker lives in the existing Markdown content file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TypeAlias


IMAGE_MARKER_LINE_RE = re.compile(
    r"^[ \t]*\[\[image:(\d+)\]\][ \t]*(?:\r?\n|$)",
    re.MULTILINE,
)


class InlineImageError(ValueError):
    """An invalid or unsafe Baijiahao inline-image marker sequence."""


@dataclass(frozen=True)
class TextBlock:
    text: str


@dataclass(frozen=True)
class ImageBlock:
    index: int


ContentBlock: TypeAlias = TextBlock | ImageBlock


@dataclass(frozen=True)
class InlineImageValidation:
    """Validation details used by both the UI and Publisher layers."""

    image_count: int
    used_images: tuple[int, ...]
    duplicate_images: tuple[int, ...]
    invalid_images: tuple[int, ...]
    unused_images: tuple[int, ...]

    @property
    def has_markers(self) -> bool:
        return bool(self.used_images)

    @property
    def is_valid(self) -> bool:
        return not self.duplicate_images and not self.invalid_images


def parse_inline_images(text: str) -> tuple[ContentBlock, ...]:
    """Split strict, line-isolated ``[[image:N]]`` markers from text.

    Marker indentation and trailing spaces are ignored. The line ending that
    follows a marker is consumed so it cannot become visible marker text, but
    all text outside marker lines is retained exactly as supplied.
    """

    if not isinstance(text, str):
        raise TypeError("Baijiahao content must be a string.")

    blocks: list[ContentBlock] = []
    cursor = 0
    for match in IMAGE_MARKER_LINE_RE.finditer(text):
        if match.start() > cursor:
            blocks.append(TextBlock(text[cursor : match.start()]))
        blocks.append(ImageBlock(index=int(match.group(1))))
        cursor = match.end()
    if cursor < len(text):
        blocks.append(TextBlock(text[cursor:]))
    return tuple(blocks)


def validate_inline_images(
    blocks: tuple[ContentBlock, ...] | list[ContentBlock],
    image_count: int,
) -> InlineImageValidation:
    """Return duplicate, out-of-range, and unused image indexes."""

    if image_count < 0:
        raise ValueError("image_count must not be negative.")

    used: list[int] = []
    duplicates: list[int] = []
    invalid: list[int] = []
    for block in blocks:
        if not isinstance(block, ImageBlock):
            continue
        if block.index < 1 or block.index > image_count:
            if block.index not in invalid:
                invalid.append(block.index)
        if block.index in used:
            if block.index not in duplicates:
                duplicates.append(block.index)
        else:
            used.append(block.index)

    unused = tuple(index for index in range(1, image_count + 1) if index not in used)
    return InlineImageValidation(
        image_count=image_count,
        used_images=tuple(used),
        duplicate_images=tuple(duplicates),
        invalid_images=tuple(invalid),
        unused_images=unused,
    )


def validate_inline_image_text(
    text: str,
    image_count: int,
) -> tuple[tuple[ContentBlock, ...], InlineImageValidation]:
    """Parse and validate one Baijiahao body in a single call."""

    blocks = parse_inline_images(text)
    return blocks, validate_inline_images(blocks, image_count)


def append_unused_images(
    blocks: tuple[ContentBlock, ...] | list[ContentBlock],
    validation: InlineImageValidation,
) -> tuple[ContentBlock, ...]:
    """Append unreferenced images in Package order after marker content."""

    if not validation.has_markers or not validation.unused_images:
        return tuple(blocks)
    return tuple((*blocks, *(ImageBlock(index) for index in validation.unused_images)))


def inline_image_error(validation: InlineImageValidation) -> InlineImageError | None:
    """Build the stable Publisher-facing error for invalid marker input."""

    if validation.duplicate_images:
        index = validation.duplicate_images[0]
        return InlineImageError(
            "Baijiahao content contains duplicate image marker: "
            f"[[image:{index}]]"
        )
    if validation.invalid_images:
        index = validation.invalid_images[0]
        return InlineImageError(
            "Baijiahao content contains invalid image marker: "
            f"[[image:{index}]]. Only {validation.image_count} "
            "images are available."
        )
    return None


__all__ = [
    "ContentBlock",
    "ImageBlock",
    "InlineImageError",
    "InlineImageValidation",
    "TextBlock",
    "append_unused_images",
    "inline_image_error",
    "parse_inline_images",
    "validate_inline_image_text",
    "validate_inline_images",
]
