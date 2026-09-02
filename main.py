from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

from alarkive_publisher import __version__
from alarkive_publisher.content import ContentError, load_post


VERSION = f"v{__version__}"


def _configure_output_encoding() -> None:
    """Keep Unicode task names and titles printable in Windows consoles."""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Fill the available Baijiahao and WeChat image posts in order, "
            "then stop before publishing."
        )
    )
    parser.add_argument("post_folder", type=Path, help="Path to a post folder")
    return parser.parse_args()


def main() -> int:
    _configure_output_encoding()
    args = parse_args()
    post_folder = args.post_folder.expanduser().resolve()

    print(f"Alarkive Publisher {VERSION}")
    print()
    print("[1/17] Loading Alarkive Package...")

    try:
        post = load_post(post_folder)
    except ContentError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print()
    print("Package:")
    print(post.name)
    print()
    print("ID:")
    print(post.id)
    print()
    print("Created:")
    print(post.created_at)
    print()
    print("Package folder:")
    print(post.folder)
    print()
    for platform, label in (
        ("xiaohongshu", "Xiaohongshu"),
        ("baijiahao", "Baijiahao"),
        ("wechat", "WeChat"),
    ):
        content = getattr(post, platform)
        if content is None:
            continue
        print(label)
        print("Title:")
        print(content.title)
        print()
        print("Content:")
        print(f"{len(content.body)} characters")
        print()
        print("Images:")
        for image in content.images:
            print(image.name)
        print()

    try:
        from alarkive_publisher.workflow import run_publisher_workflow
        from alarkive_publisher.workflow_controller import CLIWorkflowController
    except ModuleNotFoundError as exc:
        if exc.name != "playwright":
            raise
        print(
            "Error: Playwright is not installed. Run "
            "'python -m pip install -r requirements.txt' first.",
            file=sys.stderr,
        )
        return 1

    project_root = Path(__file__).resolve().parent
    debug_dir = project_root / "debug"
    debug_dir.mkdir(parents=True, exist_ok=True)
    browser = {"playwright": None, "context": None, "page": None}

    def remember_browser(playwright, context, page) -> None:
        browser.update(playwright=playwright, context=context, page=page)

    try:
        print("[2/17] Starting browser...")
        run_publisher_workflow(
            post,
            project_root,
            CLIWorkflowController(),
            on_browser_started=remember_browser,
        )
    except Exception as exc:
        print()
        error_step = getattr(exc, "step", "Publisher workflow")
        print(f"ERROR during {error_step} step:", file=sys.stderr)
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        traceback.print_exception(exc, file=sys.stderr)
        page = browser["page"]
        context = browser["context"]
        if page is not None:
            try:
                error_text = f"{error_step} {exc}".lower()
                screenshot_name = (
                    "xiaohongshu-failure.png"
                    if "xiaohongshu" in error_text
                    else "baijiahao-failure.png"
                    if "baijiahao" in error_text
                    else "wechat-failure.png"
                    if "wechat" in error_text
                    else "publisher-failure.png"
                )
                screenshot_path = debug_dir / screenshot_name
                page.screenshot(path=str(screenshot_path), full_page=True)
                print(f"Debug screenshot saved to: {screenshot_path}", file=sys.stderr)
            except Exception as screenshot_error:
                print(
                    f"Could not save debug screenshot: {type(screenshot_error).__name__}: "
                    f"{screenshot_error}",
                    file=sys.stderr,
                )
        if context is not None:
            print("The browser was left open for inspection. Press Enter to close it...")
            try:
                input()
            except EOFError:
                pass
            try:
                context.close()
            except Exception:
                pass
        playwright = browser["playwright"]
        if playwright is not None:
            try:
                playwright.stop()
            except Exception:
                pass
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
