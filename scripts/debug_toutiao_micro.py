"""Run the existing single-platform workflow in visible Chrome, stopping at preview.

With no argument, use the latest Package by manifest created_at. Package
content is never rewritten. All reports stay in the ignored debug directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import traceback
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alarkive_publisher.content import load_post  # noqa: E402
from alarkive_publisher.toutiao_micro import capture_debug_snapshot  # noqa: E402
from alarkive_publisher.workflow import run_single_platform_workflow  # noqa: E402
from alarkive_publisher.workflow_controller import CLIWorkflowController  # noqa: E402


def latest_package() -> Path:
    packages = []
    for manifest in (PROJECT_ROOT / "posts").glob("*/manifest.json"):
        data = json.loads(manifest.read_text(encoding="utf-8"))
        packages.append((datetime.fromisoformat(data["created_at"]), manifest.parent))
    if not packages:
        raise ValueError("posts 中没有 Package。")
    return max(packages, key=lambda entry: (entry[0], entry[1].name))[1]


def _fingerprint(folder: Path) -> dict[str, str]:
    return {
        path.relative_to(folder).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in folder.rglob("*") if path.is_file()
    }


def run_package(folder: Path) -> int:
    folder = folder.resolve()
    before = _fingerprint(folder)
    post = load_post(folder)
    print(f"PACKAGE: {post.id}; variant=toutiao_short", flush=True)
    playwright = context = page = None

    def remember_browser(p, c, current_page):
        nonlocal playwright, context, page
        playwright, context, page = p, c, current_page

    def hold(name: str):
        while True:
            command = input(f"{name}: state = 重新检查截图；Enter = 关闭浏览器 > ").strip()
            if command != "state":
                break
            page.wait_for_timeout(1000)
            capture_debug_snapshot(page, name=name)
            print(f"PACKAGE_UNCHANGED: {before == _fingerprint(folder)}", flush=True)

    class DiagnosticController(CLIWorkflowController):
        def ready(self, platform, message, prompt):
            page.wait_for_timeout(2000)
            capture_debug_snapshot(page, name="ready")
            print(f"READY: {message}; final Publish NOT clicked", flush=True)
            print(f"PACKAGE_UNCHANGED: {before == _fingerprint(folder)}", flush=True)
            hold("ready")

    try:
        run_single_platform_workflow(
            post, PROJECT_ROOT, "toutiao_micro", DiagnosticController(),
            on_browser_started=remember_browser,
        )
        # The workflow closes both handles after the ready pause is released.
        context = playwright = None
        return 0
    except Exception:
        traceback.print_exc()
        if page is not None and not page.is_closed():
            capture_debug_snapshot(page)
            hold("failure")
        return 1
    finally:
        print(f"PACKAGE_UNCHANGED: {before == _fingerprint(folder)}", flush=True)
        if context is not None:
            context.close()
        if playwright is not None:
            playwright.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("post_folder", nargs="?", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    raise SystemExit(run_package(args.post_folder or latest_package()))
