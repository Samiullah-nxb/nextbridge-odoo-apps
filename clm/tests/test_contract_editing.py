# Part of NextContract. See LICENSE file for full copyright and licensing details.
"""
Contract editing, versioned content, preview and document generation.

The scenario the business described: a contract is drafted from a template with
a 20-day notice period, edited to 30 days and captured again. Version 1 must
keep saying 20 and version 2 must say 30 — in the preview, the DOCX and the PDF
— and generating one version's files must never overwrite the other's.
"""

import base64
import io
import re
import zipfile

from lxml import html as lxml_html

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, new_test_user

from ..services import FinalContractBuilder


class TestContractEditing(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Moving a contract straight to a signed state uses migration mode,
        # which is reserved for Legal Admins.
        cls.env.user.group_ids |= cls.env.ref('clm.group_clm_legal_admin')
        cls.contract_type = cls.env.ref('clm.contract_type_services')
        cls.partner = cls.env['res.partner'].create({'name': 'Acme Corp'})

        Clause = cls.env['clm.clause']
        cls.payment = Clause.create({
            'name': 'Payment',
            'category': 'payment',
            'standard_wording': '<p>The Client will pay within 20 days.</p>',
        })
        cls.termination = Clause.create({
            'name': 'Termination',
            'category': 'termination',
            'standard_wording': '<p>Either party may terminate this agreement by providing '
                                '{{notice_period_days}} days written notice.</p>',
        })
        for clause in cls.payment | cls.termination:
            clause.action_submit_review()
            clause.action_approve()
            clause.action_publish()

        cls.template = cls.env['clm.template'].create({
            'name': 'IT Services Agreement',
            'contract_type_id': cls.contract_type.id,
            'content': '<h2>IT SERVICES AGREEMENT</h2>'
                       '<p>This agreement is made between {{company_name}} and {{partner_name}}.</p>',
        })
        cls.env['clm.template.clause.line'].create([
            {'template_id': cls.template.id, 'clause_id': cls.payment.id,
             'sequence': 10, 'section': 'Commercial'},
            {'template_id': cls.template.id, 'clause_id': cls.termination.id,
             'sequence': 20, 'section': 'Termination'},
        ])
        cls.template.action_approve()
        cls.template.action_publish()

        cls.legal = new_test_user(
            cls.env, login='clm_editing_legal',
            groups='base.group_user,clm.group_clm_legal,clm.group_clm_user',
        )

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _make_contract(self, **values):
        return self.env['clm.contract'].create({
            'name': 'IT Services Agreement - Acme Corp',
            'contract_type_id': self.contract_type.id,
            'partner_id': self.partner.id,
            'template_id': self.template.id,
            'notice_period_days': 20,
            **values,
        })

    def _draft_v1(self, contract):
        return contract._generate_draft(renderer='docx').result_version_id

    def _new_version(self, contract, summary):
        wizard = self.env['clm.contract.version.wizard'].with_context(
            default_contract_id=contract.id,
        ).create({'change_summary': summary})
        action = wizard.action_create_version()
        return self.env['clm.contract.version'].browse(action['res_id'])

    def _v1_and_v2(self):
        contract = self._make_contract()
        v1 = self._draft_v1(contract)
        contract.notice_period_days = 30
        v2 = self._new_version(contract, 'Notice period changed from 20 to 30 days')
        return contract, v1, v2

    @staticmethod
    def _text(version):
        content = version.contract_id._build_final_contract_content(version=version, kind='draft')
        return '\n'.join(FinalContractBuilder.content_lines(content))

    @staticmethod
    def _pdf_text(attachment):
        import pypdfium2
        pdf = pypdfium2.PdfDocument(base64.b64decode(attachment.datas))
        text = ' '.join(page.get_textpage().get_text_range() for page in pdf)
        return ' '.join(text.split())

    @staticmethod
    def _docx_text(attachment):
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(attachment.datas))) as archive:
            xml = archive.read('word/document.xml').decode('utf-8')
        return ' '.join(re.sub(r'<[^>]+>', ' ', xml).split())

    # -------------------------------------------------------------------------
    # TEST 1 — Version 1 from the template, with its documents
    # -------------------------------------------------------------------------

    def test_draft_creates_version_1_with_frozen_content_and_documents(self):
        contract = self._make_contract()
        v1 = self._draft_v1(contract)

        self.assertEqual(v1.version_number, 1)
        self.assertTrue(v1.content_snapshot, 'The draft freezes the contract content')
        self.assertEqual(
            contract.clause_ids.mapped('clause_id'), self.payment | self.termination,
            'Drafting turns the template clauses into the contract\'s own wording',
        )
        self.assertTrue(v1.document_id)
        self.assertEqual(v1.generated_docx_id.name, v1.document_id.name)
        self.assertIn('_V1_DRAFT.docx', v1.generated_docx_id.name)
        self.assertIn('providing 20 days written notice', self._docx_text(v1.generated_docx_id))

        pdf = v1._clm_generate_document('pdf')
        self.assertTrue(base64.b64decode(pdf.datas).startswith(b'%PDF'))
        self.assertIn('_V1_DRAFT.pdf', pdf.name)
        pdf_text = self._pdf_text(pdf)
        self.assertIn('providing 20 days written notice', pdf_text)
        self.assertIn('The Client will pay within 20 days.', pdf_text)
        self.assertIn('IT SERVICES AGREEMENT', pdf_text, 'The template body opens the document')
        self.assertIn('made between', pdf_text)

    # -------------------------------------------------------------------------
    # TEST 2 — Editing a term creates Version 2; Version 1 keeps its value
    # -------------------------------------------------------------------------

    def test_new_version_keeps_the_previous_version_unchanged(self):
        contract, v1, v2 = self._v1_and_v2()

        self.assertEqual(v2.version_number, 2)
        self.assertEqual(v2.parent_version_id, v1)
        self.assertEqual(v1.content_snapshot['variables']['notice_period_days'], '20')
        self.assertEqual(v2.content_snapshot['variables']['notice_period_days'], '30')

        v1_text, v2_text = self._text(v1), self._text(v2)
        self.assertIn('Notice Period: 20 days', v1_text)
        self.assertIn('providing 20 days written notice', v1_text)
        self.assertIn('Notice Period: 30 days', v2_text)
        self.assertIn('providing 30 days written notice', v2_text)
        self.assertNotIn('20 days written notice', v2_text)

    # -------------------------------------------------------------------------
    # TEST 3 — Editing a clause
    # -------------------------------------------------------------------------

    def test_clause_edit_is_captured_in_the_new_version_only(self):
        contract = self._make_contract()
        v1 = self._draft_v1(contract)
        payment = contract.clause_ids.filtered(lambda c: c.clause_id == self.payment)
        payment.content = '<p>The Client will pay within 30 days.</p>'
        self.assertTrue(payment.is_modified)

        v2 = self._new_version(contract, 'Payment term extended')
        self.assertIn('The Client will pay within 20 days.', self._text(v1))
        self.assertIn('The Client will pay within 30 days.', self._text(v2))
        self.assertIn('within 20 days', self.payment.standard_wording,
                      'Editing the contract never changes the clause library')

    # -------------------------------------------------------------------------
    # Preview
    # -------------------------------------------------------------------------

    def test_preview_reflects_the_selected_version(self):
        contract = self._make_contract()
        v1 = self._draft_v1(contract)
        self.assertFalse(contract.has_unversioned_changes)

        contract.notice_period_days = 30
        self.assertTrue(contract.has_unversioned_changes)
        self.assertIn('providing 30 days written notice', contract.contract_preview_html,
                      'The working preview shows the edit immediately')

        v2 = self._new_version(contract, 'Notice period changed')
        contract.invalidate_recordset(['has_unversioned_changes', 'current_version_id'])
        self.assertFalse(contract.has_unversioned_changes)
        self.assertEqual(contract.current_version_id, v2)
        self.assertIn('providing 20 days written notice', v1._clm_render_version_preview())
        self.assertNotIn('providing 30 days written notice', v1._clm_render_version_preview())
        self.assertIn('providing 30 days written notice', v2._clm_render_version_preview())

    @staticmethod
    def _preview_text(html):
        """Plain text from PDF preview HTML (visible banner + hidden text layer)."""
        root = lxml_html.fromstring(str(html or ''))
        return ' '.join(root.text_content().split())

    def test_contract_preview_shows_the_version_it_matches(self):
        contract = self._make_contract()
        preview = contract.contract_preview_html or ''
        self.assertIn('o_clm_pdf_preview', preview)
        self.assertIn('not captured in a version yet', preview)
        self.assertIn('Not versioned yet', self._preview_text(preview))

        v1 = self._draft_v1(contract)
        contract.invalidate_recordset(['contract_preview_html'])
        preview = contract.contract_preview_html or ''
        self.assertIn('identical to version v1', preview)
        self.assertIn('final PDF document', preview)

        contract.notice_period_days = 30
        preview = contract.contract_preview_html or ''
        self.assertIn(
            'v1 + unversioned edits', self._preview_text(preview),
            'Edits that are not in a version are never labelled as that version',
        )

        v2 = self._new_version(contract, 'Notice period changed')
        contract.clause_ids[:1].content = '<p>The Client will pay within 45 days.</p>'
        v3 = self._new_version(contract, 'Payment term extended')
        for version in (v1, v2, v3):
            html = version._clm_render_version_preview() or ''
            self.assertIn('o_clm_pdf_preview', html)
            self.assertIn('Version v%s' % version.version_number, html)
            self.assertIn('final PDF document', html)

        contract.invalidate_recordset(['contract_preview_html'])
        preview = contract.contract_preview_html
        self.assertIn('identical to version v3', preview)
        self.assertIn('o_clm_pdf_preview', preview)
        self.assertIn('pay within 45 days', self._preview_text(preview))

    # -------------------------------------------------------------------------
    # TEST 5 / 6 — DOCX and PDF of Version 2
    # -------------------------------------------------------------------------

    def test_docx_and_pdf_of_version_2_carry_its_content(self):
        contract, v1, v2 = self._v1_and_v2()

        docx_text = self._docx_text(v2._clm_generate_document('docx'))
        self.assertIn('providing 30 days written notice', docx_text)
        self.assertNotIn('providing 20 days written notice', docx_text)

        pdf_text = self._pdf_text(v2._clm_generate_document('pdf'))
        self.assertIn('providing 30 days written notice', pdf_text)
        self.assertNotIn('providing 20 days written notice', pdf_text)
        self.assertIn('Termination', pdf_text)
        self.assertIn('The Client will pay within 20 days.', pdf_text,
                      'The PDF carries the clause wording, not a summary')

    def test_contract_form_generates_the_current_version(self):
        contract, v1, v2 = self._v1_and_v2()
        action = contract.action_generate_pdf()
        self.assertEqual(action['type'], 'ir.actions.act_url')
        self.assertEqual(v2.generated_pdf_id.name, action['url'].split('filename=')[1])

        contract.notice_period_days = 45
        with self.assertRaises(UserError, msg='Unversioned edits must be captured first'):
            contract.action_generate_pdf()

    # -------------------------------------------------------------------------
    # TEST 7 — Version 1's files are never overwritten
    # -------------------------------------------------------------------------

    def test_version_1_documents_survive_version_2(self):
        contract = self._make_contract()
        v1 = self._draft_v1(contract)
        v1_docx = v1._clm_generate_document('docx')
        v1_pdf = v1._clm_generate_document('pdf')
        v1_docx_bytes = base64.b64decode(v1_docx.datas)
        v1_pdf_checksum = v1_pdf.checksum

        contract.notice_period_days = 30
        v2 = self._new_version(contract, 'Notice period changed')
        v2_docx = v2._clm_generate_document('docx')
        v2_pdf = v2._clm_generate_document('pdf')

        self.assertNotEqual(v1_docx, v2_docx)
        self.assertNotEqual(v1_pdf, v2_pdf)
        self.assertNotEqual(v1_docx.name, v2_docx.name)
        v1_docx.invalidate_recordset()
        v1_pdf.invalidate_recordset()
        self.assertEqual(base64.b64decode(v1_docx.datas), v1_docx_bytes)
        self.assertEqual(v1_pdf.checksum, v1_pdf_checksum)
        self.assertIn('providing 20 days written notice', self._docx_text(v1_docx))
        self.assertIn('providing 20 days written notice', self._pdf_text(v1_pdf))

    # -------------------------------------------------------------------------
    # TEST 8 — Frozen versions and locked contracts
    # -------------------------------------------------------------------------

    def test_version_content_can_never_be_rewritten(self):
        contract = self._make_contract()
        v1 = self._draft_v1(contract)
        with self.assertRaises(UserError):
            v1.with_user(self.legal).write({'content_snapshot': {'title': 'Tampered'}})
        with self.assertRaises(UserError, msg='Not even through sudo'):
            v1.sudo().write({'content_snapshot': {'title': 'Tampered'}})
        self.assertEqual(v1.content_snapshot['title'], 'IT Services Agreement - Acme Corp')

    def test_signed_contract_content_is_locked(self):
        contract = self._make_contract()
        self._draft_v1(contract)
        contract.with_context(migration_mode=True).write({'state': 'executed'})

        self.assertTrue(contract.content_locked)
        self.assertIn('Create Amendment', contract.content_lock_reason)
        clause = contract.clause_ids[:1]
        with self.assertRaises(UserError):
            clause.with_user(self.legal).write({'content': '<p>Changed after signing</p>'})
        with self.assertRaises(UserError):
            clause.write({'content': '<p>Changed after signing</p>'})
        with self.assertRaises(UserError):
            self.env['clm.contract.clause'].create({
                'contract_id': contract.id, 'title': 'Extra', 'content': '<p>Extra</p>',
            })
        with self.assertRaises(UserError):
            contract.action_load_template_clauses()
        with self.assertRaises(UserError):
            contract.with_user(self.legal).action_open_new_version_wizard()
        with self.assertRaises(UserError):
            contract._clm_create_content_version(change_summary='Silent change')

    def test_unchanged_contract_is_not_versioned_twice(self):
        contract = self._make_contract()
        self._draft_v1(contract)
        with self.assertRaises(UserError):
            self._new_version(contract, 'Nothing changed')

    # -------------------------------------------------------------------------
    # Single source of truth
    # -------------------------------------------------------------------------

    def test_wording_that_contradicts_the_terms_is_flagged(self):
        contract = self._make_contract(notice_period_days=30)
        contract._clm_load_template_clauses()
        termination = contract.clause_ids.filtered(lambda c: c.clause_id == self.termination)
        termination.content = '<p>Either party may terminate by giving 20 days written notice.</p>'

        content = contract._build_final_contract_content(
            kind='draft', snapshot=contract._clm_capture_content(),
        )
        self.assertIn(
            "Clause 'Termination' mentions 20 days' notice, but the contract's Notice "
            "Period is 30 days.", content['warnings'],
        )
        self.assertIn('mentions 20 days', contract.contract_preview_html)

        termination.content = '<p>Governed by the laws of {{no_such_value}}.</p>'
        content = contract._build_final_contract_content(
            kind='draft', snapshot=contract._clm_capture_content(),
        )
        self.assertTrue(any('{{no_such_value}}' in warning for warning in content['warnings']))

    def test_placeholders_use_the_contract_terms(self):
        contract = self._make_contract(notice_period_days=45)
        v1 = self._draft_v1(contract)
        text = self._text(v1)
        self.assertIn('providing 45 days written notice', text)
        self.assertIn('made between %s and Acme Corp.' % contract.company_id.name, text)
        self.assertNotIn('{{', text)
        self.assertFalse(
            FinalContractBuilder.content_warnings(v1.content_snapshot),
            'Placeholder-based wording is always consistent',
        )

    # -------------------------------------------------------------------------
    # TEST 10 — Templates and the clause library keep working
    # -------------------------------------------------------------------------

    def test_templates_and_library_clauses_keep_working(self):
        contract = self._make_contract()
        request = contract._generate_draft(renderer='plain_text')
        self.assertEqual(request.state, 'done')
        self.assertIn('Acme Corp', request.result_preview)
        self.assertIn('The Client will pay within 20 days.', request.result_preview)

        payment = contract.clause_ids.filtered(lambda c: c.clause_id == self.payment)
        payment.content = '<p>Custom payment wording.</p>'
        self.assertIn('within 20 days', self.payment.standard_wording)

        contract.action_load_template_clauses()
        reloaded = contract.clause_ids.filtered(lambda c: c.clause_id == self.payment)
        self.assertIn('within 20 days', reloaded.content)
        self.assertFalse(reloaded.is_modified)

        other = self._make_contract(name='Second IT Services Agreement')
        other_v1 = other._generate_draft(renderer='plain_text').result_version_id
        self.assertIn('The Client will pay within 20 days.', self._text(other_v1))

    def test_library_clause_can_be_added_to_a_contract(self):
        contract = self._make_contract()
        self._draft_v1(contract)
        added = self.env['clm.contract.clause'].create({
            'contract_id': contract.id,
            'title': 'Termination (additional)',
            'clause_id': self.termination.id,
            'content': self.termination.standard_wording,
        })
        self.assertEqual(added.source, 'library')
        self.assertEqual(added.clause_version, self.termination.version)
        self.assertTrue(contract.has_unversioned_changes)

    def test_editor_widgets_are_not_contract_wording(self):
        """What the HTML editor saves: a root wrapper and an attached-file box."""
        body = (
            '<div data-oe-version="2.0">'
            '<span class="o_file_box o-contenteditable-false" data-attachment-id="1">'
            '<span class="alert alert-info"><a href="/web/content/1">BASE TEMPLATE.docx</a></span>'
            '</span>'
            '<h2>IT SERVICES AGREEMENT</h2>'
            '<p>This agreement is made between {{company_name}} and {{partner_name}}.</p>'
            '<p>It starts on {{effective_date}}.</p>'
            '</div>'
        )
        snapshot = {
            'variables': {
                'company_name': 'YourCompany', 'partner_name': 'Acme', 'effective_date': '2026-10-01',
            },
            'template': {'body': body},
            'clauses': [],
        }
        entries = FinalContractBuilder(self.env['clm.contract'])._body_from_snapshot(snapshot)
        self.assertEqual([entry['heading'] for entry in entries], ['IT SERVICES AGREEMENT'])
        self.assertEqual(
            [block['text'] for block in entries[0]['blocks']],
            ['This agreement is made between YourCompany and Acme.', 'It starts on 2026-10-01.'],
        )

        # The editor may instead mark the first line's own <div>: nothing may be lost.
        clause = (
            '<div data-oe-version="2.0">Both parties agree to keep all confidential information</div>'
            '<div>received during the term of this agreement confidential</div>'
            '<div>without prior written consent.</div>'
        )
        self.assertEqual(
            [block['text'] for block in FinalContractBuilder._blocks_from_html(clause)],
            [
                'Both parties agree to keep all confidential information',
                'received during the term of this agreement confidential',
                'without prior written consent.',
            ],
        )
        # A wrapper with loose text before its blocks keeps that text too.
        wrapped = '<div data-oe-version="2.0">Opening line<p>Second line</p></div>'
        self.assertEqual(
            [block['text'] for block in FinalContractBuilder._blocks_from_html(wrapped)],
            ['Opening line', 'Second line'],
        )
        # Entity-escaped HTML (common Html-field storage) must not leak <p> tags.
        escaped = '&lt;p&gt;Each party shall keep confidential information.&lt;/p&gt;'
        self.assertEqual(
            [block['text'] for block in FinalContractBuilder._blocks_from_html(escaped)],
            ['Each party shall keep confidential information.'],
        )
        self.assertNotIn('<p>', FinalContractBuilder._blocks_from_html(escaped)[0]['text'])

    def test_read_only_user_can_preview_the_contract(self):
        """Read-Only users read contracts without access to field values or templates."""
        contract = self._make_contract()
        self._draft_v1(contract)
        reader = new_test_user(
            self.env, login='clm_editing_reader',
            groups='base.group_user,clm.group_clm_read_only',
        )
        as_reader = contract.with_user(reader)
        as_reader.invalidate_recordset(['contract_preview_html'])
        preview = as_reader.contract_preview_html
        self.assertNotIn('could not be built', preview)
        self.assertIn('providing 20 days written notice', preview)
        preview_action = as_reader.action_preview_contract()
        self.assertIn(
            preview_action['res_model'],
            ('clm.contract.version', 'clm.pdf.preview.wizard'),
        )

    # -------------------------------------------------------------------------
    # UI entry points
    # -------------------------------------------------------------------------

    def test_editor_preview_and_form_actions_load(self):
        contract = self._make_contract()
        for xmlid in ('clm.view_clm_contract_editor_form',
                      'clm.view_clm_contract_preview_form'):
            arch = contract.get_view(self.env.ref(xmlid).id, 'form')['arch']
            self.assertIn('contract_preview_html', arch)

        action = contract.action_open_contract_editor()
        self.assertEqual(
            action['views'], [(self.env.ref('clm.view_clm_contract_editor_form').id, 'form')]
        )
        form_arch = contract.get_view(self.env.ref('clm.view_clm_contract_form').id, 'form')['arch']
        for method in ('action_open_contract_editor', 'action_open_new_version_wizard',
                       'action_preview_contract', 'action_generate_docx', 'action_generate_pdf'):
            self.assertIn(method, form_arch)

        self.assertEqual(contract.action_preview_contract()['target'], 'new')
