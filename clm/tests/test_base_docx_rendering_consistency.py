# Part of NextContract. See LICENSE file for full copyright and licensing details.
"""Base DOCX rendering consistency across Preview, DOCX, PDF and Final Contract.

Proves Format 4 uses the locked Base DOCX for every entry point, keeps that
lock when the Template Active version changes, and does not fall back to
Template Body / QWeb / default generation.
"""

import base64
import io
import re
import zipfile

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase

from odoo.addons.clm.services.docx_to_pdf import find_soffice


def _minimal_docx_bytes(*paragraphs):
    from docx import Document
    document = Document()
    if not paragraphs:
        paragraphs = ('',)
    first, *rest = paragraphs
    if document.paragraphs:
        document.paragraphs[0].text = first
    else:
        document.add_paragraph(first)
    for text in rest:
        document.add_paragraph(text)
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


def _docx_marker(raw_bytes):
    with zipfile.ZipFile(io.BytesIO(raw_bytes)) as archive:
        xml = archive.read('word/document.xml').decode('utf-8')
    return ' '.join(re.sub(r'<[^>]+>', ' ', xml).split())


def _pdf_marker(raw_bytes):
    import pypdfium2
    pdf = pypdfium2.PdfDocument(raw_bytes)
    text = ' '.join(page.get_textpage().get_text_range() for page in pdf)
    return ' '.join(text.split())


class TestBaseDocxRenderingConsistency(TransactionCase):

    def setUp(self):
        super().setUp()
        self.contract_type = self.env.ref('clm.contract_type_nda')
        self.clause = self.env['clm.clause'].create({
            'name': 'Confidentiality',
            'category': 'confidentiality',
            'standard_wording': '<p>Confidential for {{partner_name}}.</p>',
            'risk_level': 'medium',
        })
        self.clause.action_submit_review()
        self.clause.action_approve()
        self.clause.action_publish()

        raw_v1 = _minimal_docx_bytes(
            'BASE DOCX VERSION ONE MARKER',
            'Party {{partner_name}}.',
        )
        self.template = self.env['clm.template'].create({
            'name': 'NDA Base DOCX Render Template',
            'contract_type_id': self.contract_type.id,
            # Template Body also present — Base DOCX must still win.
            'content': '<h1>TEMPLATE BODY COVER — MUST NOT APPEAR</h1>',
            'base_docx_upload': base64.b64encode(raw_v1),
            'base_docx_upload_filename': 'nda_render_v1.docx',
            'state': 'draft',
        })
        self.env['clm.template.clause.line'].create({
            'template_id': self.template.id,
            'clause_id': self.clause.id,
            'sequence': 10,
            'section': 'Confidentiality',
            'required': True,
        })
        raw_v2 = _minimal_docx_bytes(
            'BASE DOCX VERSION TWO MARKER',
            'Active {{partner_name}}.',
        )
        self.template.write({
            'base_docx_upload': base64.b64encode(raw_v2),
            'base_docx_upload_filename': 'nda_render_v2.docx',
        })
        self.template.action_approve()
        self.template.action_publish()

        self.partner = self.env['res.partner'].create({'name': 'Acme Corp'})
        self.v1 = self.template.base_docx_version_ids.filtered('is_original')[:1]
        self.v2 = self.template._active_base_docx_version()
        self.assertTrue(self.v1)
        self.assertTrue(self.v2)
        self.assertNotEqual(self.v1, self.v2)

    def _new_contract(self, name='Render Contract'):
        return self.env['clm.contract'].create({
            'name': name,
            'contract_type_id': self.contract_type.id,
            'partner_id': self.partner.id,
            'template_id': self.template.id,
        })

    def _activate_new_base(self, marker, filename):
        self.template.write({
            'base_docx_upload': base64.b64encode(_minimal_docx_bytes(marker)),
            'base_docx_upload_filename': filename,
        })
        return self.template._active_base_docx_version()

    def _assert_base_marker(self, text, marker, forbidden=()):
        self.assertIn(marker, text)
        for bad in forbidden:
            self.assertNotIn(bad, text)

    def test_01_preview_uses_locked_base_docx_v1(self):
        if not find_soffice():
            self.skipTest('LibreOffice not available')
        # Lock Contract A to current Active (v2 in setUp — rename for clarity).
        # Use original v1 by activating it first for this scenario.
        self.v1.action_set_active()
        self.assertTrue(self.v1.is_active)
        contract = self._new_contract('Preview A')
        self.assertEqual(contract.base_docx_version_id, self.v1)
        self.assertTrue(contract._clm_get_document_generation_context()['uses_base_docx'])

        contract.invalidate_recordset(['contract_preview_html'])
        preview = contract.contract_preview_html or ''
        self._assert_base_marker(
            preview,
            'BASE DOCX VERSION ONE MARKER',
            forbidden=('TEMPLATE BODY COVER', 'BASE DOCX VERSION TWO MARKER'),
        )
        self.assertIn('o_clm_pdf_preview', preview)
        self.assertIn('final PDF document', preview)

    def test_02_generate_docx_uses_locked_base(self):
        self.v1.action_set_active()
        contract = self._new_contract('DOCX A')
        version = contract._generate_draft(renderer='docx').result_version_id
        raw = version._clm_generate_document('docx').raw
        text = _docx_marker(raw)
        self._assert_base_marker(
            text,
            'BASE DOCX VERSION ONE MARKER',
            forbidden=('TEMPLATE BODY COVER', 'BASE DOCX VERSION TWO MARKER'),
        )
        self.assertEqual(version.base_docx_version_id, self.v1)

    def test_03_generate_pdf_matches_docx_base(self):
        if not find_soffice():
            self.skipTest('LibreOffice not available')
        self.v1.action_set_active()
        contract = self._new_contract('PDF A')
        version = contract._generate_draft(renderer='docx').result_version_id
        docx_text = _docx_marker(version._clm_generate_document('docx').raw)
        pdf_text = _pdf_marker(version._clm_generate_document('pdf').raw)
        self._assert_base_marker(
            docx_text,
            'BASE DOCX VERSION ONE MARKER',
            forbidden=('TEMPLATE BODY COVER',),
        )
        self._assert_base_marker(
            pdf_text,
            'BASE DOCX VERSION ONE MARKER',
            forbidden=('TEMPLATE BODY COVER',),
        )

    def test_04_active_change_keeps_preview_docx_pdf_on_lock(self):
        if not find_soffice():
            self.skipTest('LibreOffice not available')
        self.v1.action_set_active()
        contract = self._new_contract('Stable A')
        self.assertEqual(contract.base_docx_version_id, self.v1)
        version = contract._generate_draft(renderer='docx').result_version_id

        v3 = self._activate_new_base('BASE DOCX VERSION THREE MARKER', 'nda_v3.docx')
        self.assertNotEqual(v3, self.v1)
        self.assertEqual(contract.base_docx_version_id, self.v1)

        contract.invalidate_recordset(['contract_preview_html'])
        preview = version._clm_render_version_preview()
        docx_text = _docx_marker(version._clm_generate_document('docx').raw)
        pdf_text = _pdf_marker(version._clm_generate_document('pdf').raw)
        for blob in (preview, docx_text, pdf_text):
            self._assert_base_marker(
                blob,
                'BASE DOCX VERSION ONE MARKER',
                forbidden=(
                    'BASE DOCX VERSION THREE MARKER',
                    'TEMPLATE BODY COVER',
                ),
            )

    def test_05_new_contract_uses_new_active(self):
        if not find_soffice():
            self.skipTest('LibreOffice not available')
        # setUp Active = v2
        contract_b = self._new_contract('Contract B')
        self.assertEqual(contract_b.base_docx_version_id, self.v2)
        version = contract_b._generate_draft(renderer='docx').result_version_id
        preview = version._clm_render_version_preview()
        docx_text = _docx_marker(version._clm_generate_document('docx').raw)
        pdf_text = _pdf_marker(version._clm_generate_document('pdf').raw)
        for blob in (preview, docx_text, pdf_text):
            self._assert_base_marker(
                blob,
                'BASE DOCX VERSION TWO MARKER',
                forbidden=('BASE DOCX VERSION ONE MARKER', 'TEMPLATE BODY COVER'),
            )

    def test_06_template_body_regression(self):
        template = self.env['clm.template'].create({
            'name': 'Body Only Template',
            'contract_type_id': self.contract_type.id,
            'content': '<h1>TEMPLATE BODY ONLY MARKER</h1>',
        })
        template.action_approve()
        template.action_publish()
        contract = self.env['clm.contract'].create({
            'name': 'Body Contract',
            'contract_type_id': self.contract_type.id,
            'partner_id': self.partner.id,
            'template_id': template.id,
        })
        self.assertEqual(contract._clm_document_generation_format(), 'template_body')
        self.assertFalse(contract._clm_get_document_generation_context()['uses_base_docx'])
        version = contract._generate_draft(renderer='docx').result_version_id
        docx_text = _docx_marker(version._clm_generate_document('docx').raw)
        self.assertIn('TEMPLATE BODY ONLY MARKER', docx_text)
        preview = contract.contract_preview_html or ''
        self.assertIn('TEMPLATE BODY ONLY MARKER', preview)

    def test_07_default_regression(self):
        template = self.env['clm.template'].create({
            'name': 'Bare Template',
            'contract_type_id': self.contract_type.id,
        })
        template.action_approve()
        template.action_publish()
        contract = self.env['clm.contract'].create({
            'name': 'Default Contract',
            'contract_type_id': self.contract_type.id,
            'partner_id': self.partner.id,
            'template_id': template.id,
        })
        self.assertEqual(contract._clm_document_generation_format(), 'default_template')
        self.assertFalse(contract._clm_get_document_generation_context()['uses_base_docx'])
        version = contract._generate_draft(renderer='plain_text').result_version_id
        self.assertTrue(version)

    def test_08_base_docx_takes_precedence_over_template_body(self):
        if not find_soffice():
            self.skipTest('LibreOffice not available')
        self.v1.action_set_active()
        contract = self._new_contract('Precedence')
        self.assertTrue(self.template.content)
        self.assertEqual(contract._clm_document_generation_format(), 'base_docx')
        gen = contract._clm_get_document_generation_context()
        self.assertTrue(gen['uses_base_docx'])
        self.assertEqual(gen['base_docx_version'], self.v1)
        contract.invalidate_recordset(['contract_preview_html'])
        preview = contract.contract_preview_html or ''
        self.assertIn('BASE DOCX VERSION ONE MARKER', preview)
        self.assertNotIn('TEMPLATE BODY COVER', preview)
        self.assertIn('o_clm_pdf_preview', preview)

    def test_09_missing_locked_base_docx_raises(self):
        self.v1.action_set_active()
        contract = self._new_contract('Missing File')
        self.assertEqual(contract.base_docx_version_id, self.v1)
        # Wipe the locked version's file bytes without deleting the document row.
        document = self.v1.document_id
        attachment = document.attachment_id if document else False
        self.assertTrue(attachment)
        attachment.write({'datas': False, 'raw': False})
        with self.assertRaises(UserError) as err:
            contract._render_contract_docx(kind='draft')
        message = str(err.exception)
        self.assertTrue(
            'can no longer be read' in message
            or 'could not be found or read' in message.lower()
            or 'no longer' in message.lower()
            or 'could not' in message.lower(),
            message,
        )
        with self.assertRaises(UserError):
            contract._render_contract_document('pdf', kind='draft')

    def test_10_draft_generate_and_later_generate_share_same_base(self):
        """Generate Draft DOCX and later Generate DOCX use the same locked file."""
        self.v1.action_set_active()
        contract = self._new_contract('Shared Path')
        req = contract._generate_draft(renderer='docx')
        version = req.result_version_id
        draft_text = _docx_marker(version.generated_docx_id.raw)
        later_text = _docx_marker(version._clm_generate_document('docx').raw)
        self.assertIn('BASE DOCX VERSION ONE MARKER', draft_text)
        self.assertIn('BASE DOCX VERSION ONE MARKER', later_text)
        self.assertNotIn('TEMPLATE BODY COVER', draft_text)
        self.assertNotIn('TEMPLATE BODY COVER', later_text)

    def test_12_base_docx_bytes_survives_bin_size_context(self):
        """UI bin_size context must not raise Incorrect padding on Preview."""
        if not find_soffice():
            self.skipTest('LibreOffice not available')
        self.v1.action_set_active()
        contract = self._new_contract('Bin Size Safe')
        version = contract._generate_draft(renderer='docx').result_version_id
        # Reproduce the UI context that turns attachment.datas into "57.25 Kb".
        contract = contract.with_context(bin_size=True)
        version = version.with_context(bin_size=True)
        raw = contract._render_contract_docx(version=version, kind='draft')
        self.assertTrue(raw.startswith(b'PK'))
        html = version._clm_render_version_preview()
        self.assertIn('BASE DOCX VERSION ONE MARKER', html)
        self.assertIn('o_clm_pdf_preview', html)
        self.assertNotIn('Incorrect padding', html)

    def test_13_version_preview_pdf_attachment_has_bytes(self):
        """Preview must not leave an empty PDF that breaks the iframe."""
        if not find_soffice():
            self.skipTest('LibreOffice not available')
        self.v1.action_set_active()
        contract = self._new_contract('Preview Payload')
        version = contract._generate_draft(renderer='docx').result_version_id
        # Generate Draft must not eagerly create an empty generated_pdf_id.
        if version.generated_pdf_id:
            raw = version.generated_pdf_id.with_context(bin_size=False).raw or b''
            self.assertTrue(
                raw.startswith(b'%PDF'),
                'Draft must not link an empty PDF attachment',
            )
        # content_preview_html compute historically wrote empty datas rows.
        html = version.with_context(bin_size=True)._clm_render_version_preview()
        self.assertIn('o_clm_pdf_preview', html)
        pdf = version.generated_pdf_id
        self.assertTrue(pdf)
        raw = pdf.with_context(bin_size=False).raw or b''
        self.assertGreater(pdf.file_size or 0, 0)
        self.assertTrue(raw.startswith(b'%PDF'), raw[:40])
        self.assertIn('BASE DOCX VERSION ONE MARKER', html)
        self.assertIn('unique=', html)
        self.assertIn('pdfjs/web/viewer.html', html)
        self.assertIn('zoom=100', html)

    def test_14_version_preview_opens_pdf_wizard(self):
        if not find_soffice():
            self.skipTest('LibreOffice not available')
        self.v1.action_set_active()
        contract = self._new_contract('Preview Wizard')
        version = contract._generate_draft(renderer='docx').result_version_id
        action = version.action_preview_version()
        self.assertEqual(action['res_model'], 'clm.pdf.preview.wizard')
        wizard = self.env['clm.pdf.preview.wizard'].browse(action['res_id'])
        self.assertEqual(wizard.version_id, version)
        self.assertIn('pdfjs/web/viewer.html', wizard.preview_html or '')
        self.assertIn('zoom=100', wizard.preview_html or '')
