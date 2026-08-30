from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable

from .web.publish_state import update_workflow


PLATFORM_LABELS = {
    "xiaohongshu": "小红书",
    "baijiahao": "百家号",
    "wechat": "微信公众号",
}


class WorkflowController:
    """The pause/progress surface shared by CLI and Web publisher runs."""

    def start_platform(self, platform: str) -> None:
        raise NotImplementedError

    def step(self, platform: str, step: str, message: str) -> None:
        raise NotImplementedError

    def wait_for_user(
        self,
        platform: str,
        step: str,
        message: str,
        prompt: str,
    ) -> None:
        raise NotImplementedError

    def ready(self, platform: str, message: str, prompt: str) -> None:
        raise NotImplementedError

    def system_step(self, step: str, message: str) -> None:
        raise NotImplementedError

    def completed(self, message: str) -> None:
        raise NotImplementedError

    def failed(
        self,
        platform: str | None,
        step: str,
        exc: BaseException,
    ) -> None:
        raise NotImplementedError


class WorkflowBrowserClosedError(RuntimeError):
    """The shared browser was closed before the workflow was finished."""


class CLIWorkflowController(WorkflowController):
    """Keep the v0.1.2 terminal interaction behind the shared controller."""

    def start_platform(self, platform: str) -> None:
        print()
        print(f"--- {PLATFORM_LABELS.get(platform, platform)} ---")
        print()

    def step(self, platform: str, step: str, message: str) -> None:
        print(f"{PLATFORM_LABELS.get(platform, platform)}: {message}...")

    def wait_for_user(
        self,
        platform: str,
        step: str,
        message: str,
        prompt: str,
    ) -> None:
        print(message)
        print(prompt)
        input()

    def ready(self, platform: str, message: str, prompt: str) -> None:
        print()
        print("================================")
        print(message)
        print()
        print("The final Publish button was NOT clicked.")
        print("Please inspect the page manually in the browser.")
        print()
        print(prompt)
        print("================================")
        input()

    def system_step(self, step: str, message: str) -> None:
        print(message)

    def completed(self, message: str) -> None:
        print()
        print(message)

    def failed(
        self,
        platform: str | None,
        step: str,
        exc: BaseException,
    ) -> None:
        # main.py keeps its detailed traceback and browser-inspection prompt.
        return


class WebWorkflowController(WorkflowController):
    """Persist progress and wait for the matching Web Continue action."""

    def __init__(self, post_folder: Path | str):
        self.post_folder = Path(post_folder).expanduser()
        self._wait_lock = threading.Lock()
        self._continue_event = threading.Event()
        self._waiting = False
        self._current_platform: str | None = None
        self._current_step: str | None = None
        self._browser_probe: Callable[[], bool] | None = None
        self._browser_closed_callback: Callable[[], None] | None = None

    @property
    def current_platform(self) -> str | None:
        return self._current_platform

    @property
    def current_step(self) -> str | None:
        return self._current_step

    def set_browser_probe(
        self,
        probe: Callable[[], bool],
        on_closed: Callable[[], None] | None = None,
    ) -> None:
        """Install a worker-thread-only browser liveness check."""

        self._browser_probe = probe
        self._browser_closed_callback = on_closed

    def _update(
        self,
        *,
        status: str,
        platform: str | None = None,
        step: str | None = None,
        message: str | None = None,
        platform_status: str | None = None,
        platform_message: str | None = None,
        error: dict[str, Any] | None = None,
        set_error: bool = False,
    ) -> None:
        self._current_platform = platform
        self._current_step = step
        values: dict[str, Any] = {
            "status": status,
            "current_platform": platform,
            "current_step": step,
            "message": message,
        }
        if platform is not None:
            values["platform"] = platform
        if platform_status is not None:
            values["platform_status"] = platform_status
        if platform_message is not None:
            values["platform_message"] = platform_message
        if set_error:
            values["error"] = error
        update_workflow(self.post_folder, **values)

    def start_platform(self, platform: str) -> None:
        self.step(platform, "start", f"正在准备{PLATFORM_LABELS[platform]}")

    def step(self, platform: str, step: str, message: str) -> None:
        self._update(
            status="running",
            platform=platform,
            step=step,
            message=message,
            platform_status="running",
            platform_message=message,
        )

    def system_step(self, step: str, message: str) -> None:
        self._update(status="running", platform=None, step=step, message=message)

    def _wait(
        self,
        *,
        platform: str,
        step: str,
        message: str,
        platform_status: str,
        resume_platform_status: str,
    ) -> None:
        with self._wait_lock:
            self._continue_event.clear()
            self._waiting = True
            self._update(
                status="waiting",
                platform=platform,
                step=step,
                message=message,
                platform_status=platform_status,
                platform_message=message,
            )
        while not self._continue_event.wait(timeout=0.5):
            if self._browser_probe is None:
                continue
            try:
                browser_open = self._browser_probe()
            except Exception:
                browser_open = False
            if not browser_open:
                with self._wait_lock:
                    self._waiting = False
                if self._browser_closed_callback is not None:
                    self._browser_closed_callback()
                raise WorkflowBrowserClosedError(
                    "共享浏览器已被关闭，本次发布准备流程无法继续。"
                )
        with self._wait_lock:
            self._waiting = False
        self._update(
            status="running",
            platform=platform,
            step=step,
            message="用户已确认，继续执行",
            platform_status=resume_platform_status,
            # A final-check platform remains ready, including its original
            # ready message; intermediate waits transition to running.
            platform_message=(
                "继续执行" if resume_platform_status == "running" else None
            ),
        )

    def wait_for_user(
        self,
        platform: str,
        step: str,
        message: str,
        prompt: str,
    ) -> None:
        del prompt
        self._wait(
            platform=platform,
            step=step,
            message=message,
            platform_status="waiting",
            resume_platform_status="running",
        )

    def ready(self, platform: str, message: str, prompt: str) -> None:
        del prompt
        self._wait(
            platform=platform,
            step="ready",
            message=message,
            platform_status="ready",
            resume_platform_status="ready",
        )

    def continue_if_waiting(self) -> bool:
        with self._wait_lock:
            if not self._waiting or self._continue_event.is_set():
                return False
            self._continue_event.set()
            return True

    def completed(self, message: str) -> None:
        self._update(
            status="completed",
            platform=None,
            step=None,
            message=message,
        )

    def failed(
        self,
        platform: str | None,
        step: str,
        exc: BaseException,
    ) -> None:
        error = {
            "platform": platform,
            "step": step,
            "type": type(exc).__name__,
            "message": str(exc),
        }
        self._update(
            status="failed",
            platform=platform,
            step=step,
            message=f"发布流程失败：{exc}",
            platform_status="failed" if platform else None,
            platform_message=str(exc) if platform else None,
            error=error,
            set_error=True,
        )
