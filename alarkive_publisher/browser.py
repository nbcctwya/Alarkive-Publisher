"""Shared visible Chrome lifecycle for all platform publishers."""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

from playwright.sync_api import BrowserContext, Error as PlaywrightError, sync_playwright

from .publisher_common import PublisherError


def _automation_user_data_dir(project_root: Path) -> Path:
    override = os.environ.get("ALARKIVE_BROWSER_DATA_DIR")
    if override:
        return Path(override).expanduser()
    return project_root / ".browser-data"


def _chrome_window_snapshot() -> set[int]:
    """Return visible/top-level Chrome window handles on Windows.

    Playwright's ``page.bring_to_front`` only selects a tab.  On Windows a
    persistent Chrome launch can also leave the real browser window behind a
    small IME/helper window, so the OS window itself needs to be restored.
    This helper is intentionally a no-op outside Windows.
    """

    if os.name != "nt":
        return set()

    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        handles: set[int] = set()
        enum_proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        @enum_proc_type
        def enum_proc(hwnd: int, _lparam: int) -> bool:
            class_name = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, class_name, len(class_name))
            if class_name.value == "Chrome_WidgetWin_1":
                handles.add(int(hwnd))
            return True

        user32.EnumWindows(enum_proc, 0)
        return handles
    except Exception:
        # Window activation is a usability aid; it must never prevent the
        # publisher from starting in a restricted desktop environment.
        return set()


def _restore_new_chrome_window(before_handles: set[int]) -> None:
    """Restore and foreground the Chrome window created by this workflow."""

    if os.name != "nt":
        return

    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        deadline = time.monotonic() + 3.0
        new_handles: set[int] = set()
        while time.monotonic() < deadline:
            new_handles = _chrome_window_snapshot() - before_handles
            if new_handles:
                break
            time.sleep(0.05)

        if not new_handles:
            return

        # Chrome may create a small "restore page" prompt alongside the real
        # editor. Pick the largest new top-level window so that prompt cannot
        # accidentally become the window we foreground.
        def area(hwnd_value: int) -> int:
            rect = wintypes.RECT()
            user32.GetWindowRect(wintypes.HWND(hwnd_value), ctypes.byref(rect))
            return max(0, rect.right - rect.left) * max(0, rect.bottom - rect.top)

        hwnd = wintypes.HWND(max(new_handles, key=area))
        # SW_RESTORE also unminimizes a window.  SetWindowPos gives Chrome a
        # deterministic, normal on-screen size even when its persistent
        # profile has a stale 50x50/off-screen geometry saved in Local State.
        # HWND_NOTOPMOST is intentional: the workflow browser must remain
        # usable, but it must not cover the Web Content Manager forever.
        screen_width = max(1, int(user32.GetSystemMetrics(0)))
        screen_height = max(1, int(user32.GetSystemMetrics(1)))
        width = min(1200, max(900, screen_width // 2 + 100))
        height = min(800, max(650, screen_height - 100))
        left = max(0, screen_width - width)
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetWindowPos(
            hwnd,
            wintypes.HWND(-2),  # HWND_NOTOPMOST
            left,
            0,
            width,
            height,
            0x0040,  # SWP_SHOWWINDOW
        )
        user32.BringWindowToTop(hwnd)

        # Windows normally prevents a background thread from stealing focus.
        # Temporarily attach to the current foreground thread so the browser
        # launched by the Web worker becomes the same visible window that the
        # CLI launcher presents to the user.
        foreground = user32.GetForegroundWindow()
        current_thread = user32.GetCurrentThreadId()
        foreground_thread = (
            user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
        )
        attached = bool(
            foreground_thread
            and foreground_thread != current_thread
            and user32.AttachThreadInput(current_thread, foreground_thread, True)
        )
        try:
            user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
            user32.SetForegroundWindow(hwnd)
            user32.SetActiveWindow(hwnd)
            user32.SetFocus(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(current_thread, foreground_thread, False)
    except Exception:
        # Window activation is a usability aid; it must never prevent the
        # publisher from starting in a restricted desktop environment.
        pass


def _launch_context(playwright, user_data_dir: Path) -> BrowserContext:
    user_data_dir.mkdir(parents=True, exist_ok=True)
    channel = os.environ.get("ALARKIVE_BROWSER_CHANNEL", "chrome")
    kwargs = {
        "headless": False,
        "channel": channel,
        "timeout": 30_000,
        # Some Windows desktop environments restore a persistent Chrome
        # window as a tiny off-screen 50x50 window. Explicit headed geometry
        # gives the manual login/check page a sensible initial size; the OS
        # helper below also places it in a normal, non-topmost window layer.
        "viewport": None,
        "args": [
            "--new-window",
            "--window-size=1200,800",
        ],
    }
    try:
        return playwright.chromium.launch_persistent_context(str(user_data_dir), **kwargs)
    except PlaywrightError as exc:
        message = str(exc)
        if re.search(
            r"already in use|user data directory|profile.*lock|singleton|remote debugging",
            message,
            re.I,
        ):
            raise PublisherError(
                "Starting browser",
                "The automation Chrome profile could not be started. "
                "Use the default project .browser-data directory and make sure no "
                "other Alarkive Publisher process is using it.",
            ) from exc
        raise


def start_browser(project_root: Path):
    """Start the shared visible, persistent browser context."""
    browser_data_dir = _automation_user_data_dir(project_root)
    before_handles = _chrome_window_snapshot()
    playwright = None
    context = None
    try:
        playwright = sync_playwright().start()
        context = _launch_context(playwright, browser_data_dir)
        page = context.pages[0] if context.pages else context.new_page()
        page.bring_to_front()
        _restore_new_chrome_window(before_handles)
        page.set_default_timeout(15_000)
        return playwright, context, page
    except BaseException:
        # Context creation can succeed before page setup fails. Keep cleanup
        # here so callers never need a context/playwright handle they did not
        # receive, and never let cleanup replace the original exception.
        if context is not None:
            try:
                context.close()
            except BaseException:
                pass
        if playwright is not None:
            try:
                playwright.stop()
            except BaseException:
                pass
        raise


__all__ = ["start_browser"]
