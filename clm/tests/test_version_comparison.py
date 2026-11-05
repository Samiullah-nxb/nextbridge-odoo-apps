# Part of NextContract. See LICENSE file for full copyright and licensing details.
"""
Comparing contract versions edited in Odoo.

Version 1 says "The Client will pay within 20 days." with a 20-day notice
period; version 2 changes both to 30. The existing comparison model and redline
engine must report exactly that: 20 removed, 30 added.
"""

from odoo.tests.common import TransactionCase

from ..services.redline_engine import HtmlRedlineEngine


class TestVersionComparison(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.contract = cls.env['clm.contract'].create({
            'name': 'IT Services Agreement - Acme Corp',
            'contract_type_id': cls.env.ref('clm.contract_type_services').id,
            'notice_period_days': 20,
        })
        cls.payment = cls.env['clm.contract.clause'].create({
            'contract_id': cls.contract.id,
            'section': 'Payment',
            'title': 'Payment',
            'content': '<p>The Client will pay within 20 days.</p>',
        })
        cls.v1 = cls.contract._clm_create_content_version(change_summary='First draft')
        cls.payment.content = '<p>The Client will pay within 30 days.</p>'
        cls.contract.notice_period_days = 30
        cls.v2 = cls.contract._clm_create_content_version(change_summary='Payment and notice extended')

    def test_comparison_detects_the_changed_values(self):
        comparison = self.env['clm.document.comparison'].create({
            'base_version_id': self.v1.id,
            'target_version_id': self.v2.id,
        })
        comparison.action_generate_redline()

        self.assertEqual(comparison.status, 'done', comparison.error_message)
        html = comparison.html_redline
        self.assertIn('Summary of Changes', html)
        self.assertIn('<del>20</del>', html)
        self.assertIn('<ins>30</ins>', html)
        self.assertIn('o_clm_redline_unified', html)
        self.assertIn('o_clm_redline_split', html)
        self.assertTrue(comparison.has_differences)
        self.assertIn('o_clm_doc_compare', comparison.html_changes)
        self.assertTrue(comparison.base_pdf_attachment_id, 'Original PDF should be resolved')
        self.assertTrue(comparison.target_pdf_attachment_id, 'Revised PDF should be resolved')
        self.assertIn('o_clm_pdf_iframe', comparison.base_pdf_preview_html)
        self.assertIn('o_clm_pdf_iframe', comparison.target_pdf_preview_html)
        # Highlighted comparison PDFs are owned by the comparison record.
        self.assertEqual(comparison.base_pdf_attachment_id.res_model, 'clm.document.comparison')
        self.assertTrue(
            comparison.base_pdf_attachment_id.name.startswith('CLM_Compare_Original_'),
        )
        self.assertTrue(
            comparison.target_pdf_attachment_id.name.startswith('CLM_Compare_Revised_'),
        )
        self.assertGreater(comparison.addition_count, 0)
        self.assertGreater(comparison.deletion_count, 0)
        self.assertEqual(comparison.title, 'Compare v%s → v%s' % (
            self.v1.version_number, self.v2.version_number,
        ))

        changes = HtmlRedlineEngine.summarize_changes(
            self.v1._clm_content_lines(), self.v2._clm_content_lines(),
        )
        by_line = {change['after']: change for change in changes}
        payment = by_line['The Client will pay within 30 days.']
        self.assertEqual(payment['before'], 'The Client will pay within 20 days.')
        self.assertEqual((payment['removed'], payment['added']), (['20'], ['30']))
        notice = by_line['Notice Period: 30 days']
        self.assertEqual((notice['removed'], notice['added']), (['20'], ['30']))

    def test_side_by_side_view_toggle(self):
        comparison = self.env['clm.document.comparison']._clm_compare(self.v1, self.v2)
        self.assertEqual(comparison.diff_view, 'unified')
        self.assertIn('data-view="unified"', comparison.html_redline)

        comparison.action_set_diff_view_side_by_side()
        self.assertEqual(comparison.diff_view, 'side_by_side')
        self.assertIn('data-view="side_by_side"', comparison.html_redline)
        self.assertIn('<del>20</del>', comparison.html_redline)

        comparison.action_set_diff_view_unified()
        self.assertEqual(comparison.diff_view, 'unified')
        self.assertIn('data-view="unified"', comparison.html_redline)

    def test_compare_versions_uses_the_parent_version(self):
        action = self.contract.action_compare_versions()
        comparison = self.env['clm.document.comparison'].browse(action['res_id'])
        self.assertEqual(comparison.base_version_id, self.v1)
        self.assertEqual(comparison.target_version_id, self.v2)
        self.assertEqual(comparison.status, 'done')

        again = self.contract.action_compare_versions()
        self.assertEqual(again['res_id'], comparison.id, 'A pair keeps a single comparison')

    def test_compare_with_parent_from_the_version(self):
        action = self.v2.action_compare_with_parent()
        comparison = self.env['clm.document.comparison'].browse(action['res_id'])
        self.assertEqual((comparison.base_version_id, comparison.target_version_id), (self.v1, self.v2))

    def test_pointing_a_comparison_at_other_versions_resets_it(self):
        comparison = self.env['clm.document.comparison']._clm_compare(self.v1, self.v2)
        self.assertEqual(comparison.status, 'done')
        v3 = self.env['clm.contract.version'].create({'contract_id': self.contract.id})
        comparison.target_version_id = v3
        self.assertEqual(comparison.status, 'pending')
        self.assertFalse(comparison.html_redline)
        self.assertFalse(comparison.has_differences)

    def test_identical_versions_have_no_differences(self):
        comparison = self.env['clm.document.comparison']._clm_compare(self.v2, self.v2)
        self.assertEqual(comparison.status, 'done', comparison.error_message)
        self.assertIn('No differences found', comparison.html_redline)
        self.assertFalse(comparison.has_differences)
        self.assertEqual(comparison.change_count, 0)
        self.assertEqual(comparison.stats_label, 'No wording changes')

    def test_falls_back_to_document_when_snapshots_match(self):
        """DOCX edits must still redline when content_snapshot was copied unchanged."""
        from odoo.addons.clm.services.redline_engine import HtmlRedlineEngine

        engine = HtmlRedlineEngine()
        # Same frozen wording on both sides…
        self.assertEqual(self.v1._clm_content_lines(), self.v1._clm_content_lines())
        # …but force different document text via extract mock.
        base_doc = ['Clause A remains.', 'Force Majeure applies.']
        target_doc = ['Clause A remains.']

        original_resolve = HtmlRedlineEngine._resolve_compare_lines

        def fake_resolve(self_eng, base_version, target_version):
            # Simulate identical snapshots then differing documents.
            return base_doc, target_doc, 'document'

        HtmlRedlineEngine._resolve_compare_lines = fake_resolve
        try:
            result = engine.build_result(self.v1, self.v2)
        finally:
            HtmlRedlineEngine._resolve_compare_lines = original_resolve

        self.assertFalse(result['identical'])
        self.assertEqual(result['compare_source'], 'document')
        self.assertGreater(result['deletion_count'], 0)
        html = engine.render_html(result)
        self.assertIn('Compared from Word documents', html)
        self.assertIn('Force Majeure', html)
