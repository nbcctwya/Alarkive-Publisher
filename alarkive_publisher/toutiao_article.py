"""Toutiao article publisher.

This adapter prepares an article in the current Toutiao creator editor.  It
consumes only ``post.public_long``; the Package and routing layers remain
platform-neutral.  Login is intentionally manual and the final publish action
is never performed here.
"""

from __future__ import annotations

import logging
import re
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Frame, Locator, Page, TimeoutError

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
from .publisher_common import PublisherError, run_step as _run_step


LOGGER = logging.getLogger(__name__)

HOME_URL = "https://mp.toutiao.com/profile_v4/"
EDITOR_URL = "https://mp.toutiao.com/profile_v4/graphic/publish"

_INLINE_UPLOAD_ROOT_SELECTOR = (
    '[role="dialog"], .ant-modal, [class*="modal"], [class*="drawer"], '
    '[class*="popover"]'
)
_INLINE_UPLOAD_HINT_RE = re.compile(
    r"图片|图像|照片|本地上传|上传图片|选择文件|素材|插入图片|"
    r"image|picture|photo|upload|media",
    re.IGNORECASE,
)
_INLINE_UPLOAD_ACTIVITY_RE = re.compile(
    r"上传中|正在上传|处理中|解析中|uploading|processing|progress",
    re.IGNORECASE,
)
_UPLOAD_FAILURE_RE = re.compile(
    r"上传失败|上传错误|upload failed|upload error",
    re.IGNORECASE,
)
_COVER_CONTROL_RE = re.compile(r"封面|头图|头像|cover|header", re.IGNORECASE)
_FINAL_PUBLISH_CONTROL_RE = re.compile(
    r"^(?:预览并发布|确认发布|立即发布|发布|发表|提交)$",
    re.IGNORECASE,
)
_IMAGE_CONTROL_RE = re.compile(
    r"image|picture|photo|upload|media|icon[-_]?pic|icon[-_]?image|"
    r"插图|配图|图片|图像|照片|媒体",
    re.IGNORECASE,
)
_THUMBNAIL_SELECTOR = (
    'img, [role="option"], [class*="thumbnail"], [class*="thumb"], '
    '[data-testid*="image"], [data-testid*="thumb"], [data-e2e*="image"]'
)


@dataclass
class ToutiaoImageUploadContext:
    """The UI context opened by the article editor's image control."""

    frame: Frame
    root: Locator | None
    mode: str
    chooser: object | None = None
    before_thumbnails: tuple[str, ...] = ()


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


def _is_enabled_control(locator: Locator) -> bool:
    """Reject controls that are visible but disabled by the editor state."""

    if not _is_interactable(locator):
        return False
    try:
        if not locator.is_enabled():
            return False
    except Exception:
        return False
    try:
        aria_disabled = (locator.get_attribute("aria-disabled") or "").lower()
        if aria_disabled == "true" or locator.get_attribute("disabled") is not None:
            return False
        classes = locator.get_attribute("class") or ""
        if re.search(
            r"(?:^|[-_\s])disable(?:d)?(?:$|[-_\s])|is-disabled|forbidden",
            classes,
            re.I,
        ):
            return False
        if not locator.evaluate(
            """
            element => {
                let current = element;
                while (current && current.nodeType === Node.ELEMENT_NODE) {
                    const className = String(current.getAttribute('class') || '');
                    const ariaDisabled = String(current.getAttribute('aria-disabled') || '').toLowerCase();
                    if (ariaDisabled === 'true' || current.hasAttribute('disabled') ||
                        /(?:^|[-_\s])disable(?:d)?(?:$|[-_\s])|is-disabled|forbidden/i.test(className)) {
                        return false;
                    }
                    current = current.parentElement;
                }
                return true;
            }
            """
        ):
            return False
    except Exception:
        return False
    return True


def _locator_text(locator: Locator) -> str:
    try:
        return locator.inner_text(timeout=1_000)
    except Exception:
        try:
            return locator.text_content(timeout=1_000) or ""
        except Exception:
            return ""


def _visible_text(page: Page) -> str:
    texts: list[str] = []
    for frame in page.frames:
        try:
            text = frame.locator("body").inner_text(timeout=2_000)
        except Exception:
            continue
        if text.strip():
            texts.append(text)
    return "\n".join(texts)


def _wait_for_dom(page: Page) -> None:
    try:
        page.wait_for_load_state("domcontentloaded", timeout=30_000)
    except TimeoutError:
        pass
    page.wait_for_function(
        "() => !!document.body && document.body.innerText.trim().length > 0",
        timeout=30_000,
    )


def _capture_debug_snapshot(page: Page) -> None:
    """Save public DOM diagnostics without serializing browser storage."""

    debug_dir = Path("debug")
    try:
        debug_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return

    dom_parts: list[str] = []
    visible_parts: list[str] = []
    prosemirror_count = 0
    toolbar_count = 0
    for index, frame in enumerate(page.frames):
        try:
            prosemirror_count += frame.locator(".ProseMirror").count()
            toolbar_count += frame.locator(
                '[role="toolbar"], [class*="toolbar"]'
            ).count()
        except Exception:
            pass
        try:
            html = frame.evaluate(
                """() => {
                    if (!document.body) return '';
                    const clone = document.body.cloneNode(true);
                    clone.querySelectorAll('script, style, noscript, iframe, input[type=password], input[type=hidden]').forEach(el => el.remove());
                    const allowed = new Set(['id', 'class', 'role', 'title', 'alt', 'type', 'accept', 'placeholder', 'contenteditable', 'disabled', 'aria-disabled', 'aria-label', 'aria-busy', 'data-e2e', 'data-testid', 'data-status', 'data-upload-status', 'web_uri', '__syl_tag', 'src', 'href']);
                    for (const el of [clone, ...clone.querySelectorAll('*')]) {
                        for (const attr of Array.from(el.attributes)) {
                            if (!allowed.has(attr.name)) el.removeAttribute(attr.name);
                            else if (attr.name === 'src' || attr.name === 'href') {
                                el.setAttribute(attr.name, attr.value.startsWith('data:') ? '[embedded image]' : attr.value.split(/[?#]/)[0]);
                            }
                        }
                    }
                    return clone.outerHTML;
                }"""
            )
            dom_parts.append(f"<!-- frame {index}: {frame.url.split('?', 1)[0]} -->\n{html}")
        except Exception as exc:
            dom_parts.append(f"<!-- frame {index}: unavailable: {type(exc).__name__} -->")
        try:
            visible = frame.locator("body").inner_text(timeout=2_000)
            visible_parts.append(f"[frame {index}: {frame.url}]\n{visible}")
        except Exception:
            continue

    try:
        title = page.title()
    except Exception:
        title = ""
    try:
        upload_root_count = len(_visible_inline_upload_contexts(page))
    except Exception:
        upload_root_count = 0
    try:
        file_input_count = len(_image_file_inputs(page))
    except Exception:
        file_input_count = 0
    diagnostic = "\n".join(
        (
            f"current_url: {page.url}",
            f"page_title: {title}",
            f"frame_count: {len(page.frames)}",
            f"prosemirror_count: {prosemirror_count}",
            f"visible_toolbar_count: {toolbar_count}",
            f"visible_upload_root_count: {upload_root_count}",
            f"visible_file_input_count: {file_input_count}",
        )
    )
    try:
        (debug_dir / "toutiao_article-dom.html").write_text(
            "\n\n".join(dom_parts), encoding="utf-8"
        )
        (debug_dir / "toutiao_article-visible-text.txt").write_text(
            "\n\n".join(visible_parts), encoding="utf-8"
        )
        (debug_dir / "toutiao_article-diagnostic.txt").write_text(
            diagnostic, encoding="utf-8"
        )
    except OSError:
        pass
    try:
        page.screenshot(
            path=str(debug_dir / "toutiao_article-failure.png"),
            full_page=True,
        )
    except Exception:
        pass


def _run_toutiao_step(page: Page, step: str, action):
    try:
        return _run_step(step, action)
    except PublisherError:
        _capture_debug_snapshot(page)
        raise


def _navigate(page: Page, url: str) -> None:
    """Navigate through Toutiao's SPA redirects without false-failing on aborts.

    Toutiao can replace the requested document while Playwright is waiting for
    ``domcontentloaded`` (especially when restoring a logged-in profile).  In
    that case Chromium reports ``net::ERR_ABORTED`` even though the replacement
    page is already attached.  Waiting for ``commit`` and then the visible DOM
    lets the login/editor checks decide whether navigation actually succeeded.
    """

    try:
        page.goto(url, wait_until="commit", timeout=60_000)
    except PlaywrightError as exc:
        message = str(exc)
        if not re.search(r"ERR_ABORTED|interrupted by another navigation", message, re.I):
            raise
        if not page.url.startswith("https://mp.toutiao.com/"):
            raise
    _wait_for_dom(page)


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
    _navigate(page, HOME_URL)
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
    _navigate(page, HOME_URL)
    if _is_login_page(page):
        raise PublisherError(
            "Checking Toutiao article login",
            "Login was not completed. The page is still showing the Toutiao login screen.",
        )


def _close_dialogs(page: Page) -> None:
    """Close non-content onboarding overlays without touching publish controls."""

    # The current creator page can restore the AI assistant as a transparent
    # drawer.  Its mask still intercepts pointer events even though the
    # article editor remains visible.  Close only this named assistant drawer;
    # never dismiss an arbitrary drawer because the image uploader is also a
    # drawer and is part of the publishing flow.
    assistant_mask = page.locator(
        '.byte-drawer-wrapper.ai-assistant-drawer:visible '
        '.byte-drawer-mask:visible'
    )
    mask = _first_interactable(assistant_mask)
    if mask is not None:
        try:
            mask.click(force=True)
            page.wait_for_timeout(300)
        except Exception:
            pass

    roots = page.locator(
        '[role="dialog"], .ant-modal, [class*="modal"], '
        '[class*="popover"], [class*="tooltip"]'
    )
    for label in ("跳过", "知道了", "我知道了", "完成"):
        candidate = _first_interactable(roots.get_by_text(label, exact=True))
        if candidate is not None:
            try:
                _assert_not_final_publish_control(candidate)
                candidate.click()
            except Exception:
                continue


def _open_editor(page: Page) -> None:
    _navigate(page, EDITOR_URL)
    if _is_login_page(page):
        raise PublisherError(
            "Opening Toutiao article editor",
            "Toutiao redirected back to the login page.",
        )
    _close_dialogs(page)
    LOGGER.info("Toutiao editor opened: url=%s", page.url)


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
    try:
        tag = title.evaluate("element => element.tagName")
        placeholder = title.get_attribute("placeholder")
    except Exception:
        tag = "unknown"
        placeholder = None
    LOGGER.info(
        "Toutiao title input: tag=%s placeholder=%r",
        tag,
        placeholder,
    )
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
    try:
        editor_class = body.get_attribute("class")
    except Exception:
        editor_class = None
    LOGGER.info(
        "Toutiao body editor: class=%r prosemirror=%s",
        editor_class,
        _is_prosemirror_editor(body),
    )
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
    stable = 0
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        page.wait_for_timeout(250)
        actual = _read_locator_value(body)
        if _editor_validation_text(rendered.text) in _editor_validation_text(actual):
            stable += 1
            if stable >= 2:
                return
        else:
            stable = 0
    raise PublisherError(
        "Filling Toutiao article content",
        "The Toutiao article content did not survive the editor rerender.",
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


def _control_label(locator: Locator) -> str:
    attributes = (
        "aria-label",
        "title",
        "data-tooltip",
        "data-tip",
        "data-title",
        "data-testid",
        "data-e2e",
        "data-action",
        "data-command",
        "data-icon",
        "data-name",
        "data-type",
        "id",
        "name",
        "value",
        "onclick",
        "class",
    )
    try:
        label = " ".join(str(locator.get_attribute(attribute) or "") for attribute in attributes)
        label += " " + (locator.text_content() or "")
        label += " " + locator.evaluate("element => element.outerHTML")
        # Toutiao's current editor puts the tool name on the ancestor
        # ``.syl-toolbar-tool.image`` rather than on the button itself.
        ancestor = locator
        for _ in range(4):
            ancestor = ancestor.locator("xpath=..")
            label += " " + " ".join(
                str(ancestor.get_attribute(attribute) or "")
                for attribute in ("class", "aria-label", "title", "data-testid", "data-action")
            )
        return label
    except Exception:
        return ""


def _assert_not_final_publish_control(locator: Locator) -> None:
    """Refuse any click target whose semantics can submit the article."""

    labels = [_locator_text(locator)]
    for attribute in ("aria-label", "title", "value", "data-action", "data-command"):
        try:
            labels.append(locator.get_attribute(attribute) or "")
        except Exception:
            pass
    label = " ".join(labels)
    if re.search(r"发布|发表|提交|publish|submit", label, re.I):
        raise PublisherError(
            "Toutiao article publish safety",
            "Refused to activate a control that may submit or publish the article.",
        )


def _editor_toolbar_scopes(page: Page, editor: Locator | None) -> list[Locator]:
    del page
    toolbar_selector = (
        '[role="toolbar"], .syl-editor-toolbar, .syl-toolbar, '
        '[data-toolbar], [data-editor-toolbar]'
    )
    scopes: list[Locator] = []
    if editor is not None:
        # In the current editor the toolbar is a sibling or a nearby ancestor
        # of ProseMirror. Walking only a few ancestors keeps this editor-bound.
        for level in range(1, 5):
            try:
                parent = editor.locator("xpath=" + ".." + "/.." * (level - 1))
                scopes.append(parent.locator(toolbar_selector))
            except Exception:
                continue
    return scopes


def _image_control_in_scope(scope: Locator) -> Locator | None:
    name = re.compile(r"(?:插入|添加|上传)?\s*(?:图片|图像|照片)|image", re.IGNORECASE)
    candidates = [
        scope.get_by_role("button", name=name),
        scope.locator(
            '[aria-label*="图片"], [title*="图片"], '
            '[data-tooltip*="图片"], [data-tip*="图片"], [data-title*="图片"], '
            '[data-testid*="image"], [data-e2e*="image"], '
            '[data-action*="image"], [data-command*="image"], '
            '[class*="image-insert"], [class*="insert-image"], '
            '[class*="image-upload"], '
            '[class*="syl-toolbar-tool"][class*="image"] button, '
            '[class*="toolbar"] [class*="image"] button'
        ),
        scope.locator('button, [role="button"], [tabindex]'),
    ]
    for locator in candidates:
        try:
            count = locator.count()
        except Exception:
            continue
        for index in range(count):
            candidate = locator.nth(index)
            if not _is_enabled_control(candidate):
                continue
            if candidate.get_attribute("contenteditable") == "true":
                continue
            label = _control_label(candidate)
            if _COVER_CONTROL_RE.search(label):
                continue
            if _IMAGE_CONTROL_RE.search(label):
                return candidate
    return None


def _image_trigger(page: Page, editor: Locator | None = None) -> Locator | None:
    """Find an enabled image control after the editor owns the selection."""

    # A missing editor-owned control is a failure, never a reason to inspect
    # cover uploads or the page-wide material manager.
    for scope in _editor_toolbar_scopes(page, editor):
        candidate = _image_control_in_scope(scope)
        if candidate is not None:
            return candidate

    return None


def _acceptable_image_input(locator: Locator) -> bool:
    try:
        accept = (locator.get_attribute("accept") or "").lower()
    except Exception:
        return False
    return "image" in accept or ".png" in accept or ".jpg" in accept or not accept


def _inline_upload_root_signature(root: Locator) -> str:
    attributes = " ".join(
        str(root.get_attribute(attribute) or "")
        for attribute in ("id", "class", "role", "aria-label", "data-testid")
    )
    return f"{attributes}|{_locator_text(root)[:500]}"


def _looks_like_inline_upload_root(root: Locator) -> bool:
    if not _is_interactable(root):
        return False
    label = " ".join(
        str(root.get_attribute(attribute) or "")
        for attribute in ("id", "class", "role", "aria-label", "data-testid", "title")
    )
    text = f"{label} {_locator_text(root)}"
    if _COVER_CONTROL_RE.search(text) and not re.search(r"正文|插入", text, re.I):
        return False
    try:
        file_inputs = root.locator('input[type="file"]')
        if any(
            _acceptable_image_input(file_inputs.nth(index))
            for index in range(file_inputs.count())
        ):
            return True
    except Exception:
        pass
    return bool(_INLINE_UPLOAD_HINT_RE.search(text) and re.search(r"图片|image|upload|本地|素材", text, re.I))


def _visible_inline_upload_contexts(page: Page) -> list[ToutiaoImageUploadContext]:
    contexts: list[ToutiaoImageUploadContext] = []
    for frame in page.frames:
        roots = frame.locator(_INLINE_UPLOAD_ROOT_SELECTOR)
        try:
            count = roots.count()
        except Exception:
            continue
        for index in range(count):
            root = roots.nth(index)
            if not _looks_like_inline_upload_root(root):
                continue
            class_name = (root.get_attribute("class") or "").lower()
            mode = "drawer" if "drawer" in class_name else "dialog"
            contexts.append(
                ToutiaoImageUploadContext(
                    frame=frame,
                    root=root,
                    mode=mode,
                    before_thumbnails=_thumbnail_signatures(root),
                )
            )
    return contexts


def _thumbnail_signatures(root: Locator) -> tuple[str, ...]:
    signatures: list[str] = []
    try:
        thumbnails = root.locator(_THUMBNAIL_SELECTOR)
        for index in range(thumbnails.count()):
            candidate = thumbnails.nth(index)
            try:
                value = "|".join(
                    str(candidate.get_attribute(attribute) or "")
                    for attribute in ("src", "alt", "data-testid", "data-e2e", "class")
                )
                value += "|" + (_locator_text(candidate)[:160])
                signatures.append(value)
            except Exception:
                continue
    except Exception:
        return ()
    return tuple(signatures)


def _new_thumbnail_present(context: ToutiaoImageUploadContext) -> bool:
    if context.root is None:
        return False
    current = _thumbnail_signatures(context.root)
    remaining = list(context.before_thumbnails)
    for signature in current:
        if signature in remaining:
            remaining.remove(signature)
        else:
            return True
    return False


def _wait_for_inline_upload_context(
    page: Page,
    before_signatures: set[tuple[int, str]],
    timeout: int = 15_000,
) -> ToutiaoImageUploadContext:
    deadline = time.monotonic() + timeout / 1_000
    while time.monotonic() < deadline:
        for context in _visible_inline_upload_contexts(page):
            signature = (id(context.frame), _inline_upload_root_signature(context.root))  # type: ignore[arg-type]
            if signature not in before_signatures:
                return context
        page.wait_for_timeout(200)
    raise PublisherError(
        "Opening Toutiao article image upload",
        "Could not open Toutiao inline image upload UI after activating the article editor control.",
    )


def _image_file_inputs(
    page: Page,
    context: ToutiaoImageUploadContext | None = None,
) -> list[Locator]:
    """Return only image inputs inside the causally opened upload context."""

    roots: list[Locator] = []
    if context is not None:
        if context.root is None:
            return []
        roots = [context.root]
    else:
        roots = [
            upload_context.root
            for upload_context in _visible_inline_upload_contexts(page)
            if upload_context.root is not None
        ]

    inputs: list[Locator] = []
    for root in roots:
        try:
            candidates = root.locator('input[type="file"]')
            for index in range(candidates.count()):
                candidate = candidates.nth(index)
                if _acceptable_image_input(candidate):
                    inputs.append(candidate)
        except Exception:
            continue

    # Toutiao's current upload drawer renders two image inputs: the normal
    # ``.btn-upload-handle`` input, which owns the React ``onChange`` handler,
    # and ``#upload-drag-input``, which is reserved for drag-and-drop.  Calling
    # set_input_files() on the latter changes the file list but cannot start an
    # upload because that input deliberately has no onChange callback.  Keep
    # the drag-only input out whenever the real upload input is present, and
    # order the remaining candidates so the caller's ``[-1]`` remains safe.
    direct_inputs = [
        candidate
        for candidate in inputs
        if candidate.get_attribute("id") != "upload-drag-input"
    ]
    if direct_inputs:
        inputs = direct_inputs
    return sorted(inputs, key=_image_file_input_priority)


def _image_file_input_priority(locator: Locator) -> int:
    """Prefer a file input with an upload change handler over drag-only input."""

    try:
        input_id = locator.get_attribute("id") or ""
        if input_id == "upload-drag-input":
            return -100
        ancestor_hint = locator.evaluate(
            """
            element => {
                const parts = [];
                let current = element;
                for (let depth = 0; current && depth < 5; depth += 1) {
                    parts.push(String(current.id || ''));
                    parts.push(String(current.className || ''));
                    current = current.parentElement;
                }
                return parts.join(' ').toLowerCase();
            }
            """
        )
        if "btn-upload-handle" in ancestor_hint:
            return 100
        if "upload-handler-drag" in ancestor_hint:
            return -100
        return 10 if _is_interactable(locator) else 0
    except Exception:
        return 0


def _wait_for_image_file_input(
    page: Page,
    context: ToutiaoImageUploadContext | None = None,
    timeout: int = 15_000,
) -> Locator:
    deadline = time.monotonic() + timeout / 1_000
    while time.monotonic() < deadline:
        inputs = _image_file_inputs(page, context)
        if inputs:
            return inputs[-1]
        page.wait_for_timeout(250)
    raise PublisherError(
        "Opening Toutiao article image upload",
        "No image file input appeared inside the opened Toutiao inline-image UI.",
    )


def _context_text(page: Page, context: ToutiaoImageUploadContext) -> str:
    if context.root is not None:
        return _locator_text(context.root)
    return _visible_text(page)


def _context_is_busy(page: Page, context: ToutiaoImageUploadContext) -> bool:
    roots = [context.root] if context.root is not None else [frame.locator("body") for frame in page.frames]
    for root in roots:
        if root is None:
            continue
        try:
            busy = root.locator(
                '[aria-busy="true"], [class*="loading"], [class*="uploading"], '
                '[role="progressbar"], .syl-progress-status-active'
            )
            for index in range(busy.count()):
                try:
                    if busy.nth(index).is_visible():
                        return True
                except Exception:
                    continue
        except Exception:
            continue
    return bool(_INLINE_UPLOAD_ACTIVITY_RE.search(_context_text(page, context)))


def _successful_upload_present(context: ToutiaoImageUploadContext) -> bool:
    """Return whether the opened upload UI has a completed image item."""

    if context.root is None:
        return False
    try:
        if not _new_thumbnail_present(context):
            return False
        # The current Toutiao drawer renders ``.success`` only after the
        # uploader receives the server response.  A blob preview alone is not
        # completion: it is rendered before the asynchronous upload starts.
        # ``.success`` itself is a state marker whose child icon can have no
        # rendered box, so attachment is authoritative; requiring
        # ``is_visible()`` incorrectly times out on a completed upload.
        success = context.root.locator(
            '.pic-select-image-item .success, '
            '[data-upload-status="success"], [data-status="success"]'
        )
        return success.count() > 0
    except Exception:
        return False


def _raise_if_upload_failed(page: Page, context: ToutiaoImageUploadContext) -> None:
    if _UPLOAD_FAILURE_RE.search(_context_text(page, context)):
        raise PublisherError(
            "Uploading Toutiao article image",
            "The Toutiao inline-image UI reported an image upload failure.",
        )
    if context.root is not None:
        try:
            failed = context.root.locator(
                '.pic-select-image-item .error, '
                '[data-upload-status="error"], [data-status="error"]'
            )
            for index in range(failed.count()):
                if failed.nth(index).is_visible():
                    raise PublisherError(
                        "Uploading Toutiao article image",
                        "The Toutiao inline-image UI reported an image upload failure.",
                    )
        except PublisherError:
            raise
        except Exception:
            pass


def _wait_for_upload_started(
    page: Page,
    context: ToutiaoImageUploadContext,
    editor: Locator,
    before_count: int,
    timeout: int = 30_000,
) -> None:
    """Wait until the upload visibly starts; absence of loading is not enough."""

    deadline = time.monotonic() + timeout / 1_000
    while time.monotonic() < deadline:
        _raise_if_upload_failed(page, context)
        if _editor_image_count(editor) > before_count or _context_is_busy(page, context):
            return
        if _new_thumbnail_present(context):
            return
        page.wait_for_timeout(200)
    raise PublisherError(
        "Uploading Toutiao article image",
        "The Toutiao inline-image upload did not show a started/uploading state.",
    )


def _wait_for_upload_complete(
    page: Page,
    context: ToutiaoImageUploadContext | None = None,
    editor: Locator | None = None,
    before_count: int = 0,
    timeout: int = 120_000,
) -> None:
    """Wait for a ready image and two stable non-busy observations."""

    if context is None:
        context = ToutiaoImageUploadContext(page.main_frame, None, "native")
    stable_ready = 0
    deadline = time.monotonic() + timeout / 1_000
    while time.monotonic() < deadline:
        _raise_if_upload_failed(page, context)
        image_inserted = editor is not None and _editor_image_count(editor) > before_count
        ready_thumbnail = _successful_upload_present(context)
        success_text = bool(re.search(r"上传成功|已完成|ready|success", _context_text(page, context), re.I))
        ready = image_inserted or ready_thumbnail or success_text
        if ready and not _context_is_busy(page, context):
            stable_ready += 1
            if stable_ready >= 2:
                return
        else:
            stable_ready = 0
        page.wait_for_timeout(250)
    raise PublisherError(
        "Uploading Toutiao article image",
        "The Toutiao inline-image upload did not reach a stable ready state.",
    )


def _find_inline_confirm_control(root: Locator) -> Locator | None:
    pattern = re.compile(r"^(?:插入|确认|确定|完成)(?:图片|正文)?$", re.I)
    candidates = [
        root.get_by_role("button", name=pattern),
        root.get_by_text(pattern, exact=True),
    ]
    for locator in candidates:
        try:
            count = locator.count()
        except Exception:
            continue
        for index in range(count):
            candidate = locator.nth(index)
            if _is_enabled_control(candidate):
                return candidate
    return None


def _select_uploaded_toutiao_image(context: ToutiaoImageUploadContext) -> None:
    """Select the newly uploaded thumbnail, never the first old asset."""

    if context.root is None:
        raise PublisherError(
            "Uploading Toutiao article image",
            "Toutiao requires an image selection context, but no inline upload UI was retained.",
        )
    thumbnails = context.root.locator(_THUMBNAIL_SELECTOR)
    remaining = list(context.before_thumbnails)
    for index in range(thumbnails.count()):
        candidate = thumbnails.nth(index)
        signature = "|".join(
            str(candidate.get_attribute(attribute) or "")
            for attribute in ("src", "alt", "data-testid", "data-e2e", "class")
        )
        signature += "|" + _locator_text(candidate)[:160]
        if signature in remaining:
            remaining.remove(signature)
            continue
        try:
            candidate.click(force=True)
            return
        except Exception as exc:
            raise PublisherError(
                "Selecting Toutiao article image",
                f"Could not select the newly uploaded Toutiao image: {exc}",
            ) from exc
    raise PublisherError(
        "Selecting Toutiao article image",
        "The Toutiao inline-image UI did not show the newly uploaded image thumbnail.",
    )


def _confirm_inline_upload(
    page: Page,
    context: ToutiaoImageUploadContext | None = None,
) -> bool:
    """Confirm only the causally opened inline-image dialog/drawer."""

    if context is None:
        contexts = _visible_inline_upload_contexts(page)
        context = contexts[-1] if contexts else None
    if context is None or context.root is None:
        return False
    candidate = _find_inline_confirm_control(context.root)
    if candidate is None:
        return False
    _assert_not_final_publish_control(candidate)
    candidate.click()
    return True


def _wait_for_inline_upload_closed(
    page: Page,
    context: ToutiaoImageUploadContext,
    timeout: int = 30_000,
) -> None:
    if context.root is None:
        return
    deadline = time.monotonic() + timeout / 1_000
    while time.monotonic() < deadline:
        try:
            if not context.root.is_visible():
                return
        except Exception:
            return
        page.wait_for_timeout(200)
    raise PublisherError(
        "Uploading Toutiao article image",
        "The Toutiao inline-image dialog did not close after inserting the selected image.",
    )


def _editor_image_signatures(editor: Locator) -> tuple[str, ...]:
    """Return one stable signature per logical article image."""

    try:
        # Toutiao's Syl/ProseMirror image node contains both a canonical image
        # under a hidden ``templ`` element and a second editable preview under
        # ``mask``.  Counting every ``img`` therefore doubles the logical
        # article-image count.  Prefer the canonical model image when present.
        canonical = editor.locator(
            '[__syl_tag="true"] templ .pgc-img img, '
            'templ .pgc-img img'
        )
        images = canonical if canonical.count() else editor.locator("img")
        signatures: list[str] = []
        for index in range(images.count()):
            image = images.nth(index)
            signature = (
                image.get_attribute("web_uri")
                or image.get_attribute("data-uri")
                or image.get_attribute("src")
                or f"image-{index}"
            )
            signatures.append(signature.split("?", 1)[0])
        return tuple(signatures)
    except Exception:
        return ()


def _editor_image_count(editor: Locator) -> int:
    return len(_editor_image_signatures(editor))


def _new_image_signature(
    before: tuple[str, ...], after: tuple[str, ...]
) -> str:
    remaining = Counter(after)
    remaining.subtract(before)
    added = [signature for signature, count in remaining.items() for _ in range(count) if count > 0]
    if len(added) != 1:
        raise PublisherError(
            "Uploading Toutiao article image",
            f"Expected one new logical editor image, found {len(added)}.",
        )
    return added[0]


def _editor_content_sequence(editor: Locator) -> tuple[tuple[str, str], ...]:
    """Read merged text/image order from ProseMirror's top-level model nodes."""

    raw = editor.evaluate(
        """
        element => Array.from(element.children).map(child => {
            const image = child.querySelector('templ .pgc-img img') ||
                (child.matches('img') ? child : null);
            if (image) {
                const signature = image.getAttribute('web_uri') ||
                    image.getAttribute('data-uri') || image.getAttribute('src') || '';
                return {kind: 'image', value: signature.split('?', 1)[0]};
            }
            return {kind: 'text', value: child.innerText || child.textContent || ''};
        })
        """
    )
    sequence: list[tuple[str, str]] = []
    for item in raw:
        kind = str(item.get("kind") or "")
        value = str(item.get("value") or "")
        if kind == "text":
            value = _editor_validation_text(value)
            if not value:
                continue
            if sequence and sequence[-1][0] == "text":
                sequence[-1] = ("text", sequence[-1][1] + value)
            else:
                sequence.append(("text", value))
        elif kind == "image" and value:
            sequence.append(("image", value))
    return tuple(sequence)


def _assert_inline_content_sequence(
    editor: Locator,
    blocks: tuple[ContentBlock, ...],
    image_signatures: tuple[str, ...],
) -> None:
    expected: list[tuple[str, str]] = []
    image_index = 0
    for block in blocks:
        if isinstance(block, TextBlock):
            value = _editor_validation_text(
                render_for_platform("toutiao_article", block.text).text
            )
            if not value:
                continue
            if expected and expected[-1][0] == "text":
                expected[-1] = ("text", expected[-1][1] + value)
            else:
                expected.append(("text", value))
        else:
            expected.append(("image", image_signatures[image_index]))
            image_index += 1
    actual = _editor_content_sequence(editor)
    if actual != tuple(expected):
        raise PublisherError(
            "Filling Toutiao article content",
            f"The Toutiao editor block order was {actual!r}; expected {tuple(expected)!r}.",
        )


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


def _visible_inline_upload_signatures(page: Page) -> set[tuple[int, str]]:
    return {
        (id(context.frame), _inline_upload_root_signature(context.root))  # type: ignore[arg-type]
        for context in _visible_inline_upload_contexts(page)
        if context.root is not None
    }


def _open_inline_image_upload(
    page: Page,
    trigger: Locator,
) -> ToutiaoImageUploadContext:
    """Open the inline upload UI and retain the frame/root it created."""

    before_signatures = _visible_inline_upload_signatures(page)
    try:
        with page.expect_file_chooser(timeout=4_000) as chooser_info:
            trigger.click()
        return ToutiaoImageUploadContext(
            frame=page.main_frame,
            root=None,
            mode="native",
            chooser=chooser_info.value,
        )
    except TimeoutError:
        context = _wait_for_inline_upload_context(page, before_signatures)
        if context.root is None:
            raise PublisherError(
                "Opening Toutiao article image upload",
                "Toutiao opened an image UI without a usable inline upload root.",
            )
        return context
    except PlaywrightError as exc:
        raise PublisherError(
            "Opening Toutiao article image upload",
            f"Could not activate the Toutiao inline-image control: {exc}",
        ) from exc


def _insert_image(page: Page, editor: Locator, image: Path) -> str:
    before_signatures = _editor_image_signatures(editor)
    before_count = len(before_signatures)
    # The current toolbar can remain disabled/unmounted until the ProseMirror
    # editor owns focus, so focus it before resolving the image control.
    _focus_editor_for_image_insertion(editor)
    trigger = _image_trigger(page, editor)
    if trigger is None:
        raise PublisherError(
            "Uploading Toutiao article image",
            "Could not find the article editor's inline image control.",
        )
    _assert_not_final_publish_control(trigger)
    LOGGER.info(
        "Toutiao inline image: file=%s before_count=%d trigger=%s enabled=%s",
        image.name,
        before_count,
        _compact_text(_control_label(trigger))[:240],
        _is_enabled_control(trigger),
    )
    path = str(image.resolve())
    context = _open_inline_image_upload(page, trigger)
    LOGGER.info("Toutiao inline image upload mode: %s", context.mode)

    if context.mode == "native":
        if context.chooser is None:
            raise PublisherError(
                "Uploading Toutiao article image",
                "Toutiao opened a native image chooser without returning a chooser handle.",
            )
        context.chooser.set_files(path, timeout=30_000)  # type: ignore[attr-defined]
    else:
        file_input = _wait_for_image_file_input(page, context)
        LOGGER.info(
            "Toutiao inline file input: id=%r class=%r",
            file_input.get_attribute("id"),
            file_input.get_attribute("class"),
        )
        file_input.set_input_files(path, timeout=30_000)

    _wait_for_upload_started(page, context, editor, before_count)
    LOGGER.info("Toutiao inline image upload started: file=%s", image.name)
    _wait_for_upload_complete(page, context, editor, before_count)
    LOGGER.info("Toutiao inline image upload ready: file=%s", image.name)

    if context.root is not None:
        confirm = _find_inline_confirm_control(context.root)
        if confirm is not None:
            _select_uploaded_toutiao_image(context)
            LOGGER.info("Toutiao uploaded asset selected: file=%s", image.name)
            if not _confirm_inline_upload(page, context):
                raise PublisherError(
                    "Uploading Toutiao article image",
                    "Toutiao showed an inline-image confirmation UI but it was not usable.",
                )
            _wait_for_inline_upload_closed(page, context)
            LOGGER.info("Toutiao inline image confirmation completed: file=%s", image.name)

    _wait_for_editor_images(page, editor, before_count + 1)
    # Upload dialogs can steal focus or restore a stale ProseMirror selection.
    # Re-establish the caret before the next text/image block is appended.
    _focus_editor_for_image_insertion(editor)
    after_signatures = _editor_image_signatures(editor)
    signature = _new_image_signature(before_signatures, after_signatures)
    LOGGER.info(
        "Toutiao editor logical image count after insert: %d",
        len(after_signatures),
    )
    return signature


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
    before_count = _editor_image_count(body)
    image_blocks = sum(1 for block in blocks if isinstance(block, ImageBlock))
    image_signatures: list[str] = []
    for block in blocks:
        if isinstance(block, TextBlock):
            _append_rendered_html(body, render_for_platform("toutiao_article", block.text))
        else:
            image_signatures.append(
                _insert_image(page, body, content.images[block.index - 1])
            )
    expected_count = before_count + image_blocks
    actual_count = _editor_image_count(body)
    if actual_count != expected_count:
        raise PublisherError(
            "Filling Toutiao article content",
            f"The Toutiao article editor contains {actual_count} images; expected {expected_count} after inline insertion.",
        )
    if not _inline_text_blocks_are_present(body, blocks):
        raise PublisherError(
            "Filling Toutiao article content",
            "The Toutiao article editor did not contain the provided text after inline image insertion.",
        )
    _assert_inline_content_sequence(body, blocks, tuple(image_signatures))


def _wait_for_draft_idle(page: Page, timeout: int = 30_000) -> None:
    deadline = time.monotonic() + timeout / 1_000
    while time.monotonic() < deadline:
        text = _visible_text(page)
        if not re.search(r"草稿保存中|正在保存", text):
            return
        page.wait_for_timeout(500)
    raise PublisherError(
        "Checking Toutiao article draft",
        "Toutiao still reported that the draft was saving after 30 seconds.",
    )


def _visible_final_publish_controls(page: Page) -> tuple[str, ...]:
    labels: list[str] = []
    for frame in page.frames:
        controls = frame.locator('button, [role="button"]')
        for index in range(controls.count()):
            candidate = controls.nth(index)
            if not _is_interactable(candidate):
                continue
            label = _compact_text(_locator_text(candidate))
            if _FINAL_PUBLISH_CONTROL_RE.fullmatch(label):
                labels.append(label)
    return tuple(labels)


def _verify_ready_state(
    page: Page,
    content: PlatformContent,
    body: Locator,
    blocks: tuple[ContentBlock, ...],
    has_inline_images: bool,
) -> None:
    page.wait_for_timeout(750)
    title = _title_locator(page)
    if _read_locator_value(title).strip() != content.title:
        raise PublisherError(
            "Checking Toutiao article title",
            "The Toutiao title changed after the editor rerender.",
        )
    if has_inline_images:
        if not _inline_text_blocks_are_present(body, blocks):
            raise PublisherError(
                "Checking Toutiao article content",
                "The Toutiao text changed after inline image insertion.",
            )
    else:
        rendered = render_for_platform("toutiao_article", content.body)
        if _editor_validation_text(rendered.text) not in _editor_validation_text(
            _read_locator_value(body)
        ):
            raise PublisherError(
                "Checking Toutiao article content",
                "The Toutiao text changed after the editor rerender.",
            )
    image_count = _editor_image_count(body)
    if image_count != len(content.images):
        raise PublisherError(
            "Checking Toutiao article images",
            f"The Toutiao editor contains {image_count} logical images; expected {len(content.images)}.",
        )
    page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
    page.wait_for_timeout(300)
    _wait_for_draft_idle(page)
    final_controls = _visible_final_publish_controls(page)
    if not final_controls:
        raise PublisherError(
            "Checking Toutiao article publish area",
            "Could not find the final publish control for manual review.",
        )
    LOGGER.info(
        "Toutiao article ready: url=%s logical_images=%d final_controls=%s (not clicked)",
        page.url,
        image_count,
        final_controls,
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
    _run_toutiao_step(
        page,
        "Checking Toutiao article login",
        lambda: _check_login(page, controller),
    )

    controller.step("toutiao_article", "opening_editor", "打开文章创作页")
    _run_toutiao_step(page, "Opening Toutiao article editor", lambda: _open_editor(page))

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

    _run_toutiao_step(page, "Filling Toutiao article title and content", fill_content)
    if body is None:
        raise PublisherError("Filling Toutiao article content", "Body editor was not retained.")

    controller.step(
        "toutiao_article",
        "uploading_images",
        f"向正文插入 {len(content.images)} 张图片",
    )
    _run_toutiao_step(
        page,
        "Uploading Toutiao article images",
        lambda: (
            _fill_body_with_inline_images(page, body, content, inline_blocks)
            if has_inline_images
            else _insert_images_at_end(page, body, content.images)
        ),
    )

    controller.step("toutiao_article", "final_check", "完成最终检查")
    _run_toutiao_step(
        page,
        "Checking Toutiao article before publish",
        lambda: _verify_ready_state(
            page,
            content,
            body,
            inline_blocks,
            has_inline_images,
        ),
    )


def _insert_images_at_end(page: Page, body: Locator, images: tuple[Path, ...]) -> None:
    """Preserve the public_long no-marker rule: append images in list order."""

    before_count = _editor_image_count(body)
    before_signatures = _editor_image_signatures(body)
    _focus_editor_for_image_insertion(body)
    inserted_signatures: list[str] = []
    for image in images:
        inserted_signatures.append(_insert_image(page, body, image))
    actual_count = _editor_image_count(body)
    expected_count = before_count + len(images)
    if actual_count != expected_count:
        raise PublisherError(
            "Uploading Toutiao article images",
            f"The Toutiao article editor contains {actual_count} images; expected {expected_count} after upload.",
        )
    after_signatures = _editor_image_signatures(body)
    if after_signatures[: len(before_signatures)] != before_signatures or after_signatures[
        len(before_signatures) :
    ] != tuple(inserted_signatures):
        raise PublisherError(
            "Uploading Toutiao article images",
            "The Toutiao article images were not appended in manifest order.",
        )


__all__ = [
    "EDITOR_URL",
    "HOME_URL",
    "ToutiaoImageUploadContext",
    "run_toutiao_article",
]
