from __future__ import annotations

import ast
import inspect
import traceback
import unittest

from alarkive_publisher import baijiahao, browser, toutiao_article, wechat, workflow, xiaohongshu
from alarkive_publisher.publisher_common import PublisherError, run_step


class PublisherCommonTests(unittest.TestCase):
    def test_error_retains_runtime_error_contract(self) -> None:
        error = PublisherError("upload", "Upload failed")
        self.assertIsInstance(error, RuntimeError)
        self.assertEqual(error.step, "upload")
        self.assertEqual(error.message, "Upload failed")
        self.assertEqual(str(error), "Upload failed")
        self.assertEqual(error.__class__.__module__, "alarkive_publisher.publisher_common")

    def test_step_returns_original_value(self) -> None:
        value = object()
        self.assertIs(run_step("prepare", lambda: value), value)

    def test_step_wraps_unexpected_error_and_preserves_cause(self) -> None:
        cause = ValueError("bad input")

        def fail() -> None:
            raise cause

        with self.assertRaises(PublisherError) as caught:
            run_step("fill", fail)
        self.assertEqual(caught.exception.step, "fill")
        self.assertEqual(caught.exception.message, "ValueError: bad input")
        self.assertIs(caught.exception.__cause__, cause)

    def test_step_preserves_existing_publisher_error(self) -> None:
        error = PublisherError("specific step", "specific failure")

        def fail() -> None:
            raise error

        with self.assertRaises(PublisherError) as caught:
            run_step("outer step", fail)
        self.assertIs(caught.exception, error)
        self.assertEqual(caught.exception.step, "specific step")

    def test_step_does_not_wrap_interrupts(self) -> None:
        error = KeyboardInterrupt()

        def interrupt() -> None:
            raise error

        with self.assertRaises(KeyboardInterrupt) as caught:
            run_step("prepare", interrupt)
        self.assertIs(caught.exception, error)

    def test_shared_error_traceback_does_not_point_to_xiaohongshu(self) -> None:
        def fail() -> None:
            raise ValueError("failed")

        try:
            toutiao_article._run_step("check draft", fail)
        except PublisherError as error:
            frames = traceback.extract_tb(error.__traceback__)
            self.assertTrue(frames[-1].filename.endswith("publisher_common.py"))
            self.assertFalse(any(frame.filename.endswith("xiaohongshu.py") for frame in frames))
        else:
            self.fail("Expected PublisherError")

    def test_publishers_import_common_tools_without_xiaohongshu_dependency(self) -> None:
        for publisher in (baijiahao, toutiao_article, wechat):
            with self.subTest(publisher=publisher.__name__):
                self.assertIs(publisher.PublisherError, PublisherError)
                self.assertIs(publisher._run_step, run_step)
                imports = [node.module for node in ast.walk(ast.parse(inspect.getsource(publisher)))
                           if isinstance(node, ast.ImportFrom)]
                self.assertNotIn("xiaohongshu", imports)

    def test_legacy_exports_remain_identical_to_shared_implementations(self) -> None:
        self.assertIs(xiaohongshu.PublisherError, PublisherError)
        self.assertIs(xiaohongshu._run_step, run_step)
        self.assertIs(xiaohongshu.start_browser, browser.start_browser)
        self.assertIs(workflow.PublisherError, PublisherError)
        self.assertIs(workflow.start_browser, browser.start_browser)


if __name__ == "__main__":
    unittest.main()
