# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo.tests.common import TransactionCase


class TestCloudConsent(TransactionCase):

    def setUp(self):
        super().setUp()
        self.company_a = self.env['res.company'].create({'name': 'Company A'})
        self.company_b = self.env['res.company'].create({'name': 'Company B'})

    def test_consent_defaults_to_off(self):
        """Egress is never permitted by default."""
        self.assertFalse(self.company_a.clm_ai_cloud_processing_acknowledged)

    def test_acknowledging_records_user_and_timestamp(self):
        self.company_a.acknowledge_cloud_ai_processing()
        self.assertTrue(self.company_a.clm_ai_cloud_processing_acknowledged)
        self.assertEqual(self.company_a.clm_ai_cloud_consent_user_id, self.env.user)
        self.assertTrue(self.company_a.clm_ai_cloud_consent_date)

    def test_consent_is_per_company(self):
        self.company_a.acknowledge_cloud_ai_processing()
        self.assertTrue(self.company_a.clm_ai_cloud_processing_acknowledged)
        self.assertFalse(self.company_b.clm_ai_cloud_processing_acknowledged)

    def test_revoking_clears_flag_but_keeps_history(self):
        """Withdrawal is forward-only; who consented and when stays readable."""
        self.company_a.acknowledge_cloud_ai_processing()
        consenting_user = self.company_a.clm_ai_cloud_consent_user_id
        consent_date = self.company_a.clm_ai_cloud_consent_date

        self.company_a.revoke_cloud_ai_processing()

        self.assertFalse(self.company_a.clm_ai_cloud_processing_acknowledged)
        self.assertEqual(self.company_a.clm_ai_cloud_consent_user_id, consenting_user)
        self.assertEqual(self.company_a.clm_ai_cloud_consent_date, consent_date)
