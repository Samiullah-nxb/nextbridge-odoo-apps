# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo.tests.common import TransactionCase


def _payload(page_count=1, page_bytes=1024, text=None):
    from odoo.addons.clm.services.document_reader import (
        DocumentPayload, DocumentPage,
    )
    return DocumentPayload(
        mimetype='application/pdf',
        page_count=page_count,
        text=text,
        text_source='none' if text is None else 'native',
        pages=[
            DocumentPage(page_number=index + 1,
                         image=b'x' * page_bytes,
                         mimetype='image/png')
            for index in range(page_count)
        ],
    )


class TestGeminiTransport(TransactionCase):

    def setUp(self):
        super().setUp()
        self.config = self.env['ir.config_parameter'].sudo()

    def test_small_payload_goes_inline(self):
        from odoo.addons.clm_ai.services.transport import choose_transport
        self.config.set_param('clm_ai.inline_payload_limit_mb', '15')
        self.assertEqual(
            choose_transport(self.env, _payload(page_count=1, page_bytes=1024)),
            'inline')

    def test_large_payload_goes_to_files_api(self):
        from odoo.addons.clm_ai.services.transport import choose_transport
        self.config.set_param('clm_ai.inline_payload_limit_mb', '1')
        self.assertEqual(
            choose_transport(self.env, _payload(page_count=4, page_bytes=400_000)),
            'files_api')

    def test_size_is_measured_after_base64_encoding(self):
        """Encoding inflates by ~4/3; routing must account for it."""
        from odoo.addons.clm_ai.services.transport import serialised_payload_size
        raw_bytes = 3_000_000
        size = serialised_payload_size(_payload(page_count=1, page_bytes=raw_bytes))
        self.assertGreater(size, raw_bytes)

    def test_exceeding_max_pages_raises(self):
        from odoo.addons.clm_ai.services.transport import (
            assert_within_page_limit, MaxPagesExceeded,
        )
        self.config.set_param('clm_ai.max_pages', '40')
        with self.assertRaises(MaxPagesExceeded):
            assert_within_page_limit(self.env, _payload(page_count=200))

    def test_page_limit_error_names_both_numbers(self):
        """The failure has to be auditable, not just loud."""
        from odoo.addons.clm_ai.services.transport import (
            assert_within_page_limit, MaxPagesExceeded,
        )
        self.config.set_param('clm_ai.max_pages', '40')
        with self.assertRaises(MaxPagesExceeded) as caught:
            assert_within_page_limit(self.env, _payload(page_count=200))
        message = str(caught.exception)
        self.assertIn('200', message)
        self.assertIn('40', message)

    def test_within_page_limit_passes(self):
        from odoo.addons.clm_ai.services.transport import assert_within_page_limit
        self.config.set_param('clm_ai.max_pages', '40')
        self.assertTrue(assert_within_page_limit(self.env, _payload(page_count=12)))

    def test_gemini_declares_document_and_cloud_capabilities(self):
        from odoo.addons.clm_ai.services.gemini_engine import GeminiAIEngine
        self.assertTrue(GeminiAIEngine.accepts_documents)
        self.assertTrue(GeminiAIEngine.is_cloud)

    def test_multi_page_payload_uploads_and_returns_every_page(self):
        """A files_api transport must upload every page, not just the first.

        Dropping later pages here means the model silently extracts from a
        partial document while still returning confident, schema-conforming
        values — the exact failure mode assert_within_page_limit exists to
        prevent, except triggered by payload size instead of page count.
        """
        from odoo.addons.clm_ai.services.gemini_engine import GeminiAIEngine
        import unittest.mock as mock

        self.config.set_param('clm_ai.gemini_api_key', 'mock_key')
        payload = _payload(page_count=3, page_bytes=400_000)

        with mock.patch('odoo.addons.clm_ai.services.gemini_engine.genai.Client'):
            engine = GeminiAIEngine(self.env)

        uploaded_mocks = [mock.MagicMock(name='uploaded_page_%d' % i) for i in range(3)]
        engine.client.files.upload = mock.MagicMock(side_effect=uploaded_mocks)

        contents, uploaded_files = engine._build_contents(payload, 'files_api')

        self.assertEqual(engine.client.files.upload.call_count, 3)
        self.assertEqual(uploaded_files, uploaded_mocks)
        # instruction plus one content entry per uploaded page.
        self.assertEqual(len(contents), 4)
        for uploaded in uploaded_mocks:
            self.assertIn(uploaded, contents)
