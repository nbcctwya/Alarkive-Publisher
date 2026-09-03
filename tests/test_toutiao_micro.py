from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from alarkive_publisher import toutiao_micro as micro
from alarkive_publisher.content import ContentVariant, PostContent
from alarkive_publisher.publisher_common import PublisherError
from alarkive_publisher.routing import AVAILABLE_PUBLISHERS, PUBLISHER_REGISTRY
from alarkive_publisher.workflow import run_publisher_workflow, run_single_platform_workflow
from alarkive_publisher.workflow_controller import WorkflowController


def make_post(content=None):
    return PostContent(
        Path('.'), '20260903-120000-a7c3', 'test', '2026-09-03T12:00:00+08:00',
        public_long=ContentVariant('文章标题', '文章正文', ()),
        toutiao_short=content,
    )


class ToutiaoMicroTests(unittest.TestCase):
    def test_micro_has_its_own_runner_and_variant(self):
        spec = PUBLISHER_REGISTRY['toutiao_micro']
        self.assertEqual(spec.variant, 'toutiao_short')
        self.assertEqual(spec.runner, 'toutiao_micro')
        self.assertIn('toutiao_micro', AVAILABLE_PUBLISHERS)

    def test_text_uses_micro_title_and_plain_body_with_paragraphs(self):
        content = ContentVariant('微头条标题', '第一段 **重点**\n\n第二段\n下一行', ())
        self.assertEqual(micro._micro_text(content), '微头条标题\n\n第一段 重点\n\n第二段\n下一行')

    def test_inline_marker_is_literal_text_and_never_an_image_instruction(self):
        content = ContentVariant('标题', '正文\n\n[[image:99]]', ())
        self.assertIn('[[image:99]]', micro._micro_text(content))

    def test_missing_micro_never_uses_article_content(self):
        page = Mock()
        with self.assertRaisesRegex(PublisherError, 'toutiao_short'):
            micro.run_toutiao_micro(page, make_post())
        self.assertEqual(page.mock_calls, [])

    def test_single_micro_routes_only_to_micro(self):
        post = make_post(ContentVariant('微头条', '正文', ()))
        controller = Mock(spec=WorkflowController)
        browser, context, page = Mock(), Mock(), Mock()
        with patch('alarkive_publisher.workflow.start_browser', return_value=(browser, context, page)), patch(
            'alarkive_publisher.workflow.run_toutiao_micro'
        ) as runner, patch('alarkive_publisher.workflow.run_toutiao_article') as article, patch(
            'alarkive_publisher.workflow.run_baijiahao'
        ) as baijiahao, patch('alarkive_publisher.workflow.run_wechat') as wechat:
            run_single_platform_workflow(post, Path('.'), 'toutiao_micro', controller)
        runner.assert_called_once_with(page, post, controller)
        article.assert_not_called()
        baijiahao.assert_not_called()
        wechat.assert_not_called()
        self.assertEqual(controller.ready.call_args.args[0], 'toutiao_micro')

    def test_missing_micro_rejected_before_browser(self):
        with patch('alarkive_publisher.workflow.start_browser') as start:
            with self.assertRaisesRegex(ValueError, '不包含微头条所需内容'):
                run_single_platform_workflow(make_post(), Path('.'), 'toutiao_micro', Mock())
        start.assert_not_called()

    def test_all_workflow_accepts_micro_only_package(self):
        post = PostContent(Path('.'), 'id', 'name', 'date', toutiao_short=ContentVariant('title', 'body', ()))
        with patch('alarkive_publisher.workflow.start_browser', return_value=(Mock(), Mock(), Mock())), patch(
            'alarkive_publisher.workflow.run_toutiao_micro'
        ) as runner:
            run_publisher_workflow(post, Path('.'), Mock(spec=WorkflowController))
        runner.assert_called_once()

    def test_final_publish_controls_and_disguised_submit_are_rejected(self):
        for text, attrs in (
            ('发布', {}), ('确认发布', {}), ('发表', {}), ('定时发布', {}),
            ('确定', {'type': 'submit'}), ('确定', {'class': 'publish-content'}),
            ('确定', {'aria-label': 'Publish'}), ('存草稿', {}),
        ):
            with self.subTest(text=text, attrs=attrs):
                control = Mock()
                control.inner_text.return_value = text
                control.get_attribute.side_effect = attrs.get
                with self.assertRaises(PublisherError):
                    micro._safe_click(control, {'图片', '确定'})
                control.click.assert_not_called()

    def test_scoped_image_confirmation_is_allowed(self):
        control = Mock()
        control.inner_text.return_value = '确定'
        control.get_attribute.side_effect = {'type': 'button', 'data-e2e': 'imageUploadConfirm-btn'}.get
        micro._safe_click(control, {'确定'})
        control.click.assert_called_once_with()

    def test_html_paste_preserves_empty_lines_and_escapes_text(self):
        page, editor = Mock(), Mock()
        with patch.object(micro, '_editor', return_value=editor), patch.object(micro, '_assert_text'):
            micro._fill_text(page, '标题\n\n正文 <img src=x> &\n下一行')
        payload = editor.evaluate.call_args.args[1]
        self.assertEqual(payload['html'], '<p>标题</p><p><br></p><p>正文 &lt;img src=x&gt; &amp;</p><p>下一行</p>')
        self.assertNotIn('<img ', payload['html'])

    def test_text_check_rejects_lost_empty_lines_or_extra_inline_images(self):
        page, editor = Mock(), Mock()
        editor.locator.return_value.count.return_value = 0
        with patch.object(micro, '_editor', return_value=editor), patch.object(micro, '_read_text', return_value='标题\n正文'):
            with self.assertRaises(PublisherError):
                micro._assert_text(page, '标题\n\n正文')
        editor.locator.return_value.count.return_value = 1
        with patch.object(micro, '_editor', return_value=editor), patch.object(micro, '_read_text', return_value='标题\n\n正文'):
            with self.assertRaisesRegex(PublisherError, '独立图片区'):
                micro._assert_text(page, '标题\n\n正文')

    def test_upload_wait_requires_success_loaded_preview_and_append_order(self):
        page, drawer = Mock(), Mock()
        drawer.inner_text.return_value = '已上传 2 张图片'
        drawer.locator.return_value.count.return_value = 2
        drawer.locator.return_value.evaluate_all.return_value = True
        with patch.object(micro, '_uploaded_signatures', return_value=('/first', '/second')):
            self.assertEqual(micro._wait_for_upload(page, drawer, ('/first',)), ('/first', '/second'))
        with patch.object(micro, '_uploaded_signatures', return_value=('/second', '/first')):
            with self.assertRaisesRegex(PublisherError, '顺序'):
                micro._wait_for_upload(page, drawer, ('/first',))
        drawer.locator.return_value.evaluate_all.return_value = False
        with patch.object(micro, '_uploaded_signatures', return_value=('/first', '/second')), patch.object(
            micro.time, 'monotonic', side_effect=[0, 1, 121]
        ):
            with self.assertRaisesRegex(PublisherError, '超时'):
                micro._wait_for_upload(page, drawer, ('/first',))

    def test_changed_attachment_order_fails_final_check(self):
        with patch.object(micro, '_assert_text'), patch.object(micro, '_attached_signatures', return_value=('b', 'a')):
            with self.assertRaisesRegex(PublisherError, '重绘后发生变化'):
                micro._verify_ready(Mock(), '正文', ('a', 'b'))

    def test_upload_errors_are_not_reported_as_ready(self):
        drawer = Mock()
        drawer.inner_text.return_value = '上传失败，请重试'
        with self.assertRaisesRegex(PublisherError, '上传失败'):
            micro._wait_for_upload(Mock(), drawer, ())


if __name__ == '__main__':
    unittest.main()
