from __future__ import annotations

from pathlib import Path
from typing import Callable

from .baijiahao import run_baijiahao
from .content import PostContent
from .wechat import run_wechat
from .workflow_controller import WorkflowController
from .xiaohongshu import PublisherError, run_xiaohongshu, start_browser


BrowserStarted = Callable[[object, object, object], None]
SINGLE_PLATFORM_TARGETS = ("xiaohongshu", "baijiahao", "wechat")


def run_publisher_workflow(
    post: PostContent,
    project_root: Path,
    controller: WorkflowController,
    *,
    on_browser_started: BrowserStarted | None = None,
) -> None:
    """Run all three shared platform publishers in order.

    The workflow deliberately stops at each platform's ready-to-publish page.
    No function in this orchestration clicks a platform's final publish button.
    """

    playwright = None
    context = None
    page = None
    current_platform: str | None = None
    current_step = "Starting browser"

    try:
        controller.system_step("starting_browser", "正在启动共享浏览器")
        playwright, context, page = start_browser(project_root)
        if on_browser_started is not None:
            on_browser_started(playwright, context, page)

        current_platform = "xiaohongshu"
        current_step = "Preparing Xiaohongshu"
        controller.start_platform(current_platform)
        run_xiaohongshu(page, post, controller)
        controller.ready(
            current_platform,
            "小红书已准备完成",
            "检查完成后点击继续到百家号。",
        )

        current_platform = "baijiahao"
        current_step = "Preparing Baijiahao"
        controller.start_platform(current_platform)
        run_baijiahao(page, post, controller)
        controller.ready(
            current_platform,
            "百家号已准备完成",
            "检查完成后点击继续到微信公众号。",
        )

        current_platform = "wechat"
        current_step = "Preparing WeChat"
        controller.start_platform(current_platform)
        page = run_wechat(page, post, controller)
        controller.ready(
            current_platform,
            "微信公众号已准备完成",
            "检查完成后点击结束流程并关闭浏览器。",
        )

        current_step = "Closing browser"
        if context is not None:
            context.close()
        if playwright is not None:
            playwright.stop()
        controller.completed("发布准备流程完成。三个平台均已停在最终发布按钮之前。")
    except BaseException as exc:
        error_step = getattr(exc, "step", current_step)
        try:
            controller.failed(current_platform, error_step, exc)
        except Exception:
            # The original exception remains the useful failure; state-write
            # errors should not hide it from the CLI or worker log.
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
    """Prepare exactly one platform in its own shared-browser session.

    This is intentionally a sibling of ``run_publisher_workflow``.  The
    established all-platform orchestration above remains unchanged; this
    entry point only reuses the existing platform runners for targeted runs.
    """

    if platform not in SINGLE_PLATFORM_TARGETS:
        raise ValueError(
            f"Unsupported single-platform workflow target: {platform!r}. "
            f"Expected one of {', '.join(SINGLE_PLATFORM_TARGETS)}."
        )

    playwright = None
    context = None
    page = None
    current_step = "Starting browser"

    try:
        controller.system_step("starting_browser", "正在启动共享浏览器")
        playwright, context, page = start_browser(project_root)
        if on_browser_started is not None:
            on_browser_started(playwright, context, page)

        controller.start_platform(platform)
        current_step = f"Preparing {platform}"
        if platform == "xiaohongshu":
            run_xiaohongshu(page, post, controller)
            ready_message = "小红书已准备完成"
        elif platform == "baijiahao":
            run_baijiahao(page, post, controller)
            ready_message = "百家号已准备完成"
        else:
            page = run_wechat(page, post, controller)
            ready_message = "微信公众号已准备完成"

        controller.ready(
            platform,
            ready_message,
            "检查完成后点击结束流程并关闭浏览器。",
        )

        current_step = "Closing browser"
        if context is not None:
            context.close()
        if playwright is not None:
            playwright.stop()
        controller.completed(f"{ready_message}。单平台发布准备流程已完成。")
    except BaseException as exc:
        error_step = getattr(exc, "step", current_step)
        try:
            controller.failed(platform, error_step, exc)
        except Exception:
            # Preserve the original exception if persisting failure state also
            # fails, matching the established all-platform workflow behavior.
            pass
        raise


__all__ = [
    "run_publisher_workflow",
    "run_single_platform_workflow",
    "PublisherError",
]
