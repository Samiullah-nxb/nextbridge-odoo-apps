# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo.tests.common import TransactionCase


class TestReviewViews(TransactionCase):

    def test_review_form_loads_and_is_odoo19_syntax(self):
        view = self.env.ref('clm_ai.view_clm_ai_job_review_form')
        arch = view.arch_db
        self.assertNotIn('attrs=', arch,
                         "Odoo 16 attrs syntax is not valid in Odoo 19")
        self.assertNotIn('states=', arch)

    def test_review_form_renders_for_a_job(self):
        """get_view raises on any malformed field reference."""
        contract = self.env['clm.contract'].create({
            'name': 'View Contract',
            'contract_type_id': self.env.ref('clm.contract_type_nda').id,
        })
        version = self.env['clm.contract.version'].create({
            'contract_id': contract.id,
            'version_type': 'internal_draft', 'version_number': 1,
        })
        self.env['clm.ai.job'].create({
            'contract_version_id': version.id,
            'analysis_type': 'field_extraction', 'provider': 'mock',
        })
        rendered = self.env['clm.ai.job'].get_view(
            view_id=self.env.ref('clm_ai.view_clm_ai_job_review_form').id,
            view_type='form')
        self.assertIn('arch', rendered)

    def test_review_queue_action_excludes_superseded_runs(self):
        action = self.env.ref('clm_ai.action_clm_ai_review_queue')
        self.assertIn('is_reviewable', str(action.domain))

    def test_review_tabs_partition_suggestions_without_overlap(self):
        """The three review-screen tabs must never show the same suggestion
        twice, and together must cover every pending/needs_mapping one.

        This exercises _compute_review_tabs directly -- the actual
        mechanism the review screen's three tab fields use -- rather than
        a view-level domain, which does not filter rendered one2many rows
        in Odoo's web client.
        """
        contract = self.env['clm.contract'].create({
            'name': 'Band Partition Contract',
            'contract_type_id': self.env.ref('clm.contract_type_nda').id,
        })
        version = self.env['clm.contract.version'].create({
            'contract_id': contract.id,
            'version_type': 'internal_draft', 'version_number': 1,
        })
        job = self.env['clm.ai.job'].create({
            'contract_version_id': version.id,
            'analysis_type': 'field_extraction', 'provider': 'mock',
        })
        config = self.env['ir.config_parameter'].sudo()
        high = float(config.get_param('clm_ai.confidence_high', 95.0))
        low = float(config.get_param('clm_ai.confidence_low', 80.0))
        Suggestion = self.env['clm.ai.field.suggestion']

        blocker = Suggestion.create({
            'job_id': job.id, 'field_name': 'name', 'suggested_value': 'X',
            'confidence': low - 1, 'status': 'pending',
        })
        review = Suggestion.create({
            'job_id': job.id, 'field_name': 'effective_date',
            'suggested_value': 'Y', 'confidence': (low + high) / 2,
            'status': 'pending',
        })
        verified = Suggestion.create({
            'job_id': job.id, 'field_name': 'expiry_date',
            'suggested_value': 'Z', 'confidence': high, 'status': 'pending',
        })
        needs_mapping = Suggestion.create({
            'job_id': job.id, 'field_name': 'partner_id',
            'suggested_value': 'W', 'confidence': high,
            'status': 'needs_mapping',
        })

        self.assertEqual(job.blocker_suggestion_ids, blocker | needs_mapping)
        self.assertEqual(job.review_suggestion_ids, review)
        self.assertEqual(job.verified_suggestion_ids, verified)

        tabs = [job.blocker_suggestion_ids, job.review_suggestion_ids,
                job.verified_suggestion_ids]
        seen = self.env['clm.ai.field.suggestion']
        for tab in tabs:
            self.assertFalse(seen & tab,
                             "A suggestion appeared on more than one review tab")
            seen |= tab
        self.assertEqual(seen, job.suggestion_ids)

    def test_review_suggestion_ids_is_writable(self):
        """The Review tab must stay inline-editable.

        A computed one2many with no inverse is forced readonly=True by
        Odoo's ORM, which would silently disable the Review page's
        editable="bottom" suggested_value column. review_suggestion_ids
        carries a no-op inverse specifically to prevent that -- assert
        both the field metadata and that a write through it persists.
        """
        field = self.env['clm.ai.job']._fields['review_suggestion_ids']
        self.assertFalse(field.readonly,
                         "review_suggestion_ids must stay writable for the "
                         "Review tab's inline editing to work")

        contract = self.env['clm.contract'].create({
            'name': 'Writable Review Tab Contract',
            'contract_type_id': self.env.ref('clm.contract_type_nda').id,
        })
        version = self.env['clm.contract.version'].create({
            'contract_id': contract.id,
            'version_type': 'internal_draft', 'version_number': 1,
        })
        job = self.env['clm.ai.job'].create({
            'contract_version_id': version.id,
            'analysis_type': 'field_extraction', 'provider': 'mock',
        })
        config = self.env['ir.config_parameter'].sudo()
        high = float(config.get_param('clm_ai.confidence_high', 95.0))
        low = float(config.get_param('clm_ai.confidence_low', 80.0))
        suggestion = self.env['clm.ai.field.suggestion'].create({
            'job_id': job.id, 'field_name': 'effective_date',
            'suggested_value': 'Y', 'confidence': (low + high) / 2,
            'status': 'pending',
        })

        job.review_suggestion_ids.write({'suggested_value': 'corrected'})
        self.assertEqual(suggestion.suggested_value, 'corrected')
