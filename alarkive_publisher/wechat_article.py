"""Prepare only wechat_long in WeChat's article editor; never submit it."""

from __future__ import annotations

import re
import logging
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import Error as PlaywrightError, Locator, Page, TimeoutError

from .content import ContentVariant, PostContent
from .inline_images import ContentBlock, ImageBlock, TextBlock, inline_image_error_for_label, validate_inline_image_text
from .publisher_common import PublisherError, run_step
from .renderer import render_html
from .workflow_controller import CLIWorkflowController, WorkflowController


TARGET = "wechat_article"
HOME_URL = "https://mp.weixin.qq.com/"
BODY_SELECTOR = '.rich_media_content .ProseMirror[contenteditable="true"]'
TITLE_SELECTOR = '.title-editor__input .ProseMirror[contenteditable="true"]'
UPLOAD_SELECTOR = '#js_editor_insertimage input[type="file"]'
IMAGE_SELECTOR = 'img:not(.ProseMirror-separator)'
LOGGER = logging.getLogger(__name__)


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text.replace("\u200b", ""))


def _check_login(page: Page, controller: WorkflowController) -> None:
    def navigate() -> None:
        try:
            page.goto(HOME_URL, wait_until="commit", timeout=60_000)
        except PlaywrightError as exc:
            if "interrupted by another navigation" not in str(exc) or not page.url.startswith(HOME_URL):
                raise
        page.wait_for_load_state("domcontentloaded", timeout=30_000)
        page.locator("body").wait_for()

    navigate()
    if not page.get_by_text("新的创作", exact=True).is_visible():
        controller.wait_for_user(
            TARGET, "login", "请在 Chrome 中登录微信公众号并选择目标账号。", "登录完成后继续。"
        )
        navigate()
    page.get_by_text("新的创作", exact=True).wait_for(state="visible", timeout=30_000)


def _body(page: Page) -> Locator:
    body = page.locator(BODY_SELECTOR)
    if body.count() != 1 or not body.is_visible():
        raise PublisherError("Locating WeChat article editor", "没有找到唯一可见的公众号长文正文编辑器。")
    return body


def _title(page: Page) -> Locator:
    title = page.locator(TITLE_SELECTOR)
    if title.count() == 1 and title.is_visible():
        return title
    title = page.locator("textarea#title")
    if title.count() == 1 and title.is_visible():
        return title
    raise PublisherError("Locating WeChat article editor", "没有找到可见的公众号长文标题栏。")


def _open_editor(page: Page) -> Page:
    # Only the new-article entry is activated; never an existing draft.
    entry = page.get_by_text("文章", exact=True)
    entry.wait_for(state="visible", timeout=30_000)
    try:
        with page.expect_popup(timeout=10_000) as popup:
            entry.click()
        editor = popup.value
    except TimeoutError:
        editor = page
    editor.wait_for_load_state("domcontentloaded")
    editor.locator(BODY_SELECTOR).wait_for(state="visible", timeout=30_000)
    _title(editor).wait_for(state="visible")
    editor.bring_to_front()
    return editor


def _snapshot(page: Page) -> dict:
    return _body(page).evaluate("""root => {
        const clone = root.cloneNode(true);
        clone.querySelectorAll('.ProseMirror-widget, .editor_content_placeholder').forEach(e => e.remove());
        return {
            text: clone.textContent || '',
            html: clone.innerHTML,
            images: Array.from(root.querySelectorAll('img:not(.ProseMirror-separator)')).map(img => ({
                src: img.getAttribute('data-src') || img.getAttribute('src') || '',
                file_id: img.getAttribute('data-imgfileid') || '',
                loaded: img.complete && img.naturalWidth > 0,
                before: (() => {
                    const range = document.createRange();
                    range.selectNodeContents(root); range.setEndBefore(img);
                    return range.toString();
                })()
            }))
        };
    }""")


def _title_value(page: Page) -> str:
    title = _title(page)
    return title.input_value() if title.evaluate("e => e.tagName === 'TEXTAREA'") else title.inner_text()


def _ensure_empty(page: Page) -> None:
    state = _snapshot(page)
    if _title_value(page).strip() or state["text"].strip() or state["images"]:
        raise PublisherError("Checking empty WeChat article", "编辑器恢复了已有内容；已停止，请检查页面，避免覆盖原稿。")


def _paste_html(page: Page, html: str) -> None:
    body = _body(page)
    body.click()
    # Paste enters the editor's model; assigning innerHTML can disappear on redraw.
    body.evaluate("""(root, html) => {
        root.focus();
        const data = new DataTransfer();
        data.setData('text/html', html);
        const doc = new DOMParser().parseFromString(html, 'text/html');
        data.setData('text/plain', doc.body.textContent || '');
        root.dispatchEvent(new ClipboardEvent('paste', {
            bubbles: true, cancelable: true, clipboardData: data
        }));
    }""", html)


def _html_text(page: Page, html: str) -> str:
    return page.evaluate("html => new DOMParser().parseFromString(html, 'text/html').body.textContent", html)


def _upload_at_end(page: Page, image: Path, count: int) -> str:
    body = _body(page)
    previous_ids = [img["file_id"] for img in _snapshot(page)["images"]]
    if len(previous_ids) != count - 1:
        raise PublisherError("Uploading WeChat article image", "上传前的正文图片数量不符合预期。")
    # This editor can retain an old model selection after Ctrl+End. Select
    # the final text block explicitly, then let selectionchange reach it.
    body.evaluate("""root => {
        root.focus();
        const range = document.createRange();
        range.selectNodeContents(root.lastElementChild || root);
        range.collapse(false);
        const selection = window.getSelection();
        selection.removeAllRanges(); selection.addRange(range);
        document.dispatchEvent(new Event('selectionchange'));
    }""")
    page.wait_for_timeout(150)
    if not body.evaluate("root => {const last = root.lastElementChild; return last && !last.textContent.trim() && !last.querySelector('img');}"):
        body.press("Enter")
    page.wait_for_timeout(150)
    prefix = body.evaluate("""root => {
        const selection = window.getSelection();
        if (!selection.rangeCount || !root.contains(selection.anchorNode) || !selection.isCollapsed) return null;
        const range = document.createRange(); range.selectNodeContents(root);
        range.setEnd(selection.anchorNode, selection.anchorOffset);
        return range.toString();
    }""")
    if prefix is None or _compact(prefix) != _compact(_snapshot(page)["text"]):
        raise PublisherError("Positioning WeChat article image", "未能将光标准确放在正文末尾，已停止插图。")
    return _upload_image(page, image, count, previous_ids)


def _is_upload_request(request) -> bool:
    url = urlsplit(request.url)
    return (request.method == "POST" and url.hostname == "mp.weixin.qq.com"
            and url.path == "/cgi-bin/filetransfer"
            and parse_qs(url.query).get("action") == ["upload_material"])


def _upload_image(page: Page, image: Path, count: int, previous_ids: list[str]) -> str:
    # One UI upload can issue multiple filetransfer requests. A loaded image
    # is provisional until ALL of them finish and the model stops changing.
    pending = set()
    accepted_ids: set[str] = set()
    errors: list[str] = []
    last_activity = time.monotonic()

    def started(request):
        nonlocal last_activity
        if _is_upload_request(request):
            pending.add(request)
            last_activity = time.monotonic()

    def finished(request):
        nonlocal last_activity
        if request in pending:
            pending.discard(request)
            last_activity = time.monotonic()
            try:
                response = request.response()
                result = response.json() if response and response.ok else {}
                if result.get("base_resp", {}).get("ret") != 0 or not result.get("content"):
                    errors.append("微信图片上传接口未返回成功素材。")
                else:
                    accepted_ids.add(str(result["content"]))
            except Exception:
                errors.append("无法确认微信图片上传结果。")

    def failed(request):
        if request in pending:
            pending.discard(request)
            errors.append("微信图片上传请求失败。")

    callbacks = [("request", started), ("requestfinished", finished), ("requestfailed", failed)]
    for event, callback in callbacks:
        page.on(event, callback)
    try:
        LOGGER.info("Uploading WeChat article image %s", count)
        file_input = page.locator(UPLOAD_SELECTOR)
        file_input.set_input_files([])
        file_input.set_input_files(str(image.resolve()), timeout=30_000)
        _title(page).focus()
        deadline = time.monotonic() + 120
        previous = None
        stable_since = time.monotonic()
        while time.monotonic() < deadline:
            if errors:
                raise PublisherError("Uploading WeChat article image", errors[0])
            imgs = _snapshot(page)["images"]
            signature = [(img["file_id"], img["src"], img["loaded"]) for img in imgs]
            valid = (len(imgs) == count and imgs[-1]["file_id"] in accepted_ids
                     and [img["file_id"] for img in imgs[:-1]] == previous_ids
                     and all(img["loaded"] and img["src"].startswith("https://") for img in imgs))
            if signature != previous or not valid or pending:
                previous = signature
                stable_since = time.monotonic()
            elif time.monotonic() - max(stable_since, last_activity) >= 2:
                LOGGER.info("Uploaded WeChat article image %s", count)
                return imgs[-1]["src"]
            page.wait_for_timeout(250)
        raise PublisherError("Uploading WeChat article image", f"第 {count} 张图片未完成上传或稳定校验，已保留页面。")
    finally:
        for event, callback in callbacks:
            page.remove_listener(event, callback)


def _verify(page: Page, title: str, text: str, image_sources: list[str], prefixes: list[str] | None = None) -> None:
    state = _snapshot(page)
    if _title_value(page).strip() != title:
        raise PublisherError("Verifying WeChat article", "标题与 Package 不一致。")
    if _compact(state["text"]) != _compact(text):
        raise PublisherError("Verifying WeChat article", "正文不完整或包含额外内容。")
    if [img["src"] for img in state["images"]] != image_sources:
        raise PublisherError("Verifying WeChat article", "正文图片数量或顺序不一致。")
    if not all(img["loaded"] for img in state["images"]):
        raise PublisherError("Verifying WeChat article", "正文图片重绘后尚未加载完成。")
    expected_prefixes = prefixes if prefixes is not None else [text] * len(image_sources)
    if len(expected_prefixes) != len(state["images"]) or any(
        _compact(img["before"]) != _compact(prefix)
        for img, prefix in zip(state["images"], expected_prefixes)
    ):
        raise PublisherError("Verifying WeChat article", "图片没有插入指定的正文位置。")


def _prepare_append(page: Page, content: ContentVariant) -> None:
    blocks, validation = validate_inline_image_text(content.body, len(content.images))
    error = inline_image_error_for_label(validation, "WeChat article")
    if error:
        raise error
    html = render_html("\n\n".join(block.text.strip() for block in blocks if isinstance(block, TextBlock)))
    expected = _html_text(page, html)
    _ensure_empty(page)
    _title(page).fill(content.title)
    _paste_html(page, html)
    _title(page).focus()
    page.wait_for_timeout(700)
    _verify(page, content.title, expected, [])
    sources = []
    for count, image in enumerate(content.images, 1):
        sources.append(_upload_at_end(page, image, count))
        _title(page).focus()
        page.wait_for_timeout(700)
        _verify(page, content.title, expected, sources)
    _title(page).focus()
    page.wait_for_timeout(1500)
    _verify(page, content.title, expected, sources)


def _article_blocks(content: ContentVariant) -> tuple[ContentBlock, ...]:
    blocks, validation = validate_inline_image_text(content.body, len(content.images))
    error = inline_image_error_for_label(validation, "WeChat article")
    if error:
        raise error
    # Unlike append_unused_images(), this also appends ALL images with no markers.
    return (*blocks, *(ImageBlock(index) for index in validation.unused_images))


def _select_image_slot(page: Page, token: str, prefix: str) -> None:
    body = _body(page)
    body.evaluate("""(root, token) => {
        const matches = Array.from(root.querySelectorAll('p, section')).filter(e => e.textContent === token);
        if (matches.length !== 1) throw new Error('Expected one image slot paragraph');
        const slot = matches[0];
        slot.scrollIntoView({block: 'center'});
        root.focus();
        const range = document.createRange(); range.selectNodeContents(slot);
        const selection = window.getSelection();
        selection.removeAllRanges(); selection.addRange(range);
        document.dispatchEvent(new Event('selectionchange'));
    }""", token)
    page.wait_for_timeout(150)
    if body.evaluate("() => window.getSelection().toString()") != token:
        raise PublisherError("Positioning WeChat article image", "图片标记选区不正确，已停止。")
    body.press("Backspace")
    page.wait_for_timeout(150)
    actual_prefix = body.evaluate("""root => {
        const selection = window.getSelection();
        if (!selection.rangeCount || !selection.isCollapsed || !root.contains(selection.anchorNode)) return null;
        const range = document.createRange(); range.selectNodeContents(root);
        range.setEnd(selection.anchorNode, selection.anchorOffset);
        return range.toString();
    }""")
    if actual_prefix is None or _compact(actual_prefix) != _compact(prefix):
        raise PublisherError("Positioning WeChat article image", "光标与图片标记位置不一致，已停止。")


def _prepare_inline(page: Page, content: ContentVariant) -> None:
    blocks = _article_blocks(content)
    html_parts: list[str] = []
    text_parts: list[str] = []
    slots: list[tuple[int, str, str]] = []
    nonce = uuid.uuid4().hex
    for block in blocks:
        if isinstance(block, TextBlock):
            rendered = render_html(block.text)
            html_parts.append(rendered)
            text_parts.append(rendered)
        else:
            token = f"ALARKIVE_IMAGE_{nonce}_{block.index}"
            slots.append((block.index, token, _html_text(page, "".join(text_parts))))
            html_parts.append(f"<p>{token}</p>")
    html = "".join(html_parts)
    expected = _html_text(page, html)
    final_text = _html_text(page, "".join(text_parts))
    _ensure_empty(page)
    _title(page).fill(content.title)
    _paste_html(page, html)
    _title(page).focus()
    page.wait_for_timeout(700)
    _verify(page, content.title, expected, [])
    sources: list[str] = []
    prefixes: list[str] = []
    for index, token, prefix in slots:
        previous_ids = [img["file_id"] for img in _snapshot(page)["images"]]
        _select_image_slot(page, token, prefix)
        expected = expected.replace(token, "", 1)
        sources.append(_upload_image(page, content.images[index - 1], len(sources) + 1, previous_ids))
        prefixes.append(prefix)
        _verify(page, content.title, expected, sources, prefixes)
    _title(page).focus()
    page.wait_for_timeout(1500)
    _verify(page, content.title, final_text, sources, prefixes)


def _prepare_content(page: Page, content: ContentVariant) -> None:
    _, validation = validate_inline_image_text(content.body, len(content.images))
    if validation.has_markers:
        _prepare_inline(page, content)
    else:
        _prepare_append(page, content)


def run_wechat_article(page: Page, post: PostContent, controller: WorkflowController | None = None) -> Page:
    controller = controller or CLIWorkflowController()
    content = post.wechat_long
    if content is None:
        raise PublisherError("Preparing WeChat article", "Package 不包含 wechat_long。")
    controller.step(TARGET, "login", "检查微信公众号登录状态")
    run_step("Checking WeChat article login", lambda: _check_login(page, controller))
    controller.step(TARGET, "editor", "打开微信公众号长文编辑器")
    editor = run_step("Opening WeChat article editor", lambda: _open_editor(page))
    controller.step(TARGET, "content", "填写长文标题、完整正文和图片")
    run_step("Preparing WeChat article content", lambda: _prepare_content(editor, content))
    controller.step(TARGET, "verify", "标题、正文、图片及重绘校验通过；等待人工检查")
    return editor
