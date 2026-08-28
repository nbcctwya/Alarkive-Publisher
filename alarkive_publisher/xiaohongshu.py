from __future__ import annotations

import os
import re
import sys
import traceback
from pathlib import Path
from typing import Callable, Iterable, TypeVar

from playwright.sync_api import (
    BrowserContext,
    Error as PlaywrightError,
    Locator,
    Page,
    TimeoutError,
    sync_playwright,
)

from .content import PostContent


LOGIN_URL = "https://creator.xiaohongshu.com/"
PUBLISH_URL = "https://creator.xiaohongshu.com/publish/publish?source=official"

T = TypeVar("T")


class PublisherError(RuntimeError):
    def __init__(self, step: str, message: str):
        super().__init__(message)
        self.step = step
        self.message = message


def _first_visible(page: Page, selectors: Iterable[str]) -> Locator | None:
    for selector in selectors:
        locator = page.locator(selector)
        try:
            count = locator.count()
        except Exception:
            continue
        for index in range(count):
            candidate = locator.nth(index)
            if _is_interactable(candidate):
                return candidate
    return None


def _is_interactable(locator: Locator) -> bool:
    try:
        if not locator.is_visible():
            return False
        box = locator.bounding_box()
        if box is None or box["width"] <= 0 or box["height"] <= 0:
            return False
        style = locator.evaluate(
            """
            element => {
                let current = element;
                let opacity = 1;
                let pointerEvents = 'auto';
                let visibility = 'visible';
                while (current && current.nodeType === Node.ELEMENT_NODE) {
                    const computed = getComputedStyle(current);
                    opacity *= Number(computed.opacity);
                    if (computed.pointerEvents === 'none') pointerEvents = 'none';
                    if (computed.visibility === 'hidden') visibility = 'hidden';
                    current = current.parentElement;
                }
                return {
                    opacity,
                    pointerEvents,
                    visibility,
                };
            }
            """
        )
        return (
            style["opacity"] >= 0.1
            and style["pointerEvents"] != "none"
            and style["visibility"] != "hidden"
            and box["x"] > -1000
            and box["y"] > -1000
        )
    except Exception:
        return False


def _first_visible_locator(locator: Locator) -> Locator | None:
    try:
        count = locator.count()
    except Exception:
        return None
    for index in range(count):
        candidate = locator.nth(index)
        if _is_interactable(candidate):
            return candidate
    return None


def _image_file_input_or_none(page: Page) -> Locator | None:
    inputs = page.locator('input[type="file"]')
    for index in range(inputs.count()):
        candidate = inputs.nth(index)
        accept = (candidate.get_attribute("accept") or "").lower()
        if "image" in accept or ".png" in accept:
            return candidate
    return None


def _visible_text(page: Page) -> str:
    try:
        return page.locator("body").inner_text(timeout=2_000)
    except Exception:
        return ""


def _is_login_page(page: Page) -> bool:
    if "/login" in page.url:
        return True
    phone = _first_visible(page, ['input[placeholder*="手机号"]'])
    code = _first_visible(page, ['input[placeholder*="验证码"]'])
    return phone is not None and code is not None


def _wait_for_dom(page: Page) -> None:
    try:
        page.wait_for_load_state("domcontentloaded", timeout=30_000)
    except TimeoutError:
        # The page can remain busy because the creator center has long-lived
        # requests. The visible DOM is still usable after this timeout.
        pass
    page.wait_for_function(
        """
        () => {
            const body = document.body;
            return !!body && (
                body.innerText.trim().length > 0 ||
                !!document.querySelector('input[type="file"]')
            );
        }
        """,
        timeout=30_000,
    )


def _run_step(step: str, action: Callable[[], T]) -> T:
    try:
        return action()
    except PublisherError:
        raise
    except Exception as exc:
        raise PublisherError(step, f"{type(exc).__name__}: {exc}") from exc


def _check_login(page: Page) -> None:
    page.goto(PUBLISH_URL, wait_until="domcontentloaded", timeout=60_000)
    _wait_for_dom(page)

    if not _is_login_page(page):
        return

    print("Xiaohongshu is not logged in.")
    print("Please complete login manually in the browser.")
    print("Press Enter after login is complete...")
    input()

    page.goto(PUBLISH_URL, wait_until="domcontentloaded", timeout=60_000)
    _wait_for_dom(page)
    if _is_login_page(page):
        raise PublisherError(
            "Checking login",
            "Login was not completed. The page is still showing the login screen.",
        )


def _open_image_publisher(page: Page) -> None:
    # File inputs are often hidden by the page and are still valid upload
    # targets, so visibility is not required here.
    if _image_file_input_or_none(page) is not None:
        return

    # Depending on the creator-center version, the direct publish URL may
    # initially show a choice between image and video publishing.
    image_tab = page.get_by_text("上传图文", exact=True)
    publish_image_tab = page.get_by_text("发布图文", exact=True)
    try:
        image_tab.first.wait_for(state="attached", timeout=30_000)
    except TimeoutError:
        pass
    candidates = [
        image_tab,
        publish_image_tab,
        page.get_by_role("button", name=re.compile(r"发布图文|上传图文")),
        page.get_by_role("tab", name=re.compile(r"图文")),
    ]
    clicked = False
    for candidate in candidates:
        visible_candidate = _first_visible_locator(candidate)
        if visible_candidate is None:
            continue
        # This is a client-side tab switch, not a full navigation.
        # Avoid Playwright waiting for a traditional load event here.
        visible_candidate.click(no_wait_after=True)
        clicked = True
        break

    if not clicked:
        raise PublisherError(
            "Opening Xiaohongshu publisher",
            "Could not find an interactable '上传图文' tab on the publish page.",
        )

    try:
        page.locator(
            'input[type="file"][accept*="image"], '
            'input[type="file"][accept*=".png"]'
        ).first.wait_for(state="attached", timeout=30_000)
    except TimeoutError as exc:
        raise PublisherError(
            "Opening Xiaohongshu publisher",
            "Clicked '上传图文', but the image upload input did not appear within 30 seconds.",
        ) from exc

    file_input = _image_file_input_or_none(page)
    if file_input is None:
        raise PublisherError(
            "Opening Xiaohongshu publisher",
            "Could not find the image upload input on the publish page. "
            f"Current URL: {page.url}",
        )


def _image_file_input(page: Page) -> Locator:
    locator = _image_file_input_or_none(page)
    if locator is not None:
        return locator
    raise PublisherError("Uploading images", "Could not find an image file input.")


def _wait_for_uploads(page: Page) -> None:
    page.wait_for_function(
        """
        () => {
            const text = document.body ? document.body.innerText : '';
            return !/(上传中|正在上传|uploading)/i.test(text);
        }
        """,
        timeout=60_000,
    )
    text = _visible_text(page)
    if re.search(r"上传失败|上传错误|upload failed", text, re.IGNORECASE):
        raise PublisherError("Uploading images", "The page reported an image upload failure.")


def _wait_for_image_previews(page: Page, expected_count: int) -> None:
    page.wait_for_function(
        """
        expected => {
            const text = document.body ? document.body.innerText : '';
            const progress = new RegExp(`\\b1/${expected}\\b`).test(text);
            const previews = document.querySelectorAll('img[src^="blob:"]').length;
            return progress || previews >= expected;
        }
        """,
        arg=expected_count,
        timeout=60_000,
    )


def _upload_images(page: Page, images: tuple[Path, ...]) -> None:
    file_input = _image_file_input(page)
    image_paths = [str(path.resolve()) for path in images]
    is_multiple = file_input.get_attribute("multiple") is not None

    if is_multiple or len(image_paths) == 1:
        file_input.set_input_files(image_paths, timeout=30_000)
    else:
        # Some page versions expose a single-file input but append each change
        # to the gallery. This keeps the first version usable on those pages.
        for image_path in image_paths:
            file_input.set_input_files(image_path, timeout=30_000)

    _wait_for_uploads(page)
    _wait_for_image_previews(page, len(images))


def _wait_for_interactable_selector(page: Page, selectors: list[str]) -> None:
    page.wait_for_function(
        """
        selectors => selectors.some(selector => {
            return Array.from(document.querySelectorAll(selector)).some(element => {
                let current = element;
                let opacity = 1;
                let pointerEvents = 'auto';
                let visibility = 'visible';
                while (current && current.nodeType === Node.ELEMENT_NODE) {
                    const computed = getComputedStyle(current);
                    opacity *= Number(computed.opacity);
                    if (computed.pointerEvents === 'none') pointerEvents = 'none';
                    if (computed.visibility === 'hidden') visibility = 'hidden';
                    current = current.parentElement;
                }
                const rect = element.getBoundingClientRect();
                return opacity >= 0.1 && pointerEvents !== 'none' &&
                    visibility !== 'hidden' && rect.width > 0 && rect.height > 0 &&
                    rect.x > -1000 && rect.y > -1000;
            });
        })
        """,
        arg=selectors,
        timeout=60_000,
    )


def _title_locator(page: Page) -> Locator:
    selectors = [
        'input[placeholder*="标题"]',
        'textarea[placeholder*="标题"]',
        '[contenteditable="true"][data-placeholder*="标题"]',
    ]
    _wait_for_interactable_selector(page, selectors)
    locator = _first_visible(
        page,
        selectors,
    )
    if locator is None:
        raise PublisherError("Filling title and content", "Could not find the title input.")
    return locator


def _body_locator(page: Page) -> Locator:
    selectors = [
        'textarea[placeholder*="正文"]',
        '[contenteditable="true"][data-placeholder*="正文"]',
        '[contenteditable="true"][aria-label*="正文"]',
        '[contenteditable="true"]',
    ]
    _wait_for_interactable_selector(page, selectors)
    locator = _first_visible(
        page,
        selectors,
    )
    if locator is None:
        raise PublisherError("Filling title and content", "Could not find the body editor.")
    return locator


def _read_locator_value(locator: Locator) -> str:
    try:
        return locator.input_value()
    except Exception:
        try:
            return locator.inner_text()
        except Exception:
            return locator.text_content() or ""


def _compact_text(value: str) -> str:
    return re.sub(r"\s+", "", value.replace("\u00a0", " "))


def _fill_text(page: Page, post: PostContent) -> None:
    title = _title_locator(page)
    title.fill(post.title, timeout=15_000)
    if _read_locator_value(title) != post.title:
        raise PublisherError(
            "Filling title and content",
            "The title was not accepted exactly as provided; it was not modified.",
        )

    body = _body_locator(page)
    try:
        body.fill(post.body, timeout=15_000)
    except Exception:
        # A few rich-text implementations accept keyboard insertion more
        # reliably than fill(), while still preserving newlines and emoji.
        body.click()
        page.keyboard.insert_text(post.body)

    actual_body = _read_locator_value(body)
    if post.body and _compact_text(post.body) not in _compact_text(actual_body):
        raise PublisherError(
            "Filling title and content",
            "The body editor did not contain the provided content after filling.",
        )

    possible_validation = _visible_text(page)
    if re.search(r"标题.{0,12}(超出|过长|最多|限制)|内容.{0,12}(超出|过长|最多|限制)", possible_validation):
        raise PublisherError(
            "Filling title and content",
            "The page reported a title or content length/validation problem. "
            "The text was not modified.",
        )


def _automation_user_data_dir(project_root: Path) -> Path:
    override = os.environ.get("ALARKIVE_BROWSER_DATA_DIR")
    if override:
        return Path(override).expanduser()
    return project_root / ".browser-data"


def _launch_context(playwright, user_data_dir: Path) -> BrowserContext:
    user_data_dir.mkdir(parents=True, exist_ok=True)
    channel = os.environ.get("ALARKIVE_BROWSER_CHANNEL", "chrome")
    kwargs = {
        "headless": False,
        "channel": channel,
        "timeout": 30_000,
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


def _pause_and_close(context: BrowserContext, message: str) -> None:
    print(message)
    try:
        input()
    finally:
        context.close()


def run_dry_run(post: PostContent, project_root: Path) -> None:
    browser_data_dir = _automation_user_data_dir(project_root)
    debug_dir = project_root / "debug"
    debug_dir.mkdir(parents=True, exist_ok=True)

    context: BrowserContext | None = None
    page: Page | None = None
    playwright = None
    current_step = "Starting browser"

    try:
        playwright = sync_playwright().start()
        current_step = "Starting browser"
        print("[2/7] Starting browser...")
        context = _launch_context(playwright, browser_data_dir)
        page = context.pages[0] if context.pages else context.new_page()
        page.set_default_timeout(15_000)

        current_step = "Checking login"
        print("[3/7] Checking login...")
        _run_step(current_step, lambda: _check_login(page))

        current_step = "Opening Xiaohongshu publisher"
        print("[4/7] Opening Xiaohongshu publisher...")
        _run_step(current_step, lambda: _open_image_publisher(page))

        current_step = "Uploading images"
        print(f"[5/7] Uploading {len(post.images)} images...")
        _run_step(current_step, lambda: _upload_images(page, post.images))

        current_step = "Filling title and content"
        print("[6/7] Filling title and content...")
        _run_step(current_step, lambda: _fill_text(page, post))

        current_step = "Done"
        print("[7/7] Done.")
        print()
        print("================================")
        print("Alarkive Publisher v0.0.1")
        print()
        print("✓ Images uploaded")
        print("✓ Title filled")
        print("✓ Content filled")
        print()
        print("DRY RUN COMPLETE")
        print()
        print("The final Publish button was NOT clicked.")
        print("Please inspect the post manually in the browser.")
        print()
        print("Press Enter to close the browser...")
        print("================================")
        input()
    except Exception as exc:
        print()
        print(f"ERROR during step: {current_step}", file=sys.stderr)
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        traceback.print_exc()
        if page is not None:
            try:
                screenshot_path = debug_dir / "failure.png"
                page.screenshot(path=str(screenshot_path), full_page=True)
                print(f"Debug screenshot saved to: {screenshot_path}", file=sys.stderr)
            except Exception as screenshot_error:
                print(
                    f"Could not save debug screenshot: {type(screenshot_error).__name__}: "
                    f"{screenshot_error}",
                    file=sys.stderr,
                )
        if context is not None:
            try:
                _pause_and_close(
                    context,
                    "The browser was left open for inspection. Press Enter to close it...",
                )
            except Exception:
                try:
                    context.close()
                except Exception:
                    pass
        raise
    finally:
        if context is not None:
            try:
                context.close()
            except Exception:
                pass
        if playwright is not None:
            playwright.stop()
