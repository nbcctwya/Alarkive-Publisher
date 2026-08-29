from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

from alarkive_publisher.content import ContentError, load_post


VERSION = "v0.1.1"


def _configure_output_encoding() -> None:
    """Keep Unicode task names and titles printable in Windows consoles."""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Fill Xiaohongshu, Baijiahao, and WeChat image posts in order, "
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
    print("Xiaohongshu")
    print("Title:")
    print(post.xiaohongshu.title)
    print()
    print("Content:")
    print(f"{len(post.xiaohongshu.body)} characters")
    print()
    print("Images:")
    for image in post.xiaohongshu.images:
        print(image.name)
    print()
    print("Baijiahao")
    print("Title:")
    print(post.baijiahao.title)
    print()
    print("Content:")
    print(f"{len(post.baijiahao.body)} characters")
    print()
    print("Images:")
    for image in post.baijiahao.images:
        print(image.name)
    print()
    print("WeChat")
    print("Title:")
    print(post.wechat.title)
    print()
    print("Content:")
    print(f"{len(post.wechat.body)} characters")
    print()
    print("Images:")
    for image in post.wechat.images:
        print(image.name)
    print()

    try:
        from alarkive_publisher.xiaohongshu import start_browser, run_xiaohongshu
        from alarkive_publisher.baijiahao import run_baijiahao
        from alarkive_publisher.wechat import run_wechat
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
    context = None
    playwright = None
    page = None
    current_step = "Starting browser"
    failure_screenshot = "xiaohongshu-failure.png"

    try:
        current_step = "Starting browser"
        print("[2/17] Starting browser...")
        playwright, context, page = start_browser(project_root)

        print()
        print("--- Xiaohongshu ---")
        print()
        current_step = "Xiaohongshu"
        run_xiaohongshu(page, post)
        print("[7/17] Xiaohongshu ready.")
        print()
        print("================================")
        print("Xiaohongshu ready.")
        print()
        print("✓ Images uploaded")
        print("✓ Title filled")
        print("✓ Content filled")
        print()
        print("The Publish button was NOT clicked.")
        print("Please inspect Xiaohongshu in the browser.")
        print()
        print("Press Enter to continue to Baijiahao...")
        print("================================")
        input()

        print()
        print("--- Baijiahao ---")
        print()
        current_step = "Baijiahao"
        failure_screenshot = "baijiahao-failure.png"
        run_baijiahao(page, post)
        print("[12/17] Baijiahao ready.")
        print()
        print("================================")
        print("Baijiahao ready.")
        print()
        print("✓ Images inserted")
        print("✓ Title filled")
        print("✓ Content filled")
        print()
        print("The Publish button was NOT clicked.")
        print("Please inspect Baijiahao in the browser.")
        print()
        print("Press Enter to continue to WeChat...")
        print("================================")
        input()

        print()
        print("--- WeChat ---")
        print()
        current_step = "WeChat"
        failure_screenshot = "wechat-failure.png"
        page = run_wechat(page, post)
        print("[17/17] WeChat ready.")
        print()
        print("================================")
        print("WeChat ready.")
        print()
        print("✓ Images uploaded")
        print("✓ Title filled")
        print("✓ Content filled")
        print()
        print("DRY RUN COMPLETE")
        print()
        print("The final Publish button was NOT clicked.")
        print("Please inspect the WeChat sticker post manually in the browser.")
        print()
        print("Press Enter to close Alarkive Publisher...")
        print("================================")
        input()
    except Exception as exc:
        print()
        error_step = getattr(exc, "step", current_step)
        print(f"ERROR during {error_step} step:", file=sys.stderr)
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        traceback.print_exception(exc, file=sys.stderr)
        if current_step == "WeChat" and context is not None and context.pages:
            page = context.pages[-1]
        if page is not None:
            try:
                screenshot_path = debug_dir / failure_screenshot
                page.screenshot(path=str(screenshot_path), full_page=True)
                print(
                    f"Debug screenshot saved to: {screenshot_path}",
                    file=sys.stderr,
                )
            except Exception as screenshot_error:
                print(
                    f"Could not save debug screenshot: {type(screenshot_error).__name__}: "
                    f"{screenshot_error}",
                    file=sys.stderr,
                )
        if context is not None:
            print(
                "The browser was left open for inspection. "
                "Press Enter to close it..."
            )
            try:
                input()
            except EOFError:
                pass
        return 1
    finally:
        if context is not None:
            try:
                context.close()
            except Exception:
                pass
        if playwright is not None:
            try:
                playwright.stop()
            except Exception:
                pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
