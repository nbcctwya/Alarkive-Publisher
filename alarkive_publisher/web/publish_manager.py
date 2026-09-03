from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, cast

from ..content import PostContent, load_post
from ..routing import (
    AVAILABLE_PUBLISHERS,
    PUBLISHER_REGISTRY,
    PUBLISH_TARGETS,
    WORKFLOW_TARGETS,
    normalize_target,
)
from ..workflow_controller import WebWorkflowController
from .publish_state import (
    initialize_workflow,
    load_publish_state,
    mark_interrupted,
    mark_published,
    resume_workflow,
    update_workflow,
)
from .storage import POSTS_DIR, get_post_folder


LOGGER = logging.getLogger(__name__)


class PublishManagerError(RuntimeError):
    """An expected publisher-management action error."""


class PublisherBusyError(PublishManagerError):
    """A different post currently owns the shared browser profile."""


class PublisherNotWaitingError(PublishManagerError):
    """Continue was requested while no matching wait point was active."""


class PublisherAlreadyPublishedError(PublishManagerError):
    """The local marker must be reset before starting another workflow."""


class PublisherUnsupportedPlatformError(PublishManagerError):
    """A workflow was requested for an unsupported or missing platform."""


WorkflowRunner = Callable[[PostContent, Path, WebWorkflowController], None]
PlatformWorkflowRunner = Callable[
    [PostContent, Path, str, WebWorkflowController], None
]


@dataclass
class _ActiveJob:
    post_id: str
    post_folder: Path
    post: PostContent
    controller: WebWorkflowController
    workflow_mode: str = "all"
    target_platform: str | None = None
    thread: threading.Thread | None = None
    playwright: object | None = None
    context: object | None = None
    browser_open: bool = False
    close_requested: threading.Event = field(default_factory=threading.Event)
    skip_targets: tuple[str, ...] = ()


class PublishManager:
    """Own the single background Web Publisher workflow for this process."""

    def __init__(
        self,
        posts_root: Path | str | None = None,
        *,
        project_root: Path | str | None = None,
        workflow_runner: WorkflowRunner | None = None,
        platform_workflow_runner: PlatformWorkflowRunner | None = None,
    ) -> None:
        self.posts_root = Path(posts_root).expanduser().resolve() if posts_root else POSTS_DIR
        self.project_root = (
            Path(project_root).expanduser().resolve()
            if project_root
            else self.posts_root.parent
        )
        self._workflow_runner = workflow_runner
        self._platform_workflow_runner = platform_workflow_runner
        self._lock = threading.RLock()
        self._active_job: _ActiveJob | None = None

    def reconcile_interrupted_workflows(self) -> None:
        """Mark persisted waits/runs interrupted once after a server restart."""

        if not self.posts_root.is_dir():
            return
        for folder in self.posts_root.iterdir():
            if not folder.is_dir() or folder.name.startswith("."):
                continue
            try:
                state = load_publish_state(folder)
                if state["workflow"]["status"] in {"running", "waiting"}:
                    mark_interrupted(folder)
            except Exception:
                LOGGER.warning(
                    "Could not reconcile publisher state in %s",
                    folder,
                    exc_info=True,
                )

    def get_publish_state(self, post_id: str) -> dict:
        folder = get_post_folder(post_id, self.posts_root)
        return load_publish_state(folder)

    def has_active_workflow(self) -> bool:
        with self._lock:
            return self._active_job is not None

    def active_post_id(self) -> str | None:
        with self._lock:
            return self._active_job.post_id if self._active_job else None

    def start_publish(self, post_id: str) -> dict:
        """Start the established all-platform workflow."""

        return self._start_workflow(
            post_id,
            workflow_mode="all",
            mark_local_published=True,
        )

    @staticmethod
    def _ready_targets_for_resume(post: PostContent, state: dict) -> tuple[str, ...]:
        workflow = state["workflow"]
        if workflow["workflow_mode"] != "all" or workflow["status"] not in {"failed", "interrupted", "cancelled"}:
            return ()
        active = [
            target for target in WORKFLOW_TARGETS
            if target in AVAILABLE_PUBLISHERS
            and post.has_content(PUBLISHER_REGISTRY[target].variant)
        ]
        ready = tuple(
            target for target in active
            if workflow["platforms"][target]["status"] == "ready"
        )
        return ready if ready and len(ready) < len(active) else ()

    def can_resume(self, post_id: str) -> bool:
        folder = get_post_folder(post_id, self.posts_root)
        post = load_post(folder)
        state = load_publish_state(folder)
        return bool(self._ready_targets_for_resume(post, state))

    def start_publish_all(self, post_id: str) -> dict:
        """Explicit name for the established all-platform workflow."""

        return self.start_publish(post_id)

    def start_platform_publish(self, post_id: str, platform: str) -> dict:
        """Start a non-blocking workflow for exactly one supported platform."""

        if platform == "xiaohongshu":
            raise PublisherUnsupportedPlatformError("小红书已从 Web 发布入口移除。")
        platform = normalize_target(platform)
        spec = PUBLISHER_REGISTRY.get(platform)
        if spec is None:
            supported = "、".join(PUBLISH_TARGETS)
            raise PublisherUnsupportedPlatformError(
                f"不支持的平台：{platform}。可选平台：{supported}。"
            )
        if platform not in AVAILABLE_PUBLISHERS:
            raise PublisherUnsupportedPlatformError("该平台 Publisher 尚未接入。")
        return self._start_workflow(
            post_id,
            workflow_mode="single",
            target_platform=platform,
            mark_local_published=False,
        )

    def start_publish_platform(self, post_id: str, platform: str) -> dict:
        """Compatibility spelling for callers using ``start_publish_platform``."""

        return self.start_platform_publish(post_id, platform)

    def _start_workflow(
        self,
        post_id: str,
        *,
        workflow_mode: str,
        target_platform: str | None = None,
        mark_local_published: bool,
    ) -> dict:
        """Mark the local state immediately and start a non-blocking worker."""

        post_folder = get_post_folder(post_id, self.posts_root)
        with self._lock:
            if self._active_job is not None:
                raise PublisherBusyError("当前已有发布流程正在运行，请先完成当前流程。")

            # Validate the immutable Package before changing the local marker.
            post = load_post(post_folder)
            if workflow_mode == "all" and not any(
                post.has_content(PUBLISHER_REGISTRY[target].variant)
                for target in WORKFLOW_TARGETS
                if target in AVAILABLE_PUBLISHERS
            ):
                raise PublisherUnsupportedPlatformError(
                    "当前任务包含内容，但没有已接入的可发布平台。"
                    "没有可用于完整发布流程的平台内容。"
                )
            if target_platform is not None:
                target_platform = normalize_target(target_platform)
                spec = PUBLISHER_REGISTRY.get(target_platform)
                if spec is None or target_platform not in AVAILABLE_PUBLISHERS:
                    raise PublisherUnsupportedPlatformError("该平台 Publisher 尚未接入。")
                if not post.has_content(spec.variant):
                    platform_labels = {
                        "baijiahao": "百家号",
                        "toutiao_article": "今日头条文章",
                        "wechat_image": "微信图文",
                    }
                    label = platform_labels.get(target_platform, spec.label)
                    raise PublisherUnsupportedPlatformError(
                        f"当前任务不包含{label}所需内容，无法启动单平台发布。"
                    )
            current = load_publish_state(post_folder)
            ready_targets = (
                self._ready_targets_for_resume(post, current)
                if workflow_mode == "all" else ()
            )
            if mark_local_published and current["published"] and not ready_targets:
                raise PublisherAlreadyPublishedError(
                    "该任务已经标记为已发布，请先重新置为未发布。"
                )

            controller = WebWorkflowController(post_folder)
            job = _ActiveJob(
                post_id=post_id,
                post_folder=post_folder,
                post=post,
                controller=controller,
                workflow_mode=workflow_mode,
                target_platform=target_platform,
                skip_targets=ready_targets,
            )
            # Register before writing the running state so a simultaneous read
            # can never mistake a just-starting job for a stale server restart.
            self._active_job = job
            try:
                if mark_local_published and not current["published"]:
                    mark_published(post_folder)
                if ready_targets:
                    resume_workflow(post_folder, ready_targets)
                elif workflow_mode == "all":
                    # Keep the established all-platform initialization call
                    # and path exactly as it was before v0.1.7.
                    initialize_workflow(post_folder)
                else:
                    initialize_workflow(
                        post_folder,
                        workflow_mode=workflow_mode,
                        target_platform=target_platform,
                    )
                thread = threading.Thread(
                    target=self._run_job,
                    args=(job,),
                    name=f"alarkive-publisher-{post_id}",
                    daemon=True,
                )
                job.thread = thread
                thread.start()
            except BaseException:
                self._active_job = None
                raise

        return load_publish_state(post_folder)

    def _run_job(self, job: _ActiveJob) -> None:
        # The default runner selection is mode-specific.  A supplied legacy
        # ``workflow_runner`` remains a three-argument test/integration hook;
        # callers may supply a four-argument platform runner when they need to
        # observe or replace only the new single-platform path.
        default_runner = self._workflow_runner is None and (
            job.workflow_mode == "all" or self._platform_workflow_runner is None
        )
        runner: Callable[..., None]
        if default_runner:
            from ..workflow import run_publisher_workflow, run_single_platform_workflow

            runner = (
                cast(Callable[..., None], run_publisher_workflow)
                if job.workflow_mode == "all"
                else cast(Callable[..., None], run_single_platform_workflow)
            )

            def remember_browser(playwright, context, page) -> None:
                del page
                with self._lock:
                    job.playwright = playwright
                    job.context = context
                    job.browser_open = True
                job.controller.set_browser_probe(
                    lambda: self._context_is_open(context),
                    lambda: self._mark_browser_closed(job),
                )
        elif job.workflow_mode == "single" and self._platform_workflow_runner is not None:
            runner = cast(Callable[..., None], self._platform_workflow_runner)
        else:
            runner = cast(Callable[..., None], self._workflow_runner)

        try:
            if default_runner:
                if job.workflow_mode == "all":
                    runner_kwargs = {"on_browser_started": remember_browser}
                    if job.skip_targets:
                        runner_kwargs["skip_targets"] = job.skip_targets
                    runner(
                        job.post,
                        self.project_root,
                        job.controller,
                        **runner_kwargs,
                    )
                else:
                    runner(
                        job.post,
                        self.project_root,
                        job.target_platform,
                        job.controller,
                        on_browser_started=remember_browser,
                    )
            elif job.workflow_mode == "single" and self._platform_workflow_runner is not None:
                runner(
                    job.post,
                    self.project_root,
                    job.target_platform,
                    job.controller,
                )
            else:
                # Preserve the existing injectable three-argument runner used
                # by the all-platform workflow and its tests.
                runner(job.post, self.project_root, job.controller)
        except BaseException as exc:
            if job.controller.cancel_requested:
                return
            try:
                state = load_publish_state(job.post_folder)
                if state["workflow"]["status"] not in {"failed", "completed"}:
                    job.controller.failed(
                        job.controller.current_platform,
                        getattr(exc, "step", "Publisher workflow"),
                        exc,
                    )
            except Exception:
                LOGGER.exception("Could not save publisher failure state")
            LOGGER.exception("Web Publisher workflow failed for %s", job.post_id)
            if job.context is not None and self._job_browser_is_open(job):
                # Keep the browser open for inspection, but let the user close
                # it through the Web UI on this same worker thread. Playwright
                # sync objects are intentionally not touched by request threads.
                self._wait_for_failed_browser_close(job)
            if job.context is not None and not job.controller.cancel_requested:
                self._close_browser(job)
        finally:
            try:
                if job.controller.cancel_requested:
                    # Playwright cleanup must stay on its owning worker thread.
                    self._close_browser(job)
                    job.controller.cancelled()
            finally:
                with self._lock:
                    if self._active_job is job:
                        job.browser_open = False
                        self._active_job = None

    @staticmethod
    def _context_is_open(context: object) -> bool:
        """Check context liveness from the Playwright-owning worker thread."""

        try:
            pages = context.pages  # type: ignore[attr-defined]
            return any(not page.is_closed() for page in pages)
        except Exception:
            return False

    def _job_browser_is_open(self, job: _ActiveJob) -> bool:
        with self._lock:
            browser_open = job.browser_open
        if not browser_open or job.context is None:
            return False
        if self._context_is_open(job.context):
            return True
        self._mark_browser_closed(job)
        return False

    def _wait_for_failed_browser_close(self, job: _ActiveJob) -> None:
        """Wait for Web close or an external browser shutdown.

        Playwright objects belong to the worker thread. Polling here lets the
        same thread observe a manually closed or crashed browser without
        blocking the active-job slot forever.
        """

        while not job.close_requested.wait(timeout=0.5):
            if job.context is None or not self._context_is_open(job.context):
                self._mark_browser_closed(job)
                return

    def _mark_browser_closed(self, job: _ActiveJob) -> None:
        with self._lock:
            job.browser_open = False

    @staticmethod
    def _close_browser(job: _ActiveJob) -> None:
        if job.context is not None:
            try:
                # A manual Chrome close already disconnected the context. Do
                # not call close() again in that case.
                if PublishManager._context_is_open(job.context):
                    job.context.close()  # type: ignore[attr-defined]
            except Exception:
                LOGGER.warning("Could not close failed workflow browser context", exc_info=True)
        if job.playwright is not None:
            try:
                job.playwright.stop()  # type: ignore[attr-defined]
            except Exception:
                LOGGER.warning("Could not stop failed workflow Playwright", exc_info=True)
        job.browser_open = False

    def close_browser(self, post_id: str) -> dict:
        with self._lock:
            job = self._active_job
            if job is None or job.post_id != post_id or job.context is None:
                raise PublishManagerError("当前没有可关闭的发布流程浏览器。")
            state = load_publish_state(job.post_folder)
            if state["workflow"]["status"] != "failed":
                raise PublishManagerError("只有失败的发布流程可以关闭浏览器。")
            job.close_requested.set()
            return state

    def cancel_requested_for(self, post_id: str) -> bool:
        with self._lock:
            job = self._active_job
            return bool(job and job.post_id == post_id and job.controller.cancel_requested)

    def cancel_publish(self, post_id: str) -> dict:
        """Request a worker-thread stop even when the persisted status is stale."""

        with self._lock:
            job = self._active_job
            if job is None or job.post_id != post_id:
                raise PublishManagerError("当前任务没有正在运行的发布准备流程。")
            state = update_workflow(
                job.post_folder,
                message="正在取消发布准备，当前浏览器操作结束后关闭浏览器。",
            )
            job.controller.request_cancel()
            job.close_requested.set()
            return state

    def browser_open_for(self, post_id: str) -> bool:
        with self._lock:
            return bool(
                self._active_job
                and self._active_job.post_id == post_id
                and self._active_job.browser_open
            )

    def continue_publish(self, post_id: str) -> dict:
        with self._lock:
            job = self._active_job
            if job is None or job.post_id != post_id:
                raise PublisherNotWaitingError("当前发布流程不在等待状态。")
            if not job.controller.continue_if_waiting():
                raise PublisherNotWaitingError("当前发布流程不在等待状态。")
            return load_publish_state(job.post_folder)

    def reconcile_post_if_needed(self, post_id: str) -> dict:
        """Reconcile a state created by a process that is no longer running."""

        folder = get_post_folder(post_id, self.posts_root)
        with self._lock:
            state = load_publish_state(folder)
            if (
                state["workflow"]["status"] == "interrupted"
                and self._active_job is not None
                and self._active_job.post_id == post_id
                and self._active_job.controller.is_waiting_for(
                    state["workflow"]["current_platform"],
                    state["workflow"]["current_step"],
                )
            ):
                platform = state["workflow"]["current_platform"]
                state = update_workflow(
                    folder,
                    status="waiting",
                    message=state["workflow"]["platforms"][platform]["message"],
                    error=None,
                )
            if (
                state["workflow"]["status"] in {"running", "waiting"}
                and (self._active_job is None or self._active_job.post_id != post_id)
            ):
                state = mark_interrupted(folder)
            return state
