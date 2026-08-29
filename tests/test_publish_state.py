from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from alarkive_publisher.web.publish_state import (
    default_publish_state,
    load_publish_state,
    mark_published,
    mark_unpublished,
    save_publish_state,
    update_workflow,
)


class PublishStateTests(unittest.TestCase):
    def test_missing_sidecar_uses_default_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            state = load_publish_state(folder)

            self.assertFalse(state["published"])
            self.assertIsNone(state["published_at"])
            self.assertEqual(state["workflow"]["status"], "idle")
            self.assertFalse((folder / "publish-state.json").exists())

    def test_mark_published_sets_only_local_marker_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            state = default_publish_state()
            save_publish_state(folder, state)

            published = mark_published(folder)

            self.assertTrue(published["published"])
            self.assertIsNotNone(published["published_at"])
            self.assertEqual(published["workflow"], state["workflow"])

    def test_mark_unpublished_preserves_workflow_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            state = default_publish_state()
            state["published"] = True
            state["published_at"] = "2026-08-29T20:10:00+08:00"
            save_publish_state(folder, state)
            update_workflow(
                folder,
                status="failed",
                current_platform="baijiahao",
                current_step="uploading_images",
                message="test failed",
                platform="baijiahao",
                platform_status="failed",
                platform_message="test",
                error={"message": "test"},
            )
            before = load_publish_state(folder)["workflow"]

            result = mark_unpublished(folder)

            self.assertFalse(result["published"])
            self.assertIsNone(result["published_at"])
            self.assertEqual(result["workflow"], before)
            self.assertEqual(load_publish_state(folder)["workflow"], before)

    def test_save_and_reload_is_complete_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            state = default_publish_state()
            state["published"] = True
            save_publish_state(folder, state)

            raw = (folder / "publish-state.json").read_text(encoding="utf-8")
            parsed = json.loads(raw)

            self.assertEqual(parsed, load_publish_state(folder))
            self.assertEqual(parsed["schema_version"], "0.1")
