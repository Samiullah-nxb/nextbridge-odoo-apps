# Part of NextContract. See LICENSE file for full copyright and licensing details.
"""Document Template Version preview uses the same PDF.js shell as contracts."""

import base64
import io

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


class TestBaseDocxPreview(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids |= cls.env.ref('clm.group_clm_legal_admin')
        cls.contract_type = cls.env.ref('clm.contract_type_services')
        cls.template = cls.env['clm.template'].create({
            'name': 'LEGAL CONSULTING AGREEMENT',
            'contract_type_id': cls.contract_type.id,
            'base_docx_upload': base64.b64encode(
                _minimal_docx_bytes('Preview me {{ partner_name }}.')
            ),
            'base_docx_upload_filename': 'LEGAL CONSULTING AGREEMENT.docx',
        })

    def test_preview_action_opens_wizard_with_pdfjs_when_libreoffice_available(self):
        if not find_soffice():
            self.skipTest('LibreOffice not available')
        version = self.template._active_base_docx_version()
        self.assertTrue(version)
        action = version.action_preview()
        self.assertEqual(action['res_model'], 'clm.base.docx.preview.wizard')
        wizard = self.env['clm.base.docx.preview.wizard'].browse(action['res_id'])
        self.assertTrue(wizard.exists())
        self.assertTrue(wizard.pdf_attachment_id)
        self.assertTrue(
            (wizard.pdf_attachment_id.mimetype or '').startswith('application/pdf')
        )
        html = wizard.preview_html or ''
        self.assertIn('o_clm_pdf_preview', html)
        self.assertIn('pdfjs/web/viewer.html', html)
        self.assertIn('zoom=100', html)
        self.assertIn('o_clm_pdf_fullscreen_btn', html)
        self.assertNotIn('native PDF preview', html)
        self.assertTrue(action['name'].startswith('Preview('))
        self.assertIn('.docx', action['name'])
        self.assertEqual(
            action['name'],
            'Preview(%s)' % version._preview_file_name(),
        )

    def test_preview_title_uses_version_label_not_stale_document_name(self):
        """Stale Documents filenames must not stick the dialog on an older — vN."""
        version = self.template._active_base_docx_version()
        self.assertTrue(version)
        # Simulate a later version whose Documents file still carries v17.
        version.document_id.write({'name': 'LEGAL CONSULTING AGREEMENT — v17.docx'})
        if version.document_id.attachment_id:
            version.document_id.attachment_id.write({
                'name': 'LEGAL CONSULTING AGREEMENT — v17.docx',
            })
        # Force a distinct version label (public — vN style).
        version.write({'name': 'LEGAL CONSULTING AGREEMENT — v18'})
        action = version.action_preview()
        self.assertEqual(
            action['name'],
            'Preview(LEGAL CONSULTING AGREEMENT — v18.docx)',
        )
        wizard = self.env['clm.base.docx.preview.wizard'].browse(action['res_id'])
        self.assertEqual(
            wizard.display_name,
            'Preview(LEGAL CONSULTING AGREEMENT — v18.docx)',
        )
        self.assertEqual(
            wizard.file_name,
            'LEGAL CONSULTING AGREEMENT — v18.docx',
        )

    def test_preview_falls_back_to_html_without_crashing(self):
        """Even without a PDF attachment, the wizard still renders something."""
        wizard = self.env['clm.base.docx.preview.wizard'].create({
            'template_id': self.template.id,
            'document_id': self.template.base_document_id.id,
            'kind': 'previous_changed',
            'file_name': self.template.base_document_id.name,
        })
        # Force the HTML path (no conversion).
        html = wizard.preview_html or ''
        self.assertTrue(html)
        self.assertEqual(
            wizard.title,
            'Preview(%s)' % self.template.base_document_id.name,
        )
        self.assertNotIn('native PDF preview', html)
