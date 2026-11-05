# Part of NextContract. See LICENSE file for full copyright and licensing details.
"""
The complete lifecycle of a versioned contract, driven through the real
transition methods:

    template → Generate Draft (v1) → edit → Create New Version (v2)
    → Compare v1 ↔ v2 → Legal Review → Approval → Signature Draft
    → Send for Signature → signed (v3, executed)
    → Create Amendment → edit → v4 → Legal Review → Approval → signed (v5)
"""

import base64

from odoo.tests.common import TransactionCase

from odoo.addons.clm.services import FinalContractBuilder


class TestVersionedLifecycle(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Approving a workflow task is restricted to its assignee or a Legal
        # Admin; the test user drives the whole flow.
        cls.env.user.group_ids |= cls.env.ref('clm.group_clm_legal_admin')
        cls.contract_type = cls.env.ref('clm.contract_type_services')
        cls.partner = cls.env['res.partner'].create({
            'name': 'Acme Corporation', 'email': 'legal@acme.example.com',
        })
        clause = cls.env['clm.clause'].create({
            'name': 'Termination',
            'category': 'termination',
            'standard_wording': '<p>Either party may terminate this agreement by providing '
                                '{{notice_period_days}} days written notice.</p>',
        })
        clause.action_submit_review()
        clause.action_approve()
        clause.action_publish()
        cls.template = cls.env['clm.template'].create({
            'name': 'IT Services Agreement',
            'contract_type_id': cls.contract_type.id,
            'content': '<h2>IT SERVICES AGREEMENT</h2><p>Between {{company_name}} and {{partner_name}}.</p>',
        })
        cls.env['clm.template.clause.line'].create({
            'template_id': cls.template.id, 'clause_id': clause.id, 'section': 'Termination',
        })
        cls.template.action_approve()
        cls.template.action_publish()

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _new_version(self, contract, summary):
        wizard = self.env['clm.contract.version.wizard'].with_context(
            default_contract_id=contract.id,
        ).create({'change_summary': summary, 'change_classification': 'material'})
        return self.env['clm.contract.version'].browse(wizard.action_create_version()['res_id'])

    def _review_and_approve(self, contract):
        """Legal review and approval, completing whatever tasks the rules create."""
        for _step in range(6):
            if contract.state == 'approved':
                return
            contract.invalidate_recordset(['workflow_task_ids'])
            tasks = contract._lifecycle_open_tasks()
            if tasks:
                tasks.with_env(self.env).action_approve()
            elif contract.state == 'in_review':
                contract.action_complete_internal_review()
            else:
                contract.action_approve()
        self.assertEqual(contract.state, 'approved')

    def _sign(self, contract):
        contract.action_prepare_signature_draft()
        contract.action_send_for_signature()
        self.assertEqual(contract.state, 'pending_signature')
        request = contract.sign_request_ids.filtered(lambda r: r.state not in ('signed', 'canceled'))
        # Every signer must have signed before CLM executes the contract.
        for item in request.request_item_ids:
            self.env.cr.execute(
                "UPDATE sign_request_item SET state = 'completed' WHERE id = %s", (item.id,))
        request.invalidate_recordset()
        request.request_item_ids.invalidate_recordset()
        request.write({'state': 'signed'})
        self.assertEqual(contract.state, 'executed')
        return contract.final_version_id

    @staticmethod
    def _pdf_text(attachment):
        import pypdfium2
        pdf = pypdfium2.PdfDocument(base64.b64decode(attachment.datas))
        return ' '.join(' '.join(page.get_textpage().get_text_range() for page in pdf).split())

    @staticmethod
    def _notice(version):
        return version.content_snapshot['variables']['notice_period_days']

    # -------------------------------------------------------------------------
    # The lifecycle
    # -------------------------------------------------------------------------

    def test_versioned_contract_through_the_whole_lifecycle(self):
        contract = self.env['clm.contract'].create({
            'name': 'IT Services Agreement - Acme Corp',
            'contract_type_id': self.contract_type.id,
            'partner_id': self.partner.id,
            'template_id': self.template.id,
            'notice_period_days': 20,
        })

        # Draft → v1, edit → v2
        v1 = contract._generate_draft(renderer='docx').result_version_id
        contract.notice_period_days = 30
        v2 = self._new_version(contract, 'Notice period changed from 20 to 30 days')
        self.assertEqual(v2.parent_version_id, v1)

        # Compare v1 ↔ v2
        comparison = self.env['clm.document.comparison'].browse(
            contract.action_compare_versions()['res_id']
        )
        self.assertEqual((comparison.base_version_id, comparison.target_version_id), (v1, v2))
        self.assertIn('<del>20</del>', comparison.html_redline)
        self.assertIn('<ins>30</ins>', comparison.html_redline)

        # Review → approval → signing
        v2.action_activate()
        contract.action_submit_for_review()
        self.assertTrue(contract._lifecycle_open_tasks(), 'Legal review is a workflow task')
        self._review_and_approve(contract)
        signed = self._sign(contract)

        self.assertEqual(signed.source_version_id, v2)
        self.assertEqual(signed.content_snapshot, v2.content_snapshot)
        self.assertTrue(contract.content_locked)
        final_pdf = contract._get_final_contract_attachment('pdf')
        self.assertIn('_V%s_FINAL.pdf' % signed.version_number, final_pdf.name)
        self.assertIn('providing 30 days written notice', self._pdf_text(final_pdf))

        # Amendment → v4 → review → approval → signing
        contract.action_create_amendment()
        self.assertEqual(contract.state, 'draft')
        contract.notice_period_days = 45
        v4 = self._new_version(contract, 'Amendment: notice period 45 days')
        self.assertEqual(v4.parent_version_id, signed)
        v4.action_activate()
        self.assertEqual(v2.status, 'superseded')

        contract.action_submit_for_review()
        contract.invalidate_recordset(['workflow_task_ids'])
        open_tasks = contract._lifecycle_open_tasks()
        self.assertTrue(open_tasks, 'The amendment is reviewed again')
        archived = self.env['clm.workflow.task'].with_context(active_test=False).search([
            ('contract_id', '=', contract.id), ('active', '=', False),
        ])
        self.assertTrue(archived, 'The first review cycle stays on record, archived')
        self._review_and_approve(contract)
        amended = self._sign(contract)

        self.assertEqual(contract.final_version_id, amended)
        self.assertEqual(amended.source_version_id, v4)
        self.assertEqual(
            [self._notice(v) for v in (v1, v2, signed, v4, amended)],
            ['20', '30', '30', '45', '45'],
            'Every version keeps its own notice period',
        )
        content = contract._build_final_contract_content(version=amended, kind='signed')
        self.assertIn(
            'Either party may terminate this agreement by providing 45 days written notice.',
            FinalContractBuilder.content_lines(content),
        )
        self.assertIn('Executed', contract.lifecycle_chart_html)
