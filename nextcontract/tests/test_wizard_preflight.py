# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo.tests.common import TransactionCase


class TestWizardPreflight(TransactionCase):
    """A run that cannot succeed is refused up front, with advice."""

    def setUp(self):
        super().setUp()
        self.company = self.env.company
        self.company.acknowledge_cloud_ai_processing()
        contract = self.env['nextcontract.contract'].create({
            'name': 'No File Contract',
            'contract_type_id': self.env.ref('nextcontract.contract_type_nda').id,
            'company_id': self.company.id})
        self.version = self.env['nextcontract.contract.version'].create({
            'contract_id': contract.id,
            'version_type': 'internal_draft', 'version_number': 1})
        self.provider = self.env['nextcontract.ai.provider'].create({
            'name': 'Preflight', 'provider_type': 'openai', 'api_key': 'k',
            'model_ids': [(0, 0, {'name': 'm'})]})

    def _wizard(self, **values):
        vals = {'contract_version_id': self.version.id,
                'provider_id': self.provider.id}
        vals.update(values)
        return self.env['nextcontract.ai.job.wizard'].create(vals)

    def test_version_without_a_document_shows_a_toast_and_creates_no_job(self):
        """A toast, not a blocking error dialog, and the wizard closes."""
        jobs_before = self.env['nextcontract.ai.job'].search_count([])
        action = self._wizard().action_create_job()
        self.assertEqual(action['type'], 'ir.actions.client')
        self.assertEqual(action['tag'], 'display_notification')
        params = action['params']
        self.assertEqual(params['type'], 'danger')
        self.assertTrue(params['sticky'])
        self.assertIn('no document attached', params['message'])
        self.assertIn('No File Contract', params['message'])
        self.assertEqual(params['next'], {'type': 'ir.actions.act_window_close'})
        self.assertEqual(self.env['nextcontract.ai.job'].search_count([]), jobs_before)

    def test_mock_provider_needs_no_document(self):
        action = self._wizard(provider_id=False, provider='mock').action_create_job()
        self.assertTrue(action['res_id'])

    def test_clause_analysis_may_use_already_extracted_text(self):
        self.version.extracted_text = 'Already extracted contract text.'
        action = self._wizard(analysis_type='clause_analysis').action_create_job()
        self.assertTrue(action['res_id'])

    def test_missing_document_on_a_queued_job_is_an_unreadable_document(self):
        """If a job gets past the wizard, the recorded reason is accurate."""
        job = self.env['nextcontract.ai.job'].create({
            'contract_version_id': self.version.id,
            'analysis_type': 'field_extraction',
            'provider_id': self.provider.id})
        job.action_run_sync()
        self.assertEqual(job.status, 'failed')
        self.assertEqual(job.failure_reason, 'unreadable_document')
        self.assertIn('no document attached', job.error_message)
