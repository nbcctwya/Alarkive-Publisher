from __future__ import annotations

import re
from pathlib import Path
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page, TimeoutError

from .content import PlatformContent, PostContent
from .xiaohongshu import PublisherError, _run_step


HOME_URL = "https://mp.weixin.qq.com/"


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
                return {opacity, pointerEvents, visibility};
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


def _first_interactable(locator: Locator) -> Locator | None:
    try:
        count = locator.count()
    except Exception:
        return None
    for index in range(count):
        candidate = locator.nth(index)
        if _is_interactable(candidate):
            return candidate
    return None


def _wait_for_dom(page: Page) -> None:
    try:
        page.wait_for_load_state("domcontentloaded", timeout=30_000)
    except TimeoutError:
        pass
    page.wait_for_function(
        """
        () => !!document.body && document.body.innerText.trim().length > 0
        """,
        timeout=30_000,
    )


def _visible_text(page: Page) -> str:
    try:
        return page.locator("body").inner_text(timeout=2_000)
    except Exception:
        return ""


def _is_login_page(page: Page) -> bool:
    if "/login" in page.url.lower():
        return True
    text = _visible_text(page)
    if "微信扫一扫，选择公众平台账号登录" in text:
        return True
    if _first_interactable(page.locator('input[placeholder="邮箱/微信号"]')):
        return True
    return _first_interactable(
        page.get_by_text("使用账号登录", exact=True)
    ) is not None and _first_interactable(page.get_by_text("登录", exact=True)) is not None


def _is_account_selection_page(page: Page) -> bool:
    text = _visible_text(page)
    return bool(re.search(r"选择公众号|选择账号|请选择公众号", text)) and not re.search(
        r"新的创作|内容管理|Alark知新录", text
    )


def _navigate_home(page: Page) -> None:
    """Navigate to WeChat and tolerate its post-login dashboard redirect."""

    try:
        # WeChat commonly redirects / to /cgi-bin/home after login. Waiting
        # for DOMContentLoaded on the first URL can race with that redirect.
        page.goto(HOME_URL, wait_until="commit", timeout=60_000)
    except PlaywrightError as exc:
        message = str(exc)
        if (
            "interrupted by another navigation" not in message
            or not page.url.startswith(HOME_URL)
        ):
            raise
    _wait_for_dom(page)


def _check_login(page: Page) -> None:
    _navigate_home(page)
    if _is_login_page(page):
        print("WeChat Official Account is not logged in.")
        print()
        print("Please complete login manually in the browser.")
        print("This may require scanning a QR code in WeChat.")
        print()
        print("Press Enter after login is complete...")
        input()
        _navigate_home(page)
        if _is_login_page(page):
            raise PublisherError("Checking WeChat login", "Login was not completed.")

    if _is_account_selection_page(page):
        print("Multiple WeChat Official Accounts may be available.")
        print()
        print("Please select the target account manually in the browser.")
        print()
        print("Press Enter after entering the target account dashboard...")
        input()
        _navigate_home(page)
        if _is_login_page(page) or _is_account_selection_page(page):
            raise PublisherError(
                "Checking WeChat login",
                "Login was not completed or a target account was not selected.",
            )


def _close_known_popups(page: Page) -> None:
    # These are informational overlays only. No content or publishing control
    # is clicked here.
    for text in ("我知道了", "知道了"):
        candidate = _first_interactable(page.get_by_text(text, exact=True))
        if candidate is not None:
            candidate.click()


def _open_sticker_editor(page: Page) -> Page:
    _close_known_popups(page)
    sticker = _first_interactable(page.get_by_text("贴图", exact=True))
    if sticker is None:
        raise PublisherError(
            "Opening WeChat sticker editor",
            "Could not find the visible WeChat '贴图' creation entry.",
        )

    try:
        with page.expect_popup(timeout=15_000) as popup_info:
            sticker.click()
        editor = popup_info.value
    except TimeoutError:
        # Some account/page versions reuse the current tab instead of opening
        # a new one.
        editor = page

    _wait_for_dom(editor)
    try:
        editor.wait_for_url(re.compile(r"type=77|createType=8"), timeout=30_000)
    except TimeoutError:
        pass
    try:
        editor.get_by_text("支持添加话题卡片", exact=True).first.wait_for(
            state="visible", timeout=3_000
        )
    except TimeoutError:
        pass
    _close_known_popups(editor)

    sticker_drop_zone = _first_interactable(
        editor.get_by_text(re.compile(r"选择或拖拽图片|到此处"))
    )
    title_editor = _first_interactable(
        editor.locator(
            '[contenteditable="true"][data-placeholder*="请在这里输入标题"]'
        )
    )
    if sticker_drop_zone is None or title_editor is None:
        visible_article_body = _first_interactable(
            editor.get_by_text("从这里开始写正文", exact=True)
        )
        if visible_article_body is not None:
            raise PublisherError(
                "Opening WeChat sticker editor",
                "Could not locate WeChat sticker/image-post editor. "
                "The current page appears to be the traditional article editor.",
            )
        raise PublisherError(
            "Opening WeChat sticker editor",
            "Could not locate WeChat sticker/image-post editor.",
        )
    return editor


def _title_locator(page: Page) -> Locator:
    selectors = [
        '[contenteditable="true"][data-placeholder*="请在这里输入标题"]',
        'textarea#title',
        'input[placeholder*="标题"]',
    ]
    for selector in selectors:
        candidate = _first_interactable(page.locator(selector))
        if candidate is not None:
            return candidate
    raise PublisherError(
        "Filling WeChat title and content",
        "Could not find the visible WeChat sticker title editor.",
    )


def _description_locator(page: Page) -> Locator:
    candidates = page.locator('[contenteditable="true"].ProseMirror')
    for index in range(candidates.count()):
        candidate = candidates.nth(index)
        if not _is_interactable(candidate):
            continue
        if candidate.get_attribute("data-placeholder"):
            continue
        return candidate

    for selector in (
        '[contenteditable="true"][data-placeholder*="描述"]',
        'textarea[placeholder*="描述"]',
        'textarea[placeholder*="说点什么"]',
    ):
        candidate = _first_interactable(page.locator(selector))
        if candidate is not None:
            return candidate
    raise PublisherError(
        "Filling WeChat title and content",
        "Could not find the visible WeChat sticker description editor.",
    )


def _read_value(locator: Locator) -> str:
    try:
        return locator.input_value()
    except Exception:
        try:
            return locator.inner_text()
        except Exception:
            return locator.text_content() or ""


def _compact_text(value: str) -> str:
    return re.sub(r"\s+", "", value.replace("\u00a0", " "))


def _fill_editable(locator: Locator, value: str, page: Page) -> None:
    try:
        locator.fill(value, timeout=15_000)
    except Exception:
        locator.click()
        locator.press("ControlOrMeta+A")
        locator.press("Backspace")
        page.keyboard.insert_text(value)


def _fill_text(page: Page, content: PlatformContent) -> None:
    title = _title_locator(page)
    _fill_editable(title, content.title, page)
    if _read_value(title).strip() != content.title:
        raise PublisherError(
            "Filling WeChat title and content",
            "The WeChat title was not accepted exactly as provided.",
        )

    description = _description_locator(page)
    _fill_editable(description, content.body, page)
    if content.body and _compact_text(content.body) not in _compact_text(
        _read_value(description)
    ):
        raise PublisherError(
            "Filling WeChat title and content",
            "The WeChat description editor did not contain the provided content.",
        )

    validation_text = _visible_text(page)
    if re.search(
        r"标题.{0,16}(超出|过长|最多|限制)|描述.{0,16}(超出|过长|最多|限制)",
        validation_text,
    ):
        raise PublisherError(
            "Filling WeChat title and content",
            "The page reported a WeChat title or description length/validation problem.",
        )


def _image_file_input(page: Page) -> Locator:
    preferred_inputs = page.locator(
        '.js_upload_btn_container input[type="file"]'
    )
    for index in range(preferred_inputs.count()):
        candidate = preferred_inputs.nth(index)
        accept = (candidate.get_attribute("accept") or "").lower()
        if "image" in accept:
            return candidate

    inputs = page.locator('input[type="file"]')
    for index in range(inputs.count() - 1, -1, -1):
        candidate = inputs.nth(index)
        accept = (candidate.get_attribute("accept") or "").lower()
        if "image" in accept:
            return candidate
    raise PublisherError(
        "Uploading WeChat images",
        "Could not find the WeChat sticker image upload input.",
    )


def _image_count_from_text(text: str) -> int | None:
    match = re.search(r"(?<!\d)(\d+)\s*/\s*\d+", text)
    return int(match.group(1)) if match else None


def _check_upload_errors(page: Page) -> None:
    text = _visible_text(page)
    if re.search(r"上传失败|格式错误|尺寸错误|文件过大|upload failed", text, re.I):
        raise PublisherError(
            "Uploading WeChat images",
            "The WeChat page reported an image upload failure, format error, size error, or file-size error.",
        )
    if re.search(r"图片数量.{0,12}(超过|最多)|最多上传|超过上限", text):
        raise PublisherError(
            "Uploading WeChat images",
            "WeChat rejected the supplied image count. The images were not automatically truncated. Please inspect the page manually.",
        )


def _wait_for_uploads(page: Page, expected_count: int) -> None:
    try:
        page.wait_for_function(
            """
            expected => {
                const text = document.body ? document.body.innerText : '';
                const busy = /(上传中|正在上传|uploading)/i.test(text);
                const loading = Array.from(document.querySelectorAll(
                    '[class*="loading"], [aria-busy="true"]'
                )).some(element => {
                    const rect = element.getBoundingClientRect();
                    const style = getComputedStyle(element);
                    return style.display !== 'none' && style.visibility !== 'hidden' &&
                        rect.width > 0 && rect.height > 0;
                });
                const thumbnails = document.querySelectorAll(
                    '.image-selector__bottom-list-item'
                ).length;
                return thumbnails >= expected && !busy && !loading;
            }
            """,
            arg=expected_count,
            timeout=120_000,
        )
    except TimeoutError as exc:
        _check_upload_errors(page)
        raise PublisherError(
            "Uploading WeChat images",
            f"The WeChat sticker editor did not confirm {expected_count} uploaded images.",
        ) from exc
    _check_upload_errors(page)


def _upload_images(page: Page, images: tuple[Path, ...]) -> None:
    file_input = _image_file_input(page)
    paths = [str(image.resolve()) for image in images]
    if file_input.get_attribute("multiple") is not None:
        file_input.set_input_files(paths, timeout=30_000)
    else:
        for path in paths:
            file_input.set_input_files(path, timeout=30_000)
    _wait_for_uploads(page, len(images))


def run_wechat(page: Page, post: PostContent) -> Page:
    """Fill the WeChat sticker/image-post editor without publishing."""
    print("[13/17] Checking WeChat login...")
    _run_step("Checking WeChat login", lambda: _check_login(page))

    print("[14/17] Opening WeChat sticker editor...")
    editor = _run_step("Opening WeChat sticker editor", lambda: _open_sticker_editor(page))

    print(f"[15/17] Uploading {len(post.wechat.images)} WeChat images...")
    _run_step(
        "Uploading WeChat images",
        lambda: _upload_images(editor, post.wechat.images),
    )

    print("[16/17] Filling WeChat title and content...")
    _run_step(
        "Filling WeChat title and content",
        lambda: _fill_text(editor, post.wechat),
    )
    return editor
