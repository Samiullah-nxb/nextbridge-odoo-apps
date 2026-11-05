# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError


class TestContractReview(TransactionCase):

    def setUp(self):
        super().setUp()
        self.config = self.env['ir.config_parameter'].sudo()
        self.config.set_param('clm_ai.confidence_high', '95')
        self.config.set_param('clm_ai.confidence_low', '80')

        self.contract = self.env['clm.contract'].create({
            'name': 'Review Contract',
            'contract_type_id': self.env.ref('clm.contract_type_nda').id,
        })
        self.version = self.env['clm.contract.version'].create({
            'contract_id': self.contract.id,
            'version_type': 'internal_draft', 'version_number': 1,
        })
        self.job = self.env['clm.ai.job'].create({
            'contract_version_id': self.version.id,
            'analysis_type': 'field_extraction', 'provider': 'mock',
        })
        self.definition = self.env['clm.contract.field.definition'].create({
            'name': 'Governing Law', 'code': 'gov_law_rev', 'value_type': 'char',
        })

    def _suggestion(self, confidence=98.0, status='pending'):
        return self.env['clm.ai.field.suggestion'].create({
            'job_id': self.job.id,
            'field_name': 'gov_law_rev',
            'suggested_value': 'England and Wales',
            'target_type': 'definition',
            'definition_id': self.definition.id,
            'confidence': confidence,
            'status': status,
        })

    def test_high_confidence_bands_as_verified(self):
        self.assertEqual(self._suggestion(confidence=98.0).review_band, 'verified')

    def test_mid_confidence_bands_as_review(self):
        self.assertEqual(self._suggestion(confidence=91.0).review_band, 'review')

    def test_low_confidence_bands_as_blocker(self):
        self.assertEqual(self._suggestion(confidence=42.0).review_band, 'blocker')

    def test_needs_mapping_bands_as_blocker_regardless_of_confidence(self):
        suggestion = self._suggestion(confidence=99.0, status='needs_mapping')
        self.assertEqual(suggestion.review_band, 'blocker')

    def test_band_follows_threshold_changes(self):
        """Bands are derived, so re-tuning re-bands without a migration."""
        suggestion = self._suggestion(confidence=94.0)
        self.assertEqual(suggestion.review_band, 'review')
        self.config.set_param('clm_ai.confidence_high', '90')
        suggestion.invalidate_recordset(['review_band'])
        self.assertEqual(suggestion.review_band, 'verified')

    def test_confirm_blocked_while_blockers_exist(self):
        self._suggestion(confidence=42.0)
        self.assertFalse(self.job.can_confirm)
        with self.assertRaises(UserError):
            self.job.action_confirm_review()

    def test_confirm_blocked_by_missing_required_field(self):
        self.definition.required = True
        self.assertFalse(self.job.can_confirm)

    def test_confirm_applies_values_and_marks_reviewed(self):
        suggestion = self._suggestion(confidence=98.0)
        self.job.action_confirm_review()
        self.assertEqual(self.job.review_state, 'reviewed')
        self.assertEqual(suggestion.status, 'approved')
        value = self.env['clm.contract.field.value'].search([
            ('contract_id', '=', self.contract.id),
            ('definition_id', '=', self.definition.id),
        ])
        self.assertEqual(value.display_value, 'England and Wales')

    def test_confirm_records_reviewer(self):
        self._suggestion()
        self.job.action_confirm_review()
        self.assertEqual(self.job.reviewed_by_id, self.env.user)
        self.assertTrue(self.job.reviewed_at)

    def test_reviewed_is_terminal(self):
        self._suggestion()
        self.job.action_confirm_review()
        with self.assertRaises(UserError):
            self.job.action_confirm_review()

    def test_reviewed_outcome_survives_threshold_changes(self):
        """Re-tuning thresholds must never rewrite a recorded decision."""
        self._suggestion(confidence=96.0)
        self.job.action_confirm_review()
        self.config.set_param('clm_ai.confidence_high', '99')
        self.assertEqual(self.job.review_state, 'reviewed')
        self.assertEqual(self.job.suggestion_ids.status, 'approved')

    def test_reject_applies_nothing(self):
        # Every contract carries an empty row per seeded field definition, so
        # "nothing applied" means the rows are exactly as they were.
        values = self.env['clm.contract.field.value'].search([('contract_id', '=', self.contract.id)])
        before = values.read()
        self._suggestion()
        self.job.action_reject_review()
        self.assertEqual(self.job.review_state, 'rejected')
        self.assertEqual(
            self.env['clm.contract.field.value'].search([('contract_id', '=', self.contract.id)]).read(),
            before,
        )

    def test_confirm_review_wraps_apply_errors(self):
        """A bad value on confirm must raise UserError, not a raw
        exception naming no field."""
        float_definition = self.env['clm.contract.field.definition'].create({
            'name': 'Contract Score', 'code': 'score_rev', 'value_type': 'float',
        })
        self.env['clm.ai.field.suggestion'].create({
            'job_id': self.job.id,
            'field_name': 'score_rev',
            'suggested_value': 'not-a-number',
            'target_type': 'definition',
            'definition_id': float_definition.id,
            'confidence': 98.0,
            'status': 'pending',
        })
        with self.assertRaises(UserError) as cm:
            self.job.action_confirm_review()
        self.assertIn('Could not apply suggestion', str(cm.exception))
