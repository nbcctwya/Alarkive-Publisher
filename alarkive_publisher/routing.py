from __future__ import annotations

from dataclasses import dataclass


# A Content Variant is the thing stored in an Alarkive Package.  Platform
# targets are consumers of a variant and are deliberately kept out of the
# manifest so adding a publisher does not change the package format.
CONTENT_VARIANTS = (
    "public_long",
    "wechat_long",
    "wechat_short",
    "toutiao_short",
)

CONTENT_VARIANT_LABELS = {
    "public_long": "公域长文（百家号 + 今日头条）",
    "wechat_long": "微信长文",
    "wechat_short": "微信图文 / 小绿书",
    "toutiao_short": "微头条",
}

CONTENT_PLATFORM_MAP = {
    "public_long": ("baijiahao", "toutiao_article"),
    "wechat_long": ("wechat_article",),
    "wechat_short": ("wechat_image",),
    "toutiao_short": ("toutiao_micro",),
}


@dataclass(frozen=True)
class PublisherSpec:
    target: str
    variant: str
    label: str
    implemented: bool
    runner: str | None = None


# The registry describes every target known to the v0.2 routing layer.  Only
# targets with a runner are executable in this release.
PUBLISHER_REGISTRY = {
    "baijiahao": PublisherSpec(
        "baijiahao", "public_long", "百家号", True, "baijiahao"
    ),
    "toutiao_article": PublisherSpec(
        "toutiao_article", "public_long", "今日头条文章", True, "toutiao_article"
    ),
    "wechat_article": PublisherSpec(
        "wechat_article", "wechat_long", "微信公众号长文", True, "wechat_article"
    ),
    "wechat_image": PublisherSpec(
        "wechat_image", "wechat_short", "微信图文", True, "wechat_image"
    ),
    "toutiao_micro": PublisherSpec(
        "toutiao_micro", "toutiao_short", "微头条", True, "toutiao_micro"
    ),
}

PUBLISH_TARGETS = tuple(PUBLISHER_REGISTRY)
AVAILABLE_PUBLISHERS = frozenset(
    target for target, spec in PUBLISHER_REGISTRY.items() if spec.implemented
)
WORKFLOW_TARGETS = ("baijiahao", "toutiao_article", "wechat_article", "wechat_image", "toutiao_micro")

# v0.1 callers and sidecars used ``wechat`` for the current WeChat image
# publisher.  Keep it as an input alias while making the canonical target
# explicit everywhere new.
LEGACY_TARGET_ALIASES = {"wechat": "wechat_image"}
LEGACY_PLATFORM_VARIANT_MAP = {
    "baijiahao": "public_long",
    "wechat": "wechat_short",
    "xiaohongshu": None,
}


def normalize_target(target: str) -> str:
    return LEGACY_TARGET_ALIASES.get(target, target)


def target_spec(target: str) -> PublisherSpec | None:
    return PUBLISHER_REGISTRY.get(normalize_target(target))


def variant_targets(variant: str) -> tuple[str, ...]:
    return CONTENT_PLATFORM_MAP.get(variant, ())


__all__ = [
    "AVAILABLE_PUBLISHERS",
    "CONTENT_PLATFORM_MAP",
    "CONTENT_VARIANTS",
    "CONTENT_VARIANT_LABELS",
    "LEGACY_PLATFORM_VARIANT_MAP",
    "LEGACY_TARGET_ALIASES",
    "PUBLISHER_REGISTRY",
    "PUBLISH_TARGETS",
    "PublisherSpec",
    "WORKFLOW_TARGETS",
    "normalize_target",
    "target_spec",
    "variant_targets",
]
