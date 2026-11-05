# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo.tests.common import TransactionCase


class TestEngineFactory(TransactionCase):

    def setUp(self):
        super().setUp()
        self.company_a = self.env['res.company'].create({'name': 'Consenting Co'})
        self.company_b = self.env['res.company'].create({'name': 'Refusing Co'})
        self.company_a.acknowledge_cloud_ai_processing()

    def test_mock_engine_needs_no_consent(self):
        """Local engines never transmit anything, so they are never gated."""
        from odoo.addons.clm_ai.services.engine_factory import build_engine
        engine = build_engine(self.env, 'mock', self.company_b)
        self.assertFalse(engine.accepts_documents)
        self.assertFalse(engine.is_cloud)

    def test_cloud_engine_refused_without_consent(self):
        from odoo.addons.clm_ai.services.engine_factory import (
            build_engine, CloudConsentError,
        )
        with self.assertRaises(CloudConsentError):
            build_engine(self.env, 'gemini', self.company_b)

    def test_cloud_engine_consent_checked_against_given_company(self):
        """Company A's approval must not authorise Company B's contracts."""
        from odoo.addons.clm_ai.services.engine_factory import (
            build_engine, CloudConsentError,
        )
        with self.assertRaises(CloudConsentError):
            build_engine(self.env, 'gemini', self.company_b)

    def test_consent_check_precedes_api_key_check(self):
        """Refusal must not depend on how the provider is configured."""
        from odoo.addons.clm_ai.services.engine_factory import (
            build_engine, CloudConsentError,
        )
        self.env['ir.config_parameter'].sudo().set_param(
            'clm_ai.gemini_api_key', '')
        with self.assertRaises(CloudConsentError):
            build_engine(self.env, 'gemini', self.company_b)

    def test_revoked_consent_blocks_new_engines(self):
        from odoo.addons.clm_ai.services.engine_factory import (
            build_engine, CloudConsentError,
        )
        self.company_a.revoke_cloud_ai_processing()
        with self.assertRaises(CloudConsentError):
            build_engine(self.env, 'gemini', self.company_a)

    def test_unknown_provider_raises(self):
        from odoo.exceptions import UserError
        from odoo.addons.clm_ai.services.engine_factory import build_engine
        with self.assertRaises(UserError):
            build_engine(self.env, 'no_such_provider', self.company_a)

    def test_mock_engine_declares_text_capability(self):
        from odoo.addons.clm_ai.services.ai_engine import MockAIEngine
        self.assertFalse(MockAIEngine.accepts_documents)
