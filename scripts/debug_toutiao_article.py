"""Real-Chrome diagnostics for the Toutiao article publisher.

This helper intentionally delegates all browser and publisher behavior to the
production ``start_browser`` and ``run_toutiao_article`` functions.  It never
clicks a final publish control and keeps Chrome open after a failed run so the
page can be inspected manually.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
import traceback
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alarkive_publisher.content import load_post  # noqa: E402
from alarkive_publisher.inline_images import TextBlock, validate_inline_image_text  # noqa: E402
from alarkive_publisher.workflow import run_single_platform_workflow  # noqa: E402
from alarkive_publisher.toutiao_article import (  # noqa: E402
    _body_locator,
    _capture_debug_snapshot,
    _check_login,
    _control_label,
    _editor_content_sequence,
    _editor_image_count,
    _image_trigger,
    _open_editor,
    _title_locator,
    run_toutiao_article,
)
from alarkive_publisher.workflow_controller import CLIWorkflowController  # noqa: E402
from alarkive_publisher.xiaohongshu import start_browser  # noqa: E402


def _describe(locator) -> dict[str, object]:
    return locator.evaluate(
        """
        element => ({
            tag: element.tagName,
            id: element.id || '',
            class: String(element.className || ''),
            role: element.getAttribute('role') || '',
            ariaLabel: element.getAttribute('aria-label') || '',
            placeholder: element.getAttribute('placeholder') ||
                element.getAttribute('data-placeholder') || '',
            contenteditable: element.getAttribute('contenteditable') || '',
            outerHTML: element.outerHTML.slice(0, 1200),
        })
        """
    )


def _wait_before_close(message: str) -> None:
    try:
        input(message)
    except EOFError:
        pass


def _capture_ready_snapshot(page) -> None:
    debug_dir = PROJECT_ROOT / "debug"
    debug_dir.mkdir(parents=True, exist_ok=True)
    body = _body_locator(page)
    summary = {
        "current_url": page.url,
        "page_title": page.title(),
        "logical_image_count": _editor_image_count(body),
        "content_sequence": _editor_content_sequence(body),
    }
    (debug_dir / "toutiao_article-ready.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    page.screenshot(
        path=str(debug_dir / "toutiao_article-ready.png"), full_page=True
    )


def inspect_editor() -> int:
    playwright = context = None
    try:
        playwright, context, page = start_browser(PROJECT_ROOT)
        controller = CLIWorkflowController()
        _check_login(page, controller)
        _open_editor(page)
        title = _title_locator(page)
        body = _body_locator(page)
        trigger = _image_trigger(page, body)
        summary = {
            "current_url": page.url,
            "page_title": page.title(),
            "frame_urls": [frame.url for frame in page.frames],
            "title": _describe(title),
            "body": _describe(body),
            "body_is_prosemirror": "prosemirror"
            in str(body.get_attribute("class") or "").lower(),
            "editor_image_count": body.locator("img").count(),
            "image_trigger": _describe(trigger) if trigger is not None else None,
            "image_trigger_label": _control_label(trigger)[:2000]
            if trigger is not None
            else None,
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        _capture_debug_snapshot(page)
        _wait_before_close("INSPECTION_READY - press Enter to close Chrome> ")
        return 0
    except BaseException:
        traceback.print_exc()
        _wait_before_close("INSPECTION_FAILED - inspect Chrome, then press Enter to close> ")
        return 1
    finally:
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


def _posts_fingerprint() -> dict[str, str]:
    root = PROJECT_ROOT / "posts"
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


def inspect_autosave_request() -> int:
    """Observe editor startup requests, aborting the ambiguous publish endpoint.

    Do not record bodies, headers, cookies, titles, content, or credentials.
    This mode never types article content or activates any page controls.
    """
    playwright = context = None
    try:
        playwright, context, page = start_browser(PROJECT_ROOT)

        def block_and_describe(route) -> None:
            request = route.request
            payload = request.post_data or ""
            try:
                fields = json.loads(payload)
            except ValueError:
                fields = {key: values[-1] for key, values in parse_qs(payload).items()}
            if not isinstance(fields, dict):
                fields = {}
            safe_flags = {}
            for key in ("save", "publish_type", "type", "action", "status", "save_type", "is_draft", "article_type", "mode"):
                value = fields.get(key)
                if isinstance(value, (str, int, bool)) and len(str(value)) < 40:
                    safe_flags[key] = value
            route.abort()
            print("BLOCKED_EDITOR_REQUEST: " + json.dumps({
                "path": urlsplit(request.url).path,
                "method": request.method,
                "field_names": sorted(fields),
                "flags": safe_flags,
            }, ensure_ascii=False), flush=True)

        page.route("**/mp/agw/article/publish*", block_and_describe)
        page.goto("https://mp.toutiao.com/profile_v4/graphic/publish", wait_until="domcontentloaded")
        page.wait_for_timeout(15000)
        script_urls = page.evaluate("""() => [...new Set([
            ...Array.from(document.scripts, s => s.src),
            ...performance.getEntriesByType('resource').filter(r => r.initiatorType === 'script').map(r => r.name)
        ])].filter(Boolean)""")
        for url in script_urls:
            if not urlsplit(url).path.endswith('.js'):
                continue
            try:
                source = page.request.get(url, timeout=10000).text()
            except Exception:
                continue
            matches = list(re.finditer(r'(?:save:[01]|save=[01]|article/publish|草稿保存中)', source))
            if matches:
                print("EDITOR_SOURCE: " + urlsplit(url).path, flush=True)
                for match in matches[:16]:
                    print(source[max(0, match.start()-180):match.end()+220], flush=True)
        print("PROBE_READY - no article was typed; ambiguous requests were aborted", flush=True)
        _wait_before_close("Press Enter to close probe Chrome> ")
        return 0
    finally:
        if context is not None:
            context.close()
        if playwright is not None:
            playwright.stop()


def _watch_network(page, events: list[dict]) -> None:
    def record_response(response) -> None:
        url = urlsplit(response.url)
        if response.request.method == "GET" and not any(word in url.path.lower() for word in ("draft", "upload", "save", "sync")):
            return
        event = {"host": url.hostname, "path": url.path, "method": response.request.method, "status": response.status}
        if "draft" in url.path.lower() or url.path == "/mp/agw/article/publish":
            try:
                result = response.json()
                if isinstance(result, dict):
                    event["result"] = {
                        key: result[key] for key in ("err_no", "code", "errno", "status_code")
                        if isinstance(result.get(key), (str, int, bool))
                    }
                    event["result_keys"] = sorted(result)
                    for key in ("message", "reason"):
                        value = result.get(key)
                        if isinstance(value, str):
                            # Only platform error prose, never request content.
                            event[key] = re.sub(r"https?://\S+", "[URL omitted]", value)[:240]
            except Exception:
                pass
        events.append(event)
        if url.hostname == "mp.toutiao.com" and any(word in url.path for word in ("draft", "publish", "upload")):
            print("NETWORK: " + json.dumps(event), flush=True)

    def record_failure(request) -> None:
        url = urlsplit(request.url)
        if any(word in url.path.lower() for word in ("draft", "upload")):
            event = {"path": url.path, "method": request.method, "failure": request.failure}
            events.append(event)
            print("NETWORK: " + json.dumps(event), flush=True)

    page.on("response", record_response)
    page.on("requestfailed", record_failure)
    page.on("websocket", lambda socket: events.append({
        "websocket": urlsplit(socket.url).path,
        "host": urlsplit(socket.url).hostname,
    }))


def _hold_for_inspection(page, events: list[dict], message: str) -> None:
    while True:
        command = input(message + " [state / Enter to close]> ").strip().lower()
        if command != "state":
            return
        page.wait_for_timeout(1000)
        _capture_debug_snapshot(page)
        print("DRAFT: " + page.locator('.footer-draft-save').inner_text(), flush=True)
        print("NETWORK: " + json.dumps(events), flush=True)
        print("RESOURCE_PATHS: " + json.dumps(page.evaluate("""() => [...new Set(performance.getEntriesByType('resource').filter(r => !['script', 'css', 'img', 'link', 'font'].includes(r.initiatorType)).map(r => {const url = new URL(r.name); return url.host + url.pathname;}))]""")), flush=True)


def run_package(post_folder: Path, *, append_images: bool = False) -> int:
    before_posts = _posts_fingerprint()
    post = load_post(post_folder.resolve())
    if append_images:
        if post.public_long is None:
            raise ValueError("The Package must contain public_long.")
        blocks, _ = validate_inline_image_text(
            post.public_long.body, len(post.public_long.images)
        )
        body = "\n\n".join(block.text.strip() for block in blocks if isinstance(block, TextBlock))
        post = replace(post, public_long=replace(post.public_long, body=body))
    print(f"PACKAGE: {post.id}; mode={'append-images' if append_images else 'inline-markers'}", flush=True)
    playwright = context = page = None
    network_events: list[dict] = []

    def remember_browser(p, c, current_page) -> None:
        nonlocal playwright, context, page
        playwright, context, page = p, c, current_page
        _watch_network(page, network_events)
        def guard_draft_mode(route) -> None:
            # Editor startup emits save=0 without any click. Restrict this
            # diagnostic run to exactly that observed automatic draft mode.
            fields = parse_qs(route.request.post_data or "")
            save = fields.get("save", [])
            if save != ["0"]:
                print("BLOCKED non-draft article request", flush=True)
                route.abort()
                return
            print("AUTOSAVE_MODE: save=0 (editor startup mode)", flush=True)
            route.continue_()
        page.route("**/mp/agw/article/publish*", guard_draft_mode)

    class DiagnosticController(CLIWorkflowController):
        def ready(self, platform: str, message: str, prompt: str) -> None:
            _capture_ready_snapshot(page)
            super().ready(platform, message, "请检查内容并由用户自行决定是否发布。")

    try:
        run_single_platform_workflow(
            post,
            PROJECT_ROOT,
            "toutiao_article",
            DiagnosticController(),
            on_browser_started=remember_browser,
        )
        return 0
    except BaseException:
        traceback.print_exc()
        if page is not None:
            _capture_debug_snapshot(page)
            _hold_for_inspection(page, network_events, "SMOKE_FAILED - Chrome retained")
        return 1
    finally:
        (PROJECT_ROOT / "debug" / "toutiao_article-network.json").write_text(
            json.dumps(network_events, indent=2), encoding="utf-8"
        )
        after_posts = _posts_fingerprint()
        print(f"POSTS_UNCHANGED: {before_posts == after_posts}", flush=True)
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("inspect")
    subparsers.add_parser("inspect-autosave")
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("post_folder", type=Path)
    run_parser.add_argument(
        "--append-images", action="store_true",
        help="Debug only: remove markers in memory and append all original images.",
    )
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args()
    if args.command == "inspect":
        return inspect_editor()
    if args.command == "inspect-autosave":
        return inspect_autosave_request()
    return run_package(args.post_folder, append_images=args.append_images)


if __name__ == "__main__":
    raise SystemExit(main())
