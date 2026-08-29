from __future__ import annotations

import re
from pathlib import Path

from playwright.sync_api import Locator, Page, TimeoutError

from .content import PlatformContent, PostContent
from .renderer import RenderedContent, render_for_platform
from .xiaohongshu import PublisherError, _run_step


LOGIN_URL = "https://baijiahao.baidu.com/builder/theme/bjh/login"
HOME_URL = "https://baijiahao.baidu.com/builder/rc/home"
EDITOR_URL = "https://baijiahao.baidu.com/builder/rc/edit?type=news"


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
                return { opacity, pointerEvents, visibility };
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


def _visible_text(page: Page) -> str:
    try:
        return page.locator("body").inner_text(timeout=2_000)
    except Exception:
        return ""


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


def _is_login_page(page: Page) -> bool:
    if re.search(r"/login|passport", page.url, re.IGNORECASE):
        return True
    return _first_interactable(
        page.get_by_role("button", name=re.compile(r"登录/注册百家号|登录"))
    ) is not None


def _check_login(page: Page) -> None:
    page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60_000)
    _wait_for_dom(page)
    if not _is_login_page(page):
        return

    # Login is intentionally manual. The persistent browser context keeps the
    # resulting session for later runs.
    print("Baijiahao is not logged in.")
    print("Please complete login manually in the browser.")
    print("Press Enter after login is complete...")
    input()

    page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60_000)
    _wait_for_dom(page)
    if _is_login_page(page):
        raise PublisherError(
            "Checking Baijiahao login",
            "Login was not completed. The page is still showing the login screen.",
        )


def _close_tour_popups(page: Page) -> None:
    close_selectors = [
        ".cheetah-tour-close",
        ".cheetah-tour-skip",
        ".cheetah-modal-close",
        ".ant-modal-close",
        '[aria-label="关闭"]',
        '[aria-label="Close"]',
    ]
    dialog = page.locator(
        '[role="dialog"], .cheetah-tour, .cheetah-modal, .ant-modal'
    )
    for selector in close_selectors:
        candidate = _first_interactable(dialog.locator(selector))
        if candidate is not None:
            candidate.click()

    for text in ("跳过", "知道了", "我知道了", "完成"):
        candidate = _first_interactable(
            dialog.get_by_text(text, exact=True)
        )
        if candidate is not None:
            candidate.click()


def _open_editor(page: Page) -> None:
    page.goto(EDITOR_URL, wait_until="domcontentloaded", timeout=60_000)
    _wait_for_dom(page)
    if _is_login_page(page):
        raise PublisherError(
            "Opening Baijiahao editor",
            "Baijiahao redirected back to the login page.",
        )
    _close_tour_popups(page)


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
        '#newsTextArea [contenteditable="true"]',
        '[contenteditable="true"][data-lexical-editor="true"]',
        '[contenteditable="true"][data-placeholder*="标题"]',
        '[contenteditable="true"][aria-label*="标题"]',
        'input[placeholder*="请输入标题"]',
        'textarea[placeholder*="请输入标题"]',
        'input[placeholder*="标题"]',
        'textarea[placeholder*="标题"]',
        ".title-input",
    ]
    _wait_for_interactable_selector(page, selectors)
    locator = _first_interactable(page.locator(", ".join(selectors)))
    if locator is None:
        raise PublisherError(
            "Filling Baijiahao title",
            "Could not find an interactable Baijiahao title editor.",
        )
    return locator


def _read_locator_value(locator: Locator) -> str:
    try:
        return locator.input_value()
    except Exception:
        try:
            return locator.inner_text()
        except Exception:
            return locator.text_content() or ""


def _fill_editable(locator: Locator, value: str, page: Page) -> None:
    try:
        locator.fill(value, timeout=15_000)
        return
    except Exception:
        locator.click()
        locator.press("ControlOrMeta+A")
        locator.press("Backspace")
        page.keyboard.insert_text(value)


def _fill_title(page: Page, content: PlatformContent) -> None:
    title = _title_locator(page)
    _fill_editable(title, content.title, page)
    if _read_locator_value(title).strip() != content.title:
        raise PublisherError(
            "Filling Baijiahao title",
            "The Baijiahao title was not accepted exactly as provided; it was not modified.",
        )


def _frame_editor_locator(page: Page) -> Locator | None:
    # The historical editor is UEditor in #ueditor_0. Newer versions may use
    # another iframe id, so inspect same-origin frames for an editable body.
    for frame in page.frames:
        if frame == page.main_frame:
            continue
        candidates = [
            frame.locator('body[contenteditable="true"]'),
            frame.locator('[contenteditable="true"]'),
        ]
        for candidate in candidates:
            editor = _first_interactable(candidate)
            if editor is not None:
                return editor

        body = frame.locator("body")
        try:
            if _is_interactable(body) and re.search(
                r"ueditor|editor|news",
                f"{frame.url} {body.get_attribute('class') or ''}",
                re.IGNORECASE,
            ):
                return body
        except Exception:
            continue
    return None


def _body_locator(page: Page) -> Locator:
    iframe_editor = _frame_editor_locator(page)
    if iframe_editor is not None:
        return iframe_editor

    selectors = [
        '[contenteditable="true"][data-placeholder*="正文"]',
        '[contenteditable="true"][aria-label*="正文"]',
        '.ProseMirror[contenteditable="true"]',
        '[data-lexical-editor="true"]',
    ]
    _wait_for_interactable_selector(page, selectors)
    locator = _first_interactable(page.locator(", ".join(selectors)))
    if locator is None:
        raise PublisherError(
            "Filling Baijiahao content",
            "Could not find an interactable Baijiahao body editor or editor iframe.",
        )
    return locator


def _compact_text(value: str) -> str:
    return re.sub(r"\s+", "", value.replace("\u00a0", " "))


def _editor_validation_text(value: str) -> str:
    # Rich editors usually expose link text but not the URL in inner_text().
    # Ignore renderer-added list and quote markers for the text-presence check.
    value = re.sub(r"（(?:https?|mailto):[^）]*）", "", value)
    value = re.sub(r"(?m)^\s*(?:•|\d+\.)\s+", "", value)
    return _compact_text(value.replace("「", "").replace("」", ""))


def _inject_html(body: Locator, rendered: RenderedContent) -> None:
    if not rendered.html:
        raise PublisherError(
            "Filling Baijiahao content",
            "The Baijiahao renderer did not produce rich-text HTML.",
        )

    html = rendered.html
    try:
        body.evaluate(
            """
            (element, value) => {
                element.focus();
                element.innerHTML = value;
                // Do not identify this as insertFromPaste. Baijiahao treats
                // that input type as structured-paste mode and disables the
                // editor's image insertion control while it is active.
                element.dispatchEvent(new Event('input', { bubbles: true }));
                element.dispatchEvent(new Event('change', { bubbles: true }));
            }
            """,
            html,
        )
    except Exception as exc:
        # Some UEditor versions implement insertHTML more reliably than an
        # innerHTML assignment. The fallback still uses only renderer output.
        try:
            body.evaluate(
                """
                (element, value) => {
                    element.focus();
                    document.execCommand('selectAll', false, null);
                    document.execCommand('insertHTML', false, value);
                    element.dispatchEvent(new Event('input', { bubbles: true }));
                    element.dispatchEvent(new Event('change', { bubbles: true }));
                }
                """,
                html,
            )
        except Exception as fallback_exc:
            raise PublisherError(
                "Filling Baijiahao content",
                "Could not inject rendered HTML into the Baijiahao editor: "
                f"{fallback_exc}",
            ) from exc


def _has_bold_semantics(body: Locator) -> bool:
    if body.locator("strong, b").count() > 0:
        return True
    try:
        return bool(
            body.evaluate(
                """
                element => Array.from(element.querySelectorAll('*')).some(node => {
                    const weight = getComputedStyle(node).fontWeight;
                    return weight === 'bold' || Number(weight) >= 600;
                })
                """
            )
        )
    except Exception:
        return False


def _has_list_semantics(body: Locator) -> bool:
    if body.locator("ul, ol, li").count() > 0:
        return True
    try:
        return bool(
            body.evaluate(
                """
                element => Array.from(element.querySelectorAll('*')).some(node => {
                    return getComputedStyle(node).listStyleType !== 'none';
                })
                """
            )
        )
    except Exception:
        return False


def _fill_body(page: Page, body: Locator, content: PlatformContent) -> None:
    rendered = render_for_platform("baijiahao", content.body)
    _inject_html(body, rendered)
    actual = _read_locator_value(body)
    if rendered.text and _editor_validation_text(rendered.text) not in _editor_validation_text(actual):
        raise PublisherError(
            "Filling Baijiahao content",
            "The Baijiahao editor did not contain the provided content after filling.",
        )
    if "<strong>" in (rendered.html or "") and not _has_bold_semantics(body):
        raise PublisherError(
            "Filling Baijiahao content",
            "The Baijiahao editor did not preserve the rendered bold semantic.",
        )
    if ("<ul>" in (rendered.html or "") or "<ol>" in (rendered.html or "")) and not _has_list_semantics(body):
        raise PublisherError(
            "Filling Baijiahao content",
            "The Baijiahao editor did not preserve the rendered list semantic.",
        )


def _image_file_inputs(page: Page) -> list[Locator]:
    result: list[Locator] = []
    # The upload control may live in the main document or in an editor iframe.
    for frame in page.frames:
        inputs = frame.locator('input[type="file"]')
        for index in range(inputs.count()):
            candidate = inputs.nth(index)
            accept = (candidate.get_attribute("accept") or "").lower()
            if not accept or "image" in accept or ".png" in accept:
                result.append(candidate)
    return result


def _is_enabled_control(locator: Locator) -> bool:
    try:
        return (
            locator.is_enabled()
            and locator.get_attribute("disabled") is None
            and locator.get_attribute("aria-disabled") != "true"
        )
    except Exception:
        return False


def _image_trigger(page: Page) -> Locator | None:
    selectors = [
        'button[aria-label*="图片"]',
        '[role="button"][aria-label*="图片"]',
        'button[title*="图片"]',
        '[title*="插入图片"]',
        '[data-tip*="图片"]',
        '[class*="insertimage"]',
        '[class*="insertImage"]',
        '.edui-for-insertimage',
        'button:has-text("插入图片")',
        'button:has-text("图片")',
    ]
    for selector in selectors:
        candidate = _first_interactable(page.locator(selector))
        if candidate is not None and _is_enabled_control(candidate):
            return candidate
    return None


def _wait_for_upload_finish(page: Page, expected_count: int) -> None:
    page.wait_for_function(
        """
        expected => {
            const text = document.body ? document.body.innerText : '';
            const success = new RegExp(`${expected}\\s*张上传成功`).test(text);
            const uploading = /(上传中|正在上传|uploading)/i.test(text);
            const loading = Array.from(
                document.querySelectorAll('img[alt="loading"]')
            ).some(element => {
                const rect = element.getBoundingClientRect();
                const style = getComputedStyle(element);
                return style.display !== 'none' && style.visibility !== 'hidden' &&
                    rect.width > 0 && rect.height > 0;
            });
            return success && !uploading && !loading;
        }
        """,
        arg=expected_count,
        timeout=120_000,
    )
    if re.search(r"上传失败|上传错误|upload failed", _visible_text(page), re.IGNORECASE):
        raise PublisherError(
            "Uploading Baijiahao images",
            "The Baijiahao page reported an image upload failure.",
        )


def _editor_image_count(editor: Locator) -> int:
    try:
        return editor.locator("img").count()
    except Exception:
        return 0


def _select_uploaded_thumbnails(page: Page, expected_count: int) -> None:
    thumbnail_locators = [
        page.locator(".image.cheetah-ui-pro-base-image-aspect-fill"),
        page.locator("[class*='cheetah-ui-pro-base-image-aspect-fill']"),
    ]
    selected = 0
    for thumbnails in thumbnail_locators:
        for index in range(thumbnails.count()):
            candidate = thumbnails.nth(index)
            try:
                if not candidate.is_visible():
                    continue
                box = candidate.bounding_box()
                if box is None or box["width"] < 70 or box["height"] < 45:
                    continue
                candidate.click(force=True)
                selected += 1
                if selected >= expected_count:
                    return
            except Exception:
                continue

    raise PublisherError(
        "Uploading Baijiahao images",
        f"The image dialog reported {expected_count} uploaded images, "
        f"but only {selected} selectable thumbnails were found.",
    )


def _wait_for_editor_images(page: Page, editor: Locator, minimum: int) -> None:
    deadline = 120_000
    elapsed = 0
    while elapsed < deadline:
        if _editor_image_count(editor) >= minimum:
            return
        page.wait_for_timeout(500)
        elapsed += 500
    raise PublisherError(
        "Uploading Baijiahao images",
        f"The editor did not show {minimum} inserted images within 120 seconds.",
    )


def _click_local_upload_tab(page: Page) -> None:
    for frame in page.frames:
        dialog = frame.locator('[role="dialog"], .cheetah-modal, .ant-modal')
        for text in ("本地上传", "上传图片", "本地图片"):
            candidate = _first_interactable(dialog.get_by_text(text, exact=True))
            if candidate is not None:
                candidate.click()
                return


def _wait_for_image_file_input(page: Page, timeout: int = 15_000) -> Locator:
    elapsed = 0
    while elapsed < timeout:
        inputs = _image_file_inputs(page)
        if inputs:
            return inputs[-1]
        page.wait_for_timeout(250)
        elapsed += 250
    raise TimeoutError("No image file input appeared.")


def _set_image_files(file_input: Locator, paths: list[str]) -> None:
    if file_input.get_attribute("multiple") is not None:
        file_input.set_input_files(paths, timeout=30_000)
        return
    if len(paths) != 1:
        raise PublisherError(
            "Uploading Baijiahao images",
            "The Baijiahao image input accepts only one file at a time.",
        )
    file_input.set_input_files(paths[0], timeout=30_000)


def _set_native_file_chooser_images(
    page: Page,
    trigger: Locator,
    paths: list[str],
) -> bool:
    """Handle editors whose image button opens the browser file chooser."""

    try:
        with page.expect_file_chooser(timeout=3_000) as chooser_info:
            trigger.click()
        chooser = chooser_info.value
    except TimeoutError:
        return False

    if chooser.is_multiple:
        chooser.set_files(paths, timeout=30_000)
        return True

    chooser.set_files(paths[0], timeout=30_000)
    for path in paths[1:]:
        with page.expect_file_chooser(timeout=15_000) as next_chooser_info:
            trigger.click()
        next_chooser_info.value.set_files(path, timeout=30_000)
    return True


def _confirm_image_dialog(page: Page) -> None:
    candidates = []
    for text in ("确认", "确定", "插入", "完成"):
        candidates.append(
            page.locator("button.cheetah-btn-primary").filter(
                has_text=re.compile(f"^{text}$")
            )
        )
        candidates.append(page.get_by_role("button", name=text, exact=True))

    def dialog_still_visible() -> bool:
        text = _visible_text(page)
        return "本地图片" in text and "确认" in text

    precise_confirm = page.locator("button.cheetah-btn-primary").filter(
        has_text=re.compile("^确认$")
    )
    for index in range(precise_confirm.count()):
        candidate = precise_confirm.nth(index)
        try:
            if not candidate.is_visible() or not candidate.is_enabled():
                continue
            candidate.scroll_into_view_if_needed()
            box = candidate.bounding_box()
            if box is None or box["width"] <= 0 or box["height"] <= 0:
                continue
            page.mouse.click(
                box["x"] + box["width"] / 2,
                box["y"] + box["height"] / 2,
            )
            page.wait_for_timeout(800)
            if not dialog_still_visible():
                return
        except Exception:
            continue

    for candidate_locator in candidates:
        candidate = _first_interactable(candidate_locator)
        if candidate is not None:
            try:
                candidate.click(force=True)
                if dialog_still_visible():
                    candidate.press("Enter")
            except Exception:
                continue
            page.wait_for_timeout(500)
            if not dialog_still_visible():
                return

    raise PublisherError(
        "Uploading Baijiahao images",
        "The uploaded images dialog stayed open; could not click its confirmation control.",
    )


def _insert_images(page: Page, editor: Locator, images: tuple[Path, ...]) -> None:
    before_count = _editor_image_count(editor)
    try:
        page.locator(".edui-for-insertimage").first.wait_for(
            state="visible", timeout=60_000
        )
    except TimeoutError:
        # Fall through to the semantic/CSS selector list below so that a
        # different editor version can still provide its own image control.
        pass
    trigger = _image_trigger(page)
    if trigger is None:
        raise PublisherError(
            "Uploading Baijiahao images",
            "Could not find the editor's image insertion control.",
        )

    paths = [str(image.resolve()) for image in images]
    if _set_native_file_chooser_images(page, trigger, paths):
        # Direct image controls insert into the editor after the selected files
        # finish uploading; there is no modal thumbnail/confirm workflow.
        _wait_for_editor_images(page, editor, before_count + len(images))
        return

    _click_local_upload_tab(page)
    try:
        file_input = _wait_for_image_file_input(page)
    except TimeoutError as exc:
        raise PublisherError(
            "Uploading Baijiahao images",
            "No Baijiahao image file input appeared after opening the image controls. "
            "The editor may have changed its upload control.",
        ) from exc

    _set_image_files(file_input, paths)
    _wait_for_upload_finish(page, len(images))
    _select_uploaded_thumbnails(page, len(images))
    _confirm_image_dialog(page)
    _wait_for_editor_images(page, editor, before_count + len(images))


def run_baijiahao(page: Page, post: PostContent) -> None:
    """Fill a Baijiahao article and insert its manifest images without publishing."""
    print("[8/17] Checking Baijiahao login...")
    _run_step("Checking Baijiahao login", lambda: _check_login(page))

    print("[9/17] Opening Baijiahao editor...")
    _run_step("Opening Baijiahao editor", lambda: _open_editor(page))

    print("[10/17] Filling Baijiahao title and content...")
    body: Locator | None = None

    def fill_content() -> None:
        nonlocal body
        _fill_title(page, post.baijiahao)
        body = _body_locator(page)
        _fill_body(page, body, post.baijiahao)

    _run_step("Filling Baijiahao title and content", fill_content)
    if body is None:
        raise PublisherError("Filling Baijiahao content", "Body editor was not retained.")

    print(
        f"[11/17] Uploading {len(post.baijiahao.images)} Baijiahao images into content..."
    )
    _run_step(
        "Uploading Baijiahao images",
        lambda: _insert_images(page, body, post.baijiahao.images),
    )
