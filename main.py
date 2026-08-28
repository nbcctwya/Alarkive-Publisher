from __future__ import annotations

import argparse
import sys
from pathlib import Path

from alarkive_publisher.content import ContentError, load_post


VERSION = "v0.0.1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fill a Xiaohongshu image post and stop before publishing."
    )
    parser.add_argument("post_folder", type=Path, help="Path to a post folder")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    post_folder = args.post_folder.expanduser().resolve()

    print(f"Alarkive Publisher {VERSION}")
    print()
    print("[1/7] Loading content...")

    try:
        post = load_post(post_folder)
    except ContentError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print()
    print("Post folder:")
    print(post.folder)
    print()
    print("Title:")
    print(post.title)
    print()
    print("Content:")
    print(f"{len(post.body)} characters")
    print()
    print("Images:")
    for image in post.images:
        print(image.name)
    print()

    try:
        from alarkive_publisher.xiaohongshu import run_dry_run
    except ModuleNotFoundError as exc:
        if exc.name != "playwright":
            raise
        print(
            "Error: Playwright is not installed. Run "
            "'python -m pip install -r requirements.txt' first.",
            file=sys.stderr,
        )
        return 1

    try:
        run_dry_run(post, project_root=Path(__file__).resolve().parent)
    except Exception:
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
