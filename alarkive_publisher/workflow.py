from __future__ import annotations

from pathlib import Path
from typing import Callable

from .baijiahao import run_baijiahao
from .content import PostContent
from .routing import (
    AVAILABLE_PUBLISHERS,
    PUBLISHER_REGISTRY,
    PUBLISH_TARGETS,
    WORKFLOW_TARGETS,
    normalize_target,
)
from .wechat import run_wechat
from .workflow_controller import WorkflowController
from .xiaohongshu import PublisherError, run_xiaohongshu, start_browser


BrowserStarted = Callable[[object, object, object], None]
FULL_WORKFLOW_TARGETS = WORKFLOW_TARGETS
# xiaohongshu remains a legacy direct-run target for old integrations. It is
# deliberately absent from the v0.2 registry and from all Web UI choices.
SINGLE_PLATFORM_TARGETS = PUBLISH_TARGETS + ("xiaohongshu",)


def _target_has_content(post: PostContent, target: str) -> bool:
    if target == "xiaohongshu":
        return post.xiaohongshu is not None
    spec = PUBLISHER_REGISTRY.get(normalize_target(target))
    return spec is not None and post.has_content(spec.variant)


def _available_workflow_targets(post: PostContent) -> list[str]:
    return [
        target
        for target in FULL_WORKFLOW_TARGETS
        if target in AVAILABLE_PUBLISHERS and _target_has_content(post, target)
    ]


def run_publisher_workflow(
    post: PostContent,
    project_root: Path,
    controller: WorkflowController,
    *,
    on_browser_started: BrowserStarted | None = None,
) -> None:
    """Run all present targets with an implemented Publisher.

    Routing may describe targets that have no runner yet. Those targets are
    intentionally ignored by this executable workflow.
    """

    active_targets = _available_workflow_targets(post)
    if not active_targets:
        raise ValueError(
            "当前任务包含内容，但没有已接入的可发布平台。"
            "没有可用于完整发布流程的平台内容。"
        )

    playwright = None
    context = None
    page = None
    current_target: str | None = None
    current_step = "Starting browser"

    try:
        controller.system_step("starting_browser", "正在启动共享浏览器")
        playwright, context, page = start_browser(project_root)
        if on_browser_started is not None:
            on_browser_started(playwright, context, page)

        for index, target in enumerate(active_targets):
            current_target = target
            current_step = f"Preparing {target}"
            controller.start_platform(target)
            if target == "baijiahao":
                run_baijiahao(page, post, controller)
                ready_message = "百家号已准备完成"
            elif target == "wechat_image":
                page = run_wechat(page, post, controller)
                ready_message = "微信图文已准备完成"
            else:  # Defensive: registry filtering should make this unreachable.
                raise ValueError(f"该平台 Publisher 尚未接入：{target}")
            is_last = index == len(active_targets) - 1
            prompt = (
                "检查完成后点击结束流程并关闭浏览器。"
                if is_last
                else "检查完成后点击继续到下一个发布平台。"
            )
            controller.ready(target, ready_message, prompt)

        current_step = "Closing browser"
        if context is not None:
            context.close()
        if playwright is not None:
            playwright.stop()
        prepared_names = "、".join(
            PUBLISHER_REGISTRY[target].label for target in active_targets
        )
        controller.completed(
            f"发布准备流程完成。{prepared_names}已停在最终发布按钮之前。"
        )
    except BaseException as exc:
        error_step = getattr(exc, "step", current_step)
        try:
            controller.failed(current_target, error_step, exc)
        except Exception:
            pass
        raise


def run_single_platform_workflow(
    post: PostContent,
    project_root: Path,
    platform: str,
    controller: WorkflowController,
    *,
    on_browser_started: BrowserStarted | None = None,
) -> None:
    """Prepare one implemented target in a shared-browser session."""

    if platform == "xiaohongshu":
        if not _target_has_content(post, platform):
            raise ValueError("当前 Package 不包含小红书内容。")
        target = platform
    else:
        target = normalize_target(platform)
        spec = PUBLISHER_REGISTRY.get(target)
        if spec is None:
            raise ValueError(
                "Unsupported single-platform workflow target: " f"{platform!r}."
            )
        if target not in AVAILABLE_PUBLISHERS:
            raise ValueError("该平台 Publisher 尚未接入。")
        if not _target_has_content(post, target):
            raise ValueError(f"当前 Package 不包含{spec.label}所需内容。")

    playwright = None
    context = None
    page = None
    current_step = "Starting browser"

    try:
        controller.system_step("starting_browser", "正在启动共享浏览器")
        playwright, context, page = start_browser(project_root)
        if on_browser_started is not None:
            on_browser_started(playwright, context, page)

        controller.start_platform(target)
        current_step = f"Preparing {target}"
        if target == "xiaohongshu":
            run_xiaohongshu(page, post, controller)
            ready_message = "小红书已准备完成"
        elif target == "baijiahao":
            run_baijiahao(page, post, controller)
            ready_message = "百家号已准备完成"
        else:
            page = run_wechat(page, post, controller)
            ready_message = "微信图文已准备完成"

        controller.ready(target, ready_message, "检查完成后点击结束流程并关闭浏览器。")
        current_step = "Closing browser"
        if context is not None:
            context.close()
        if playwright is not None:
            playwright.stop()
        controller.completed(f"{ready_message}。单平台发布准备流程已完成。")
    except BaseException as exc:
        error_step = getattr(exc, "step", current_step)
        try:
            controller.failed(target, error_step, exc)
        except Exception:
            pass
        raise


__all__ = [
    "run_publisher_workflow",
    "run_single_platform_workflow",
    "PublisherError",
]
