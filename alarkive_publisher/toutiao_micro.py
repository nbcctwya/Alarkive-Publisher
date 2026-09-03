"""Prepare a Toutiao micro post as text plus a separate, ordered image group.

All micro-post browser behavior lives here, independent of the article
publisher. There is deliberately no final publish or draft-save action.
"""

from __future__ import annotations

import json
import logging
import re
import time
from html import escape
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError, Locator, Page

from .content import ContentVariant, PostContent
from .publisher_common import PublisherError, run_step
from .renderer import render_plain
from .workflow_controller import CLIWorkflowController, WorkflowController


LOGGER = logging.getLogger(__name__)
EDITOR_URL = "https://mp.toutiao.com/profile_v4/weitoutiao/publish"
_ROOT = ".weitoutiao_publish-wrapper"
_DRAWER = ".mp-ic-img-drawer:visible"
_FINAL_CONTROL = re.compile(r"发布|发表|群发|提交|publish|submit", re.I)


def _micro_text(content: ContentVariant) -> str:
    # The micro editor has no title field: its first paragraph is the title.
    # No inline-image parsing or article rendering is used for short content.
    return f"{content.title.strip()}\n\n{render_plain(content.body)}"


def _normalized_text(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\xa0", " ").strip()


def _editor(page: Page) -> Locator:
    return page.locator(f'{_ROOT} .ProseMirror[contenteditable="true"]')


def _read_text(editor: Locator) -> str:
    return editor.evaluate(r"""el => {
        return Array.from(el.children).map(block => {
            const copy = block.cloneNode(true);
            copy.querySelectorAll('.syl-placeholder, br.ProseMirror-trailingBreak').forEach(el => el.remove());
            copy.querySelectorAll('br').forEach(el => el.replaceWith('\n'));
            return (copy.textContent || '').replace(/\n$/, '');
        }).join('\n');
    }""")


def _safe_click(control: Locator, allowed_labels: set[str]) -> None:
    label = control.inner_text().strip()
    metadata = " ".join(control.get_attribute(name) or "" for name in (
        "aria-label", "title", "class", "data-e2e",
    ))
    if (
        label not in allowed_labels
        or _FINAL_CONTROL.search(label + " " + metadata)
        or control.get_attribute("type") == "submit"
    ):
        raise PublisherError("Checking Toutiao micro control", "Refusing an unsafe micro-post control.")
    control.click()


def _is_login(page: Page) -> bool:
    return bool(re.search(r"/auth/|/login|passport", page.url, re.I))


def _navigate(page: Page) -> None:
    try:
        page.goto(EDITOR_URL, wait_until="commit", timeout=60_000)
    except PlaywrightError as exc:
        if not re.search(r"ERR_ABORTED|interrupted by another navigation", str(exc), re.I):
            raise
        if urlsplit(page.url).hostname != "mp.toutiao.com":
            raise


def _open_editor(page: Page, controller: WorkflowController) -> None:
    _navigate(page)
    deadline = time.monotonic() + 45
    retried = False
    while time.monotonic() < deadline:
        if _editor(page).count() == 1 and _editor(page).is_visible():
            break
        if _is_login(page):
            controller.wait_for_user(
                "toutiao_micro", "login", "请在 Chrome 中完成头条登录，然后点击“继续”。", "登录完成后继续。"
            )
            _navigate(page)
            deadline = time.monotonic() + 45
            retried = False
            continue
        retry = page.get_by_role("button", name="重试加载", exact=True)
        if not retried and retry.count() and retry.is_visible():
            _safe_click(retry, {"重试加载"})
            retried = True
        page.wait_for_timeout(250)
    else:
        raise PublisherError("Opening Toutiao micro editor", "微头条编辑器未加载，请检查 Chrome 中的网络或登录状态。")

    # Only dismiss the known assistant mask, never an upload drawer.
    mask = page.locator(".publish-assistant-old-drawer .byte-drawer-mask:visible")
    if mask.count():
        mask.click(force=True)
        mask.wait_for(state="hidden")
    if _normalized_text(_read_text(_editor(page))) or _attached_signatures(page):
        raise PublisherError("Opening Toutiao micro editor", "编辑器已有内容，已停止以免覆盖现有微头条。请打开空白创作页后重试。")


def _fill_text(page: Page, expected: str) -> None:
    editor = _editor(page)
    editor.click()
    # Go through ProseMirror's paste handler so its model and undo history
    # receive the text. Direct innerHTML/textContent edits disappear on rerender.
    paragraphs = "".join(f"<p>{escape(line) if line else '<br>'}</p>" for line in expected.split("\n"))
    editor.evaluate("""(el, payload) => {
        const data = new DataTransfer();
        data.setData('text/plain', payload.text);
        data.setData('text/html', payload.html);
        el.dispatchEvent(new ClipboardEvent('paste', {
            clipboardData: data, bubbles: true, cancelable: true
        }));
    }""", {"text": expected, "html": paragraphs})
    page.wait_for_timeout(300)
    _assert_text(page, expected)


def _assert_text(page: Page, expected: str) -> None:
    editor = _editor(page)
    if _normalized_text(_read_text(editor)) != _normalized_text(expected):
        raise PublisherError("Checking Toutiao micro text", "微头条标题或正文与 Package 不一致。")
    if editor.locator("img").count():
        raise PublisherError("Checking Toutiao micro text", "微头条图片必须位于独立图片区，不能插入正文。")


def _image_key(url: str) -> str:
    # CDN transforms/signatures differ between upload and attachment views.
    return urlsplit(url).path.split("~", 1)[0] if url else ""


def _uploaded_signatures(drawer: Locator) -> tuple[str, ...]:
    urls = drawer.locator(".upload-image-wrapper .image-list .img-wrap img").evaluate_all(
        "els => els.map(el => el.getAttribute('src') || '')"
    )
    return tuple(_image_key(url) for url in urls)


def _attached_signatures(page: Page) -> tuple[str, ...]:
    urls = page.locator(f"{_ROOT} .upload-box .img-box-item > .item").evaluate_all(r"""els => els.map(el => {
        const match = el.style.backgroundImage.match(/^url\(["']?(.*?)["']?\)$/);
        return match ? match[1] : '';
    })""")
    return tuple(_image_key(url) for url in urls)


def _wait_for_upload(page: Page, drawer: Locator, previous: tuple[str, ...]) -> tuple[str, ...]:
    deadline = time.monotonic() + 120
    expected_count = len(previous) + 1
    while time.monotonic() < deadline:
        if re.search(r"上传失败|上传出错|格式不支持|超过.*限制", drawer.inner_text()):
            raise PublisherError("Uploading Toutiao micro images", "微头条图片上传失败，请检查上传面板。")
        signatures = _uploaded_signatures(drawer)
        # A success node is a model/status marker, even when it has no visible
        # dimensions. Require both that marker and a loaded preview per image.
        successes = drawer.locator(".pic-select-image-item .success").count()
        loaded = drawer.locator(".image-list .img-wrap img").evaluate_all(
            "els => els.every(el => el.complete && el.naturalWidth > 0)"
        )
        if len(signatures) == expected_count and successes == expected_count and loaded:
            if signatures[:-1] != previous or not signatures[-1]:
                raise PublisherError("Uploading Toutiao micro images", "上传列表未按 Package 图片顺序追加。")
            return signatures
        page.wait_for_timeout(250)
    raise PublisherError("Uploading Toutiao micro images", "等待微头条图片上传完成超时。")


def _upload_images(page: Page, images: tuple[Path, ...]) -> tuple[str, ...]:
    if not images:
        return ()
    if _attached_signatures(page) or page.locator(_DRAWER).count():
        raise PublisherError("Uploading Toutiao micro images", "存在未处理的图片或上传面板，已停止以免混入旧图片。")
    trigger = page.locator(f"{_ROOT} .weitoutiao-image-plugin").get_by_role("button", name="图片", exact=True)
    _safe_click(trigger, {"图片"})
    drawer = page.locator(_DRAWER)
    drawer.wait_for(state="visible")
    if _uploaded_signatures(drawer):
        raise PublisherError("Uploading Toutiao micro images", "上传面板已有图片，已停止以免使用旧素材。")
    signatures: tuple[str, ...] = ()
    for index, path in enumerate(images, 1):
        file_input = drawer.locator('.upload-handler input[type="file"][accept="image/*"]')
        file_input.set_input_files(str(path.resolve()), timeout=30_000)
        signatures = _wait_for_upload(page, drawer, signatures)
        LOGGER.info("Toutiao micro image uploaded: %s (%d/%d)", path.name, index, len(images))
    # This confirmation belongs only to the image upload panel. It attaches
    # the selected image group and never publishes the micro post.
    _safe_click(drawer.locator('[data-e2e="imageUploadConfirm-btn"]'), {"确定"})
    drawer.wait_for(state="hidden")
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if _attached_signatures(page) == signatures:
            return signatures
        page.wait_for_timeout(250)
    raise PublisherError("Checking Toutiao micro images", "独立图片区的图片数量或顺序与上传列表不一致。")


def _verify_ready(page: Page, expected_text: str, signatures: tuple[str, ...]) -> None:
    # Recheck after blur/rerender; filling the live DOM alone is insufficient.
    _editor(page).evaluate("el => el.blur()")
    page.wait_for_timeout(1000)
    _assert_text(page, expected_text)
    if _attached_signatures(page) != signatures:
        raise PublisherError("Checking Toutiao micro images", "微头条图片在重绘后发生变化。")
    if page.locator(_DRAWER).count():
        raise PublisherError("Checking Toutiao micro images", "图片上传面板尚未关闭。")
    final = page.locator(f"{_ROOT} button.publish-content")
    if final.count() != 1 or not final.is_visible() or final.inner_text().strip() != "发布":
        raise PublisherError("Checking Toutiao micro preview", "无法找到供人工检查的微头条发布区域。")
    LOGGER.info("Toutiao micro ready: %d images; final Publish NOT clicked; draft saving not requested", len(signatures))


def capture_debug_snapshot(page: Page, *, name: str = "failure") -> None:
    """Keep local diagnostics out of posts and out of version control."""
    debug = Path(__file__).resolve().parents[1] / "debug"
    try:
        debug.mkdir(exist_ok=True)
        (debug / f"toutiao_micro-{name}.json").write_text(json.dumps({
            "url": page.url,
            "text": _read_text(_editor(page)) if _editor(page).count() == 1 else "",
            "image_keys": _attached_signatures(page),
            "visible_text": page.locator("body").inner_text(),
            "final_publish_clicked": False,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        page.screenshot(path=str(debug / f"toutiao_micro-{name}.png"), full_page=True)
    except Exception:
        LOGGER.warning("Could not capture Toutiao micro diagnostics", exc_info=True)


def run_toutiao_micro(page: Page, post: PostContent, controller: WorkflowController | None = None) -> None:
    """Fill only ``post.toutiao_short`` and stop for manual review."""
    content = post.toutiao_short
    if content is None:
        raise PublisherError("Loading Toutiao micro content", "The Package does not contain toutiao_short content.")
    if len(content.images) > 18:
        raise PublisherError("Loading Toutiao micro images", "微头条最多支持 18 张图片。")
    for path in content.images:
        if not path.is_file() or path.stat().st_size > 20 * 1024 * 1024:
            raise PublisherError("Loading Toutiao micro images", f"图片不存在或超过 20MB：{path.name}")
    controller = controller or CLIWorkflowController()
    expected_text = _micro_text(content)
    try:
        controller.step("toutiao_micro", "opening_editor", "检查登录并打开微头条创作页")
        run_step("Opening Toutiao micro editor", lambda: _open_editor(page, controller))
        controller.step("toutiao_micro", "filling_content", "填写微头条标题和正文")
        run_step("Filling Toutiao micro text", lambda: _fill_text(page, expected_text))
        controller.step("toutiao_micro", "uploading_images", f"上传 {len(content.images)} 张独立配图")
        signatures = run_step("Uploading Toutiao micro images", lambda: _upload_images(page, content.images))
        controller.step("toutiao_micro", "final_check", "核对文字和图片，停在发布按钮之前")
        run_step("Checking Toutiao micro preview", lambda: _verify_ready(page, expected_text, signatures))
    except Exception:
        capture_debug_snapshot(page)
        raise


__all__ = ["EDITOR_URL", "run_toutiao_micro"]
