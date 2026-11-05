# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError
from unittest.mock import patch


class TestAiJobTransaction(TransactionCase):

    def setUp(self):
        super().setUp()
        self.contract_type_nda = self.env.ref('clm.contract_type_nda')
        self.contract = self.env['clm.contract'].create({
            'name': 'Transaction Test Contract',
            'contract_type_id': self.contract_type_nda.id,
        })
        self.version = self.env['clm.contract.version'].create({
            'contract_id': self.contract.id,
            'version_type': 'internal_draft',
        })

    def test_ai_job_failure_rolls_back_cleanly(self):
        """Failed AI job must not leave 'processing' state committed (C-03 fix)."""
        job = self.env['clm.ai.job'].create({
            'contract_version_id': self.version.id,
            'provider': 'mock',
        })
        
        # Patch the MockAIEngine to raise an exception
        with patch('odoo.addons.clm_ai.services.ai_engine.MockAIEngine.extract_fields', side_effect=Exception('Test Error')):
            job.action_run_sync()
            
        self.assertIn(job.status, ('completed', 'failed'))
        self.assertEqual(job.status, 'failed')
        self.assertEqual(job.error_message, 'Test Error')
