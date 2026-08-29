from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..content import PostContent, load_post
from ..workflow_controller import WebWorkflowController
from .publish_state import (
    initialize_workflow,
    load_publish_state,
    mark_interrupted,
    mark_published,
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


WorkflowRunner = Callable[[PostContent, Path, WebWorkflowController], None]


@dataclass
class _ActiveJob:
    post_id: str
    post_folder: Path
    post: PostContent
    controller: WebWorkflowController
    thread: threading.Thread | None = None
    playwright: object | None = None
    context: object | None = None
    browser_open: bool = False
    close_requested: threading.Event = field(default_factory=threading.Event)


class PublishManager:
    """Own the single background Web Publisher workflow for this process."""

    def __init__(
        self,
        posts_root: Path | str | None = None,
        *,
        project_root: Path | str | None = None,
        workflow_runner: WorkflowRunner | None = None,
    ) -> None:
        self.posts_root = Path(posts_root).expanduser().resolve() if posts_root else POSTS_DIR
        self.project_root = (
            Path(project_root).expanduser().resolve()
            if project_root
            else self.posts_root.parent
        )
        self._workflow_runner = workflow_runner
        self._lock = threading.RLock()
        self._active_job: _ActiveJob | None = None
        self._reconcile_interrupted_workflows()

    def _reconcile_interrupted_workflows(self) -> None:
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
        """Mark the local state immediately and start a non-blocking worker."""

        post_folder = get_post_folder(post_id, self.posts_root)
        with self._lock:
            if self._active_job is not None:
                raise PublisherBusyError("当前已有发布流程正在运行，请先完成当前流程。")

            # Validate the immutable Package before changing the local marker.
            post = load_post(post_folder)
            current = load_publish_state(post_folder)
            if current["published"]:
                raise PublisherAlreadyPublishedError(
                    "该任务已经标记为已发布，请先重新置为未发布。"
                )

            controller = WebWorkflowController(post_folder)
            job = _ActiveJob(
                post_id=post_id,
                post_folder=post_folder,
                post=post,
                controller=controller,
            )
            # Register before writing the running state so a simultaneous read
            # can never mistake a just-starting job for a stale server restart.
            self._active_job = job
            try:
                mark_published(post_folder)
                initialize_workflow(post_folder)
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
        runner = self._workflow_runner
        if runner is None:
            from ..workflow import run_publisher_workflow

            runner = run_publisher_workflow

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

        try:
            if self._workflow_runner is None:
                runner(  # type: ignore[call-arg]
                    job.post,
                    self.project_root,
                    job.controller,
                    on_browser_started=remember_browser,
                )
            else:
                runner(job.post, self.project_root, job.controller)
        except BaseException as exc:
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
            if job.context is not None and not self._context_is_open(job.context):
                job.browser_open = False
            if job.context is not None and job.browser_open:
                # Keep the browser open for inspection, but let the user close
                # it through the Web UI on this same worker thread. Playwright
                # sync objects are intentionally not touched by request threads.
                job.close_requested.wait()
            if job.context is not None:
                self._close_browser(job)
        finally:
            with self._lock:
                if self._active_job is job:
                    self._active_job = None

    @staticmethod
    def _context_is_open(context: object) -> bool:
        """Check context liveness from the Playwright-owning worker thread."""

        try:
            pages = context.pages  # type: ignore[attr-defined]
            return any(not page.is_closed() for page in pages)
        except Exception:
            return False

    def _mark_browser_closed(self, job: _ActiveJob) -> None:
        with self._lock:
            job.browser_open = False

    @staticmethod
    def _close_browser(job: _ActiveJob) -> None:
        if job.context is not None:
            try:
                job.context.close()  # type: ignore[attr-defined]
            except Exception:
                LOGGER.warning("Could not close failed workflow browser context", exc_info=True)
        if job.playwright is not None:
            try:
                job.playwright.stop()  # type: ignore[attr-defined]
            except Exception:
                LOGGER.warning("Could not stop failed workflow Playwright", exc_info=True)

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
                state["workflow"]["status"] in {"running", "waiting"}
                and (self._active_job is None or self._active_job.post_id != post_id)
            ):
                state = mark_interrupted(folder)
            return state
