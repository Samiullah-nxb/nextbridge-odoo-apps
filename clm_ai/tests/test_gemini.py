# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError

class TestGeminiIntegration(TransactionCase):

    def setUp(self):
        super().setUp()
        self.clause = self.env['clm.clause'].create({
            'name': 'Test Limitation of Liability Clause',
            'standard_wording': 'Liability is capped at 1M.',
            'category': 'limitation_of_liability',
            'state': 'published'
        })
        self.contract = self.env['clm.contract'].create({
            'name': 'Test Gemini Contract',
            'contract_type_id': self.env.ref('clm.contract_type_nda').id,
        })
        self.version = self.env['clm.contract.version'].create({
            'contract_id': self.contract.id,
            'version_type': 'internal_draft',
            'version_number': 1
        })

    def test_gemini_requires_api_key(self):
        # By default in test environment, api key is not set
        self.env['ir.config_parameter'].sudo().set_param('clm_ai.gemini_api_key', False)
        self.env.company.acknowledge_cloud_ai_processing()

        job = self.env['clm.ai.job'].create({
            'contract_version_id': self.version.id,
            'analysis_type': 'field_extraction',
            'provider': 'gemini'
        })
        
        job.action_run_sync()
        
        self.assertEqual(job.status, 'failed')
        self.assertIn('API Key is not configured', job.error_message)

    def test_relational_fallback(self):
        """A core field the model reports but that resolves to no matching
        record must be routed to manual mapping, not silently dropped."""
        self.env['ir.config_parameter'].sudo().set_param('clm_ai.gemini_api_key', 'mock_key')

        job = self.env['clm.ai.job'].create({
            'contract_version_id': self.version.id,
            'analysis_type': 'field_extraction',
            'provider': 'gemini',
        })

        from odoo.addons.clm.services.document_reader import DocumentPayload
        from odoo.addons.clm_ai.services.gemini_engine import GeminiAIEngine
        import unittest.mock as mock

        payload = DocumentPayload(
            mimetype='text/plain', page_count=1,
            text='Contract with Nonexistent Partner LLC.',
            text_source='native',
        )

        class MockExtracted:
            def __init__(self, value):
                self.value = value
                self.confidence = 99.0
                self.source_text = 'Nonexistent Partner LLC'
                self.source_page = 1

        class MockParsed:
            partner_id = MockExtracted('Nonexistent Partner LLC')

        class MockResponse:
            parsed = MockParsed()

        with mock.patch('odoo.addons.clm_ai.services.gemini_engine.genai.Client'), \
             mock.patch.object(GeminiAIEngine, '_generate', return_value=MockResponse()):
            engine = GeminiAIEngine(self.env)
            engine.extract_fields(job, payload)

        self.assertEqual(len(job.suggestion_ids), 1)
        self.assertEqual(job.suggestion_ids[0].field_name, 'partner_id')
        self.assertEqual(job.suggestion_ids[0].status, 'needs_mapping')
