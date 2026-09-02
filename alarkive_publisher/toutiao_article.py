"""Toutiao article publisher.

This adapter prepares an article in the current Toutiao creator editor.  It
consumes only ``post.public_long``; the Package and routing layers remain
platform-neutral.  Login is intentionally manual and the final publish action
is never performed here.
"""

from __future__ import annotations

import re
from pathlib import Path

from playwright.sync_api import Locator, Page, TimeoutError

from .content import PlatformContent, PostContent
from .inline_images import (
    ContentBlock,
    ImageBlock,
    TextBlock,
    append_unused_images,
    inline_image_error_for_label,
    validate_inline_image_text,
)
from .renderer import RenderedContent, render_for_platform
from .workflow_controller import CLIWorkflowController, WorkflowController
from .xiaohongshu import PublisherError, _run_step


HOME_URL = "https://mp.toutiao.com/profile_v4/"
EDITOR_URL = "https://mp.toutiao.com/profile_v4/graphic/publish"


def _is_interactable(locator: Locator) -> bool:
    try:
        if not locator.is_visible():
            return False
        box = locator.bounding_box()
        if box is None or box["width"] <= 0 or box["height"] <= 0:
            return False
        return bool(
            locator.evaluate(
                """
                element => {
                    let current = element;
                    while (current && current.nodeType === Node.ELEMENT_NODE) {
                        const style = getComputedStyle(current);
                        if (style.display === 'none' || style.visibility === 'hidden' ||
                            style.pointerEvents === 'none' || Number(style.opacity) < 0.1) {
                            return false;
                        }
                        current = current.parentElement;
                    }
                    return true;
                }
                """
            )
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
        "() => !!document.body && document.body.innerText.trim().length > 0",
        timeout=30_000,
    )


def _is_login_page(page: Page) -> bool:
    if re.search(r"/auth/|/login|passport", page.url, re.IGNORECASE):
        return True
    phone = _first_interactable(
        page.locator('input[placeholder*="手机号"], input[placeholder*="手机"]')
    )
    code = _first_interactable(
        page.locator('input[placeholder*="验证码"], input[placeholder*="验证"]')
    )
    return phone is not None and code is not None


def _check_login(page: Page, controller: WorkflowController) -> None:
    page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60_000)
    _wait_for_dom(page)
    if not _is_login_page(page):
        return

    # The user completes login in the visible persistent Chrome profile.  No
    # account, password, SMS code, or cookie is ever supplied by this module.
    controller.wait_for_user(
        "toutiao_article",
        "login",
        "需要登录今日头条。请在打开的 Chrome 浏览器中完成登录。",
        "登录完成后继续。",
    )
    page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60_000)
    _wait_for_dom(page)
    if _is_login_page(page):
        raise PublisherError(
            "Checking Toutiao article login",
            "Login was not completed. The page is still showing the Toutiao login screen.",
        )


def _close_dialogs(page: Page) -> None:
    """Close non-content onboarding overlays without touching publish controls."""

    roots = page.locator(
        '[role="dialog"], .ant-modal, [class*="modal"], '
        '[class*="popover"], [class*="tooltip"]'
    )
    for label in ("跳过", "知道了", "我知道了", "完成"):
        candidate = _first_interactable(roots.get_by_text(label, exact=True))
        if candidate is None:
            candidate = _first_interactable(page.get_by_text(label, exact=True))
        if candidate is not None:
            try:
                candidate.click()
            except Exception:
                continue


def _open_editor(page: Page) -> None:
    page.goto(EDITOR_URL, wait_until="domcontentloaded", timeout=60_000)
    _wait_for_dom(page)
    if _is_login_page(page):
        raise PublisherError(
            "Opening Toutiao article editor",
            "Toutiao redirected back to the login page.",
        )
    _close_dialogs(page)


def _wait_for_interactable_selector(page: Page, selectors: list[str]) -> None:
    page.wait_for_function(
        """
        selectors => selectors.some(selector => Array.from(document.querySelectorAll(selector)).some(element => {
            const rect = element.getBoundingClientRect();
            const style = getComputedStyle(element);
            return rect.width > 0 && rect.height > 0 && style.display !== 'none' &&
                style.visibility !== 'hidden' && style.pointerEvents !== 'none';
        }))
        """,
        arg=selectors,
        timeout=60_000,
    )


def _title_locator(page: Page) -> Locator:
    selectors = [
        'input[placeholder*="标题"]',
        'textarea[placeholder*="标题"]',
        '[contenteditable="true"][data-placeholder*="标题"]',
        '[contenteditable="true"][aria-label*="标题"]',
        '[role="textbox"][aria-label*="标题"]',
        '[data-testid*="title"]',
    ]
    _wait_for_interactable_selector(page, selectors)
    locator = _first_interactable(page.locator(", ".join(selectors)))
    if locator is None:
        raise PublisherError(
            "Filling Toutiao article title",
            "Could not find an interactable Toutiao article title input.",
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
            "Filling Toutiao article title",
            "The Toutiao article title was not accepted exactly as provided.",
        )


def _frame_editor_locator(page: Page) -> Locator | None:
    for frame in page.frames:
        if frame == page.main_frame:
            continue
        for selector in (
            '[contenteditable="true"][data-placeholder*="正文"]',
            '[contenteditable="true"][aria-label*="正文"]',
            '.ProseMirror[contenteditable="true"]',
            '[data-lexical-editor="true"]',
            'body[contenteditable="true"]',
        ):
            editor = _first_interactable(frame.locator(selector))
            if editor is not None:
                return editor
    return None


def _body_locator(page: Page) -> Locator:
    editor = _frame_editor_locator(page)
    if editor is not None:
        return editor
    selectors = [
        '[contenteditable="true"][data-placeholder*="正文"]',
        '[contenteditable="true"][aria-label*="正文"]',
        '[contenteditable="true"][data-placeholder*="写下"]',
        '.ProseMirror[contenteditable="true"]',
        '[data-lexical-editor="true"]',
        '[role="textbox"][contenteditable="true"]',
    ]
    _wait_for_interactable_selector(page, selectors)
    editor = _first_interactable(page.locator(", ".join(selectors)))
    if editor is None:
        # The current editor exposes the visible placeholder as text but some
        # builds omit the corresponding data-placeholder/aria attribute.  As
        # a compatibility fallback, inspect generic editable roots and skip
        # the title editor by its own stable label/placeholder attributes.
        candidates = page.locator('[contenteditable="true"]')
        for index in range(candidates.count() - 1, -1, -1):
            candidate = candidates.nth(index)
            attributes = " ".join(
                str(candidate.get_attribute(attribute) or "")
                for attribute in ("aria-label", "placeholder", "data-placeholder")
            )
            if re.search(r"标题", attributes):
                continue
            if _is_interactable(candidate):
                editor = candidate
                break
    if editor is None:
        raise PublisherError(
            "Filling Toutiao article content",
            "Could not find an interactable Toutiao article body editor.",
        )
    return editor


def _compact_text(value: str) -> str:
    return re.sub(r"\s+", "", value.replace("\u00a0", " "))


def _editor_validation_text(value: str) -> str:
    value = re.sub(r"（(?:https?|mailto):[^）]*）", "", value)
    value = re.sub(r"(?m)^\s*(?:•|\d+\.)\s+", "", value)
    return _compact_text(value.replace("「", "").replace("」", ""))


def _paste_rendered_html(
    body: Locator,
    rendered: RenderedContent,
    *,
    replace: bool = True,
) -> None:
    if not rendered.html:
        return
    body.evaluate(
        """
        (element, value) => {
            element.focus();
            const selection = element.ownerDocument.getSelection();
            if (selection) {
                const range = element.ownerDocument.createRange();
                range.selectNodeContents(element);
                if (value.replace) {
                    selection.removeAllRanges();
                    selection.addRange(range);
                } else {
                    range.collapse(false);
                    selection.removeAllRanges();
                    selection.addRange(range);
                }
            }
            const transfer = new DataTransfer();
            transfer.setData('text/html', value.html);
            transfer.setData('text/plain', value.text || '');
            element.dispatchEvent(new ClipboardEvent('paste', {
                bubbles: true,
                cancelable: true,
                clipboardData: transfer,
            }));
        }
        """,
        {"html": rendered.html, "text": rendered.text, "replace": replace},
    )


def _is_prosemirror_editor(body: Locator) -> bool:
    try:
        attributes = " ".join(
            str(body.get_attribute(attribute) or "")
            for attribute in ("class", "data-testid", "data-editor")
        )
    except Exception:
        return False
    return "prosemirror" in attributes.lower()


def _inject_html(body: Locator, rendered: RenderedContent) -> None:
    if not rendered.html:
        raise PublisherError(
            "Filling Toutiao article content",
            "The Toutiao article renderer did not produce rich-text HTML.",
        )
    try:
        body.evaluate(
            """
            (element, value) => {
                element.focus();
                element.innerHTML = value;
                element.dispatchEvent(new Event('input', {bubbles: true}));
                element.dispatchEvent(new Event('change', {bubbles: true}));
            }
            """,
            rendered.html,
        )
    except Exception as exc:
        # ProseMirror builds commonly update their model through the browser's
        # paste handler.  Keep the direct DOM path first, then use a synthetic
        # rich-text paste as the compatibility fallback; both paths use only
        # renderer output and dispatch a model-visible input event.
        try:
            _paste_rendered_html(body, rendered)
        except Exception as fallback_exc:
            raise PublisherError(
                "Filling Toutiao article content",
                f"Could not inject rendered HTML into the Toutiao article editor: {fallback_exc}",
            ) from exc


def _append_rendered_html(body: Locator, rendered: RenderedContent) -> None:
    if not rendered.html:
        return
    if _is_prosemirror_editor(body):
        try:
            _focus_editor_for_image_insertion(body)
            _paste_rendered_html(body, rendered, replace=False)
            return
        except Exception:
            pass
    try:
        body.evaluate(
            """
            (element, value) => {
                element.focus();
                const template = element.ownerDocument.createElement('template');
                template.innerHTML = value;
                element.appendChild(template.content);
                const selection = element.ownerDocument.getSelection();
                if (selection) {
                    const range = element.ownerDocument.createRange();
                    range.selectNodeContents(element);
                    range.collapse(false);
                    selection.removeAllRanges();
                    selection.addRange(range);
                }
                element.dispatchEvent(new Event('input', {bubbles: true}));
                element.dispatchEvent(new Event('change', {bubbles: true}));
            }
            """,
            rendered.html,
        )
    except Exception as exc:
        raise PublisherError(
            "Filling Toutiao article content",
            f"Could not append rendered content to the Toutiao article editor: {exc}",
        ) from exc


def _clear_editor(body: Locator) -> None:
    try:
        body.click()
        body.press("ControlOrMeta+A")
        body.press("Backspace")
        return
    except Exception:
        pass
    try:
        body.evaluate(
            """
            element => {
                element.focus();
                element.innerHTML = '';
                element.dispatchEvent(new Event('input', {bubbles: true}));
                element.dispatchEvent(new Event('change', {bubbles: true}));
            }
            """
        )
    except Exception as exc:
        raise PublisherError(
            "Filling Toutiao article content",
            f"Could not clear the Toutiao article editor: {exc}",
        ) from exc


def _fill_body(page: Page, body: Locator, content: PlatformContent) -> None:
    rendered = render_for_platform("toutiao_article", content.body)
    if _is_prosemirror_editor(body):
        try:
            _clear_editor(body)
            _paste_rendered_html(body, rendered)
        except Exception:
            _inject_html(body, rendered)
    else:
        _inject_html(body, rendered)
    actual = _read_locator_value(body)
    if rendered.text and _editor_validation_text(rendered.text) not in _editor_validation_text(actual):
        try:
            _paste_rendered_html(body, rendered)
            actual = _read_locator_value(body)
        except Exception:
            pass
        if _editor_validation_text(rendered.text) not in _editor_validation_text(actual):
            try:
                _inject_html(body, rendered)
                actual = _read_locator_value(body)
            except Exception:
                pass
            if _editor_validation_text(rendered.text) not in _editor_validation_text(actual):
                raise PublisherError(
                    "Filling Toutiao article content",
                    "The Toutiao article editor did not contain the provided content after filling.",
                )


def _focus_editor_for_image_insertion(editor: Locator) -> None:
    try:
        editor.click()
    except Exception:
        pass
    try:
        editor.evaluate(
            """
            element => {
                element.focus();
                const selection = element.ownerDocument.getSelection();
                if (!selection) return;
                const range = element.ownerDocument.createRange();
                range.selectNodeContents(element);
                range.collapse(false);
                selection.removeAllRanges();
                selection.addRange(range);
                element.ownerDocument.dispatchEvent(new Event('selectionchange'));
            }
            """
        )
    except Exception:
        pass


def _image_trigger(page: Page) -> Locator | None:
    name = re.compile(r"(?:插入|添加|上传)?\s*(?:图片|图像|照片)|image", re.IGNORECASE)
    # Semantic labels are preferred.  Exclude cover/header controls so an
    # upload can only be initiated from the article editor toolbar.
    for frame in page.frames:
        candidates = [
            frame.get_by_role("button", name=name),
            frame.locator(
                '[aria-label*="图片"], [title*="图片"], '
                '[data-tooltip*="图片"], [data-tip*="图片"], [data-title*="图片"], '
                '[data-testid*="image"], [data-e2e*="image"], '
                '[data-action*="image"], [data-command*="image"], '
                '[class*="image-insert"], [class*="insert-image"], '
                '[class*="image-upload"]'
            ),
        ]
        for locator in candidates:
            try:
                count = locator.count()
            except Exception:
                continue
            for index in range(count):
                candidate = locator.nth(index)
                if not _is_interactable(candidate):
                    continue
                try:
                    if not candidate.is_enabled():
                        continue
                except Exception:
                    pass
                label = " ".join(
                    str(candidate.get_attribute(attribute) or "")
                    for attribute in (
                        "aria-label",
                        "title",
                        "data-tooltip",
                        "data-tip",
                        "data-title",
                        "data-testid",
                        "data-e2e",
                        "data-action",
                        "data-command",
                        "class",
                    )
                )
                if re.search(r"封面|头图|头像", label):
                    continue
                return candidate

        # Some current builds expose toolbar buttons with no accessible name
        # and put the icon identity only on a descendant SVG/use element.
        # Inspect that small, editor-scoped control set as a fallback instead
        # of relying on a positional button index.
        toolbar_buttons = frame.locator(
            '[role="toolbar"] button, [class*="toolbar"] button, '
            '[data-toolbar] button, [data-editor-toolbar] button'
        )
        for index in range(toolbar_buttons.count()):
            candidate = toolbar_buttons.nth(index)
            if not _is_interactable(candidate):
                continue
            try:
                label = " ".join(
                    str(candidate.get_attribute(attribute) or "")
                    for attribute in (
                        "aria-label",
                        "title",
                        "data-tooltip",
                        "data-tip",
                        "data-testid",
                        "data-e2e",
                        "data-action",
                        "data-command",
                        "class",
                    )
                )
                label += " " + candidate.inner_html()
            except Exception:
                continue
            if re.search(r"封面|头图|头像", label, re.IGNORECASE):
                continue
            if re.search(r"image|picture|photo|upload|图片|图像|照片", label, re.IGNORECASE):
                return candidate
    return None


def _image_file_inputs(page: Page) -> list[Locator]:
    inputs: list[Locator] = []
    for frame in page.frames:
        # An image dialog is the authoritative scope.  This prevents a
        # similarly hidden cover/头图 input elsewhere in the page from being
        # selected after the inline-image toolbar has been activated.
        roots = [
            frame.locator(
                '#upload-drag-input, '
                '[role="dialog"] input[type="file"], '
                '.ant-modal input[type="file"], '
                '[class*="modal"] input[type="file"], '
                '.byte-drawer input[type="file"]'
            ),
            frame.locator('input[type="file"]'),
        ]
        for locator in roots:
            scoped: list[Locator] = []
            for index in range(locator.count()):
                candidate = locator.nth(index)
                accept = (candidate.get_attribute("accept") or "").lower()
                if "image" in accept or ".png" in accept or ".jpg" in accept or not accept:
                    scoped.append(candidate)
            if scoped:
                inputs.extend(scoped)
                break
    return inputs


def _wait_for_image_file_input(page: Page) -> Locator:
    elapsed = 0
    while elapsed < 15_000:
        inputs = _image_file_inputs(page)
        if inputs:
            return inputs[-1]
        page.wait_for_timeout(250)
        elapsed += 250
    raise TimeoutError("No Toutiao image file input appeared after opening the editor control.")


def _wait_for_upload_complete(page: Page) -> None:
    page.wait_for_function(
        """
        () => {
            const root = document.body;
            if (!root) return false;
            const busy = Array.from(root.querySelectorAll(
                '[aria-busy="true"], [class*="loading"], [class*="uploading"]'
            )).some(element => {
                const style = getComputedStyle(element);
                const rect = element.getBoundingClientRect();
                return style.display !== 'none' && style.visibility !== 'hidden' &&
                    rect.width > 0 && rect.height > 0;
            });
            return !busy && !/(上传中|正在上传|处理中|uploading)/i.test(root.innerText || '');
        }
        """,
        timeout=120_000,
    )
    if re.search(r"上传失败|上传错误|upload failed", _visible_text(page), re.IGNORECASE):
        raise PublisherError(
            "Uploading Toutiao article image",
            "The Toutiao page reported an image upload failure.",
        )


def _confirm_inline_upload(page: Page) -> None:
    """Confirm only an image dialog, never a page-level publish action."""

    dialogs = page.locator(
        '[role="dialog"], .ant-modal, [class*="modal"], .byte-drawer'
    )
    for dialog_index in range(dialogs.count()):
        dialog = dialogs.nth(dialog_index)
        if not _is_interactable(dialog):
            continue
        candidate = _first_interactable(
            dialog.get_by_role(
                "button",
                name=re.compile(r"^(?:插入|确认|确定|完成)(?:图片|正文)?$"),
            )
        )
        if candidate is None:
            candidate = _first_interactable(
                dialog.get_by_text(
                    re.compile(r"^(?:插入|确认|确定|完成)(?:图片|正文)?$"),
                    exact=True,
                )
            )
        if candidate is not None:
            candidate.click()
            return


def _editor_image_count(editor: Locator) -> int:
    try:
        return editor.locator("img").count()
    except Exception:
        return 0


def _wait_for_editor_images(page: Page, editor: Locator, minimum: int) -> None:
    elapsed = 0
    while elapsed < 120_000:
        if _editor_image_count(editor) >= minimum:
            return
        page.wait_for_timeout(500)
        elapsed += 500
    raise PublisherError(
        "Uploading Toutiao article image",
        f"The editor did not show {minimum} inserted images within 120 seconds.",
    )


def _insert_image(page: Page, editor: Locator, image: Path) -> None:
    before_count = _editor_image_count(editor)
    trigger = _image_trigger(page)
    if trigger is None:
        raise PublisherError(
            "Uploading Toutiao article image",
            "Could not find the article editor's inline image control.",
        )
    _focus_editor_for_image_insertion(editor)
    path = str(image.resolve())

    try:
        with page.expect_file_chooser(timeout=4_000) as chooser_info:
            trigger.click()
        chooser_info.value.set_files(path, timeout=30_000)
    except TimeoutError:
        # The editor may open an in-page dialog rather than a native chooser.
        # The file input is selected only after the article image control was
        # activated, which keeps cover/attachment inputs out of this path.
        file_input = _wait_for_image_file_input(page)
        file_input.set_input_files(path, timeout=30_000)

    _wait_for_upload_complete(page)
    _confirm_inline_upload(page)
    _wait_for_editor_images(page, editor, before_count + 1)


def _inline_image_blocks(
    content: PlatformContent,
) -> tuple[tuple[ContentBlock, ...], bool]:
    blocks, validation = validate_inline_image_text(content.body, len(content.images))
    error = inline_image_error_for_label(validation, "Toutiao article")
    if error is not None:
        raise PublisherError("Parsing Toutiao article inline images", str(error))
    if not validation.has_markers:
        return blocks, False
    return append_unused_images(blocks, validation), True


def _inline_text_blocks_are_present(body: Locator, blocks: tuple[ContentBlock, ...]) -> bool:
    actual = _editor_validation_text(_read_locator_value(body))
    cursor = 0
    for block in blocks:
        if not isinstance(block, TextBlock):
            continue
        expected = _editor_validation_text(
            render_for_platform("toutiao_article", block.text).text
        )
        if not expected:
            continue
        position = actual.find(expected, cursor)
        if position < 0:
            return False
        cursor = position + len(expected)
    return True


def _fill_body_with_inline_images(
    page: Page,
    body: Locator,
    content: PlatformContent,
    blocks: tuple[ContentBlock, ...],
) -> None:
    for block in blocks:
        if isinstance(block, TextBlock):
            _append_rendered_html(body, render_for_platform("toutiao_article", block.text))
        else:
            _insert_image(page, body, content.images[block.index - 1])
    if not _inline_text_blocks_are_present(body, blocks):
        raise PublisherError(
            "Filling Toutiao article content",
            "The Toutiao article editor did not contain the provided text after inline image insertion.",
        )


def run_toutiao_article(
    page: Page,
    post: PostContent,
    controller: WorkflowController | None = None,
) -> None:
    """Prepare ``post.public_long`` in Toutiao's article editor.

    This function intentionally has no code path that locates or clicks a
    final ``发布``/``发表`` control.
    """

    if post.public_long is None:
        raise PublisherError(
            "Loading Toutiao article content",
            "The Package does not contain public_long content.",
        )
    controller = controller or CLIWorkflowController()
    content = post.public_long

    controller.step("toutiao_article", "checking_login", "检查登录")
    _run_step(
        "Checking Toutiao article login",
        lambda: _check_login(page, controller),
    )

    controller.step("toutiao_article", "opening_editor", "打开文章创作页")
    _run_step("Opening Toutiao article editor", lambda: _open_editor(page))

    controller.step("toutiao_article", "filling_content", "填写标题和正文")
    inline_blocks: tuple[ContentBlock, ...] = ()
    has_inline_images = False
    body: Locator | None = None

    def fill_content() -> None:
        nonlocal inline_blocks, has_inline_images, body
        inline_blocks, has_inline_images = _inline_image_blocks(content)
        _fill_title(page, content)
        body = _body_locator(page)
        if has_inline_images:
            _clear_editor(body)
        else:
            _fill_body(page, body, content)

    _run_step("Filling Toutiao article title and content", fill_content)
    if body is None:
        raise PublisherError("Filling Toutiao article content", "Body editor was not retained.")

    controller.step(
        "toutiao_article",
        "uploading_images",
        f"向正文插入 {len(content.images)} 张图片",
    )
    _run_step(
        "Uploading Toutiao article images",
        lambda: (
            _fill_body_with_inline_images(page, body, content, inline_blocks)
            if has_inline_images
            else _insert_images_at_end(page, body, content.images)
        ),
    )

    controller.step("toutiao_article", "final_check", "完成最终检查")


def _insert_images_at_end(page: Page, body: Locator, images: tuple[Path, ...]) -> None:
    """Preserve the public_long no-marker rule: append images in list order."""

    _focus_editor_for_image_insertion(body)
    for image in images:
        _insert_image(page, body, image)


__all__ = ["EDITOR_URL", "HOME_URL", "run_toutiao_article"]
