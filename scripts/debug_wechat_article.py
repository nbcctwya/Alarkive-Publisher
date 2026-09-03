"""Test the existing single-platform workflow in real Chrome, never publish.

The newest posts/ Package is selected by manifest created_at. --append-images
removes markers in memory for stage-one regression; no Package file is changed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import traceback
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alarkive_publisher.content import load_post
from alarkive_publisher.inline_images import IMAGE_MARKER_LINE_RE, TextBlock, validate_inline_image_text
from alarkive_publisher.wechat_article import BODY_SELECTOR, IMAGE_SELECTOR, _snapshot, _title
from alarkive_publisher.workflow import run_single_platform_workflow
from alarkive_publisher.workflow_controller import CLIWorkflowController


def latest_package() -> Path:
    manifests = list((ROOT / "posts").glob("*/manifest.json"))
    if not manifests:
        raise ValueError("posts 中没有 Package。")
    return max(manifests, key=lambda p: (
        datetime.fromisoformat(json.loads(p.read_text(encoding="utf-8"))["created_at"]), p.parent.name
    )).parent


def fingerprint() -> dict[str, str]:
    return {p.relative_to(ROOT / "posts").as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (ROOT / "posts").rglob("*") if p.is_file()}


def run(args) -> int:
    before = fingerprint()
    post = load_post((args.post_folder or latest_package()).resolve())
    if post.wechat_long is None:
        raise ValueError("最新 Package 没有 wechat_long。")
    if args.append_images:
        blocks, _ = validate_inline_image_text(post.wechat_long.body, len(post.wechat_long.images))
        post = replace(post, wechat_long=replace(post.wechat_long, body="\n\n".join(
            b.text.strip() for b in blocks if isinstance(b, TextBlock)
        )))
    if args.marker_order:
        order = [int(n) for n in args.marker_order.split(',')]
        if len(order) != len(set(order)) or any(n < 1 or n > len(post.wechat_long.images) for n in order):
            raise ValueError("marker-order 必须为互不重复的有效图片序号。")
        if len(order) > len(IMAGE_MARKER_LINE_RE.findall(post.wechat_long.body)):
            raise ValueError("原始正文没有足够的 marker 位置。")
        remaining = iter(order)
        def replace_marker(match):
            index = next(remaining, None)
            return f"[[image:{index}]]\n" if index is not None else ""
        post = replace(post, wechat_long=replace(post.wechat_long, body=IMAGE_MARKER_LINE_RE.sub(replace_marker, post.wechat_long.body)))
    print(f"PACKAGE: {post.id}; mode={'append' if args.append_images else 'inline'}; marker_order={args.marker_order or 'original'}", flush=True)
    playwright = context = page = None
    output = ROOT / "debug" / f"wechat_article-{datetime.now():%Y%m%d-%H%M%S}"
    output.mkdir(parents=True)
    uploads = []

    def remember(p, c, current):
        nonlocal playwright, context, page
        playwright, context, page = p, c, current
        def observe(response):
            url = urlsplit(response.url)
            if response.request.method == "POST" and any(word in url.path for word in ("file", "upload")):
                try:
                    result = response.json()
                    uploads.append({"path": url.path, "action": parse_qs(url.query).get("action"),
                                    "ret": result.get("base_resp", {}).get("ret"), "file_id": result.get("content")})
                except Exception:
                    pass
        c.on("response", observe)

    def current_editor():
        candidates = [p for p in context.pages if not p.is_closed() and p.locator(BODY_SELECTOR).count()]
        return candidates[-1] if candidates else page

    def capture(name):
        editor = current_editor()
        if editor.locator(BODY_SELECTOR).count():
            snapshot = _snapshot(editor)
            (output / f"{name}.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({"text_chars": len(snapshot["text"]), "images": len(snapshot["images"]),
                              "image_text_offsets": [len(i["before"]) for i in snapshot["images"]]}, ensure_ascii=False), flush=True)
            _title(editor).scroll_into_view_if_needed()
            editor.screenshot(path=str(output / f"{name}-top.png"))
            for i, img in enumerate(editor.locator(BODY_SELECTOR + " " + IMAGE_SELECTOR).all(), 1):
                img.scroll_into_view_if_needed()
                editor.screenshot(path=str(output / f"{name}-image-{i}.png"))
            _title(editor).scroll_into_view_if_needed()
        else:
            editor.screenshot(path=str(output / f"{name}.png"))
        print(f"REPORT: {output}; POSTS_UNCHANGED: {before == fingerprint()}", flush=True)

    def hold(name):
        while True:
            command = input(f"{name}: state = 截图; Enter = 关闭 Chrome > ").strip()
            if command != "state":
                return
            capture(name)

    class Controller(CLIWorkflowController):
        def ready(self, platform, message, prompt):
            current_editor().wait_for_timeout(2000)
            capture("ready")
            print(f"READY: {message}; no final Publish, Send, Submit or Save clicked", flush=True)
            if not args.auto_close:
                hold("ready")

    try:
        run_single_platform_workflow(post, ROOT, "wechat_article", Controller(), on_browser_started=remember)
        context = playwright = None
        return 0
    except Exception:
        traceback.print_exc()
        if context:
            capture("failure")
            if not args.auto_close:
                hold("failure")
        return 1
    finally:
        (output / "uploads.json").write_text(json.dumps(uploads, indent=2), encoding="utf-8")
        print(f"POSTS_UNCHANGED: {before == fingerprint()}", flush=True)
        if context:
            context.close()
        if playwright:
            playwright.stop()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("post_folder", nargs="?", type=Path)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--append-images", action="store_true")
    modes.add_argument("--marker-order", help="Debug only: reassign first marker slots in memory, e.g. 4,2; unused images append.")
    parser.add_argument("--auto-close", action="store_true", help="Close this diagnostic Chrome after verification.")
    raise SystemExit(run(parser.parse_args()))
