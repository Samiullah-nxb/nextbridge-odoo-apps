# Part of NextContract. See LICENSE file for full copyright and licensing details.
"""
Tests for the final contract document and its PDF / DOCX exports.

Covers the full path a contract takes: draft with no final document → approved
final version → executed with a signed PDF, and the export behaviour at each
stage, including access control for a user who cannot read the contract.
"""

import base64
import datetime
import io
import re
import zipfile

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, new_test_user

_PDF_MIME = 'application/pdf'
_DOCX_MIME = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'

# A structurally valid one-page PDF, used as the "signed" artifact. Generated
# (not hand-written) because PyPDF2 cannot parse a PDF without an xref table.
def _make_signed_pdf():
    import io
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    pdf = canvas.Canvas(buf)
    pdf.drawString(72, 720, 'Signed contract')
    pdf.save()
    return buf.getvalue()


_SIGNED_PDF = _make_signed_pdf()

# 1x1 JPEG — stands in for a photographed signature page.
_JPEG = base64.b64decode(
    '/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0a'
    'HBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAABAAAAAAAA'
    'AAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AKp//2Q=='
)

# A short contract with the structure a real agreement has: numbered headings,
# a recital, and lettered sub-paragraphs.
_CONTRACT_TEXT = """SERVICES AGREEMENT
THIS AGREEMENT is entered into between the parties identified below, who
agree as follows:
1. Definitions
In this Agreement, "Confidential Information" means any non-public
information disclosed by either party to the other.
2. Scope of Services
The Supplier shall provide the services described in Schedule 1, including:
(a) implementation and configuration;
(b) training of the Customer's staff.
3. Governing Law
This Agreement is governed by the laws of Ireland.
"""


class TestFinalContract(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.contract_type = cls.env.ref('clm.contract_type_nda')
        cls.partner = cls.env['res.partner'].create({
            'name': 'Acme Industries',
            'street': '12 Market Street',
            'city': 'Dublin',
        })

    def _make_contract(self, state='draft'):
        contract = self.env['clm.contract'].with_context(migration_mode=True).create({
            'name': 'Acme Services Agreement',
            'contract_type_id': self.contract_type.id,
            'partner_id': self.partner.id,
            'effective_date': datetime.date(2026, 1, 1),
            'expiry_date': datetime.date(2026, 12, 31),
            'contract_value': 125000.0,
            'state': state,
        })
        return contract.with_context(migration_mode=False)

    def _make_version(self, contract, version_type, status='active', datas=None,
                      mimetype=_PDF_MIME, name='contract.pdf'):
        document = False
        if datas is not None:
            attachment = self.env['ir.attachment'].create({
                'name': name,
                'type': 'binary',
                'datas': base64.b64encode(datas),
                'mimetype': mimetype,
            })
            document = self.env['documents.document'].create({
                'name': name,
                'attachment_id': attachment.id,
            })
        return self.env['clm.contract.version'].create({
            'contract_id': contract.id,
            'version_type': version_type,
            'status': status,
            'document_id': document.id if document else False,
        })

    # -------------------------------------------------------------------------
    # Availability
    # -------------------------------------------------------------------------

    def test_no_final_contract_before_approval(self):
        contract = self._make_contract()
        self._make_version(contract, 'internal_draft')
        self.assertFalse(contract.final_contract_available)
        self.assertFalse(contract.final_contract_kind)
        self.assertFalse(contract.final_version_label)

    def test_draft_version_alone_is_not_final(self):
        """An approved contract whose only version is an internal draft."""
        contract = self._make_contract(state='approved')
        self._make_version(contract, 'internal_draft')
        self.assertFalse(contract.final_contract_available)

    def test_download_before_final_version_raises(self):
        contract = self._make_contract()
        with self.assertRaises(UserError):
            contract.action_download_final_pdf()

    def test_approved_version_becomes_final(self):
        contract = self._make_contract(state='approved')
        version = self._make_version(contract, 'signature_draft')
        self.assertTrue(contract.final_contract_available)
        self.assertEqual(contract.final_contract_kind, 'approved')
        self.assertEqual(contract.final_version_id, version)
        self.assertEqual(contract.final_version_label, 'v%s' % version.version_number)
        self.assertEqual(contract.final_contract_label, 'Final Approved Contract')

    def test_executed_version_supersedes_approved(self):
        contract = self._make_contract(state='approved')
        signature_draft = self._make_version(contract, 'signature_draft')
        contract.write({'state': 'pending_signature'})
        executed = contract._process_contract_execution(
            source_version_id=signature_draft.id,
            execution_method='manual',
            execution_date=datetime.date(2026, 9, 10),
            executed_document_id=self._signed_document().id,
            notes='Counter-signed by both parties',
            signatory='Jordan Blake',
        )
        self.assertEqual(contract.final_version_id, executed)
        self.assertEqual(contract.final_contract_kind, 'signed')
        self.assertEqual(contract.final_contract_label, 'Final Signed Contract')
        self.assertEqual(contract.final_contract_status, 'Signed')
        self.assertEqual(executed.execution_signatory, 'Jordan Blake')

    def test_older_versions_remain_available(self):
        contract = self._make_contract(state='approved')
        draft = self._make_version(contract, 'internal_draft', status='superseded')
        signature_draft = self._make_version(contract, 'signature_draft')
        self.assertIn(draft, contract.version_ids)
        self.assertNotEqual(contract.final_version_id, draft)
        self.assertEqual(contract.final_version_id, signature_draft)

    # -------------------------------------------------------------------------
    # Generated exports
    # -------------------------------------------------------------------------

    def test_generated_pdf_export(self):
        contract = self._make_contract(state='approved')
        self._make_version(contract, 'signature_draft')
        data, mimetype, filename = contract._render_contract_document(
            'pdf', version=contract.final_version_id, kind='approved'
        )
        self.assertEqual(mimetype, _PDF_MIME)
        self.assertTrue(data.startswith(b'%PDF'), 'A real PDF must be produced')
        self.assertTrue(filename.endswith('_FINAL.pdf'), filename)
        self.assertIn('Acme_Services_Agreement', filename)

    def test_generated_docx_export(self):
        contract = self._make_contract(state='approved')
        self._make_version(contract, 'signature_draft')
        data, mimetype, filename = contract._render_contract_document(
            'docx', version=contract.final_version_id, kind='approved'
        )
        self.assertEqual(mimetype, _DOCX_MIME)
        self.assertTrue(filename.endswith('_FINAL.docx'), filename)

        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            document_xml = archive.read('word/document.xml').decode('utf-8')
        self.assertIn('Acme Services Agreement', document_xml)
        self.assertIn('Acme Industries', document_xml)
        # The redesigned layout renders section titles in capitals.
        self.assertIn('signatures', document_xml.lower())

    def test_pdf_and_docx_share_one_source(self):
        """Both formats must be rendered from the same final version."""
        contract = self._make_contract(state='approved')
        version = self._make_version(contract, 'signature_draft')
        content = contract._build_final_contract_content(version=version, kind='approved')
        self.assertEqual(content['version'], version)
        self.assertEqual(content['reference'], contract.contract_ref)
        self.assertEqual(content['kind_label'], 'Final Approved Contract')

        pdf = contract._get_final_contract_attachment('pdf')
        docx = contract._get_final_contract_attachment('docx')
        self.assertEqual(pdf.res_id, contract.id)
        self.assertEqual(docx.res_id, contract.id)
        self.assertEqual(pdf.mimetype, _PDF_MIME)
        self.assertEqual(docx.mimetype, _DOCX_MIME)
        self.assertTrue(base64.b64decode(pdf.datas).startswith(b'%PDF'))
        self.assertTrue(base64.b64decode(docx.datas).startswith(b'PK'))

    def test_export_is_reused_between_downloads(self):
        contract = self._make_contract(state='approved')
        self._make_version(contract, 'signature_draft')
        first = contract._get_final_contract_attachment('pdf')
        second = contract._get_final_contract_attachment('pdf')
        self.assertEqual(first, second, 'A second download must reuse the export')
        self.assertEqual(
            self.env['ir.attachment'].search_count([
                ('res_model', '=', 'clm.contract'),
                ('res_id', '=', contract.id),
                ('name', '=', first.name),
            ]), 1, 'Downloads must not pile up duplicate attachments'
        )

    def test_stale_export_is_refreshed_in_place(self):
        contract = self._make_contract(state='approved')
        version = self._make_version(contract, 'signature_draft')
        first = contract._get_final_contract_attachment('pdf')

        # Postgres stamps write_date from the transaction clock, so an export
        # made after a later edit is simulated by ageing the attachment.
        self.env.cr.execute(
            "UPDATE ir_attachment SET write_date = write_date - interval '1 day' WHERE id = %s",
            [first.id],
        )
        first.invalidate_recordset(['write_date'])
        self.assertTrue(contract._is_final_export_stale(first, version))

        refreshed = contract._get_final_contract_attachment('pdf')
        self.assertEqual(first, refreshed, 'The export is rewritten, not duplicated')
        self.assertFalse(contract._is_final_export_stale(refreshed, version))

    # -------------------------------------------------------------------------
    # Signed contract priority
    # -------------------------------------------------------------------------

    def _signed_document(self):
        attachment = self.env['ir.attachment'].create({
            'name': 'signed_contract.pdf',
            'type': 'binary',
            'datas': base64.b64encode(_SIGNED_PDF),
            'mimetype': _PDF_MIME,
        })
        return self.env['documents.document'].create({
            'name': 'signed_contract.pdf',
            'attachment_id': attachment.id,
        })

    def _execute(self, contract):
        signature_draft = self._make_version(contract, 'signature_draft')
        contract.write({'state': 'pending_signature'})
        return contract._process_contract_execution(
            source_version_id=signature_draft.id,
            execution_method='odoo_sign',
            execution_date=datetime.date(2026, 9, 10),
            executed_document_id=self._signed_document().id,
        )

    def test_signed_artifact_is_served_unmodified(self):
        contract = self._make_contract(state='approved')
        executed = self._execute(contract)
        artifact = contract._get_signed_artifact()
        self.assertEqual(base64.b64decode(artifact.datas), _SIGNED_PDF,
                         'The signed PDF must be served byte-for-byte')
        self.assertEqual(
            base64.b64decode(executed.document_id.attachment_id.datas), _SIGNED_PDF,
            'The signed document itself must not be rewritten'
        )
        self.assertTrue(contract.signed_artifact_available)
        self.assertTrue(contract.signed_artifact_is_pdf)

    def test_generated_pdf_is_never_the_signed_artifact(self):
        """The generated contract PDF must not masquerade as the signed one."""
        contract = self._make_contract(state='approved')
        self._execute(contract)
        generated = contract._get_final_contract_attachment('pdf')
        artifact = contract._get_signed_artifact()
        self.assertNotEqual(generated, artifact)
        self.assertNotEqual(base64.b64decode(generated.datas), _SIGNED_PDF)
        self.assertNotIn('SIGNED', generated.name,
                         'A generated file must not be named as the signed one')
        self.assertEqual(artifact.name, 'signed_contract.pdf')

    def test_download_signed_document_action(self):
        contract = self._make_contract(state='approved')
        self._execute(contract)
        action = contract.action_download_signed_document()
        self.assertEqual(action['type'], 'ir.actions.act_url')
        self.assertIn('/web/content/%s' % contract._get_signed_artifact().id, action['url'])

    def test_download_signed_document_without_artifact_raises(self):
        contract = self._make_contract(state='approved')
        signature_draft = self._make_version(contract, 'signature_draft')
        contract.write({'state': 'pending_signature'})
        # Sign reported completion but produced no document to store.
        contract._process_contract_execution(
            source_version_id=signature_draft.id,
            execution_method='odoo_sign',
            execution_date=datetime.date(2026, 9, 10),
            executed_document_id=False,
        )
        self.assertFalse(contract.signed_artifact_available)
        with self.assertRaises(UserError):
            contract.action_download_signed_document()
        # The generated contract document is still available and honest.
        self.assertTrue(contract._get_final_contract_attachment('pdf'))

    def test_non_pdf_signed_artifact_is_flagged(self):
        """A scanned/photographed signature page is signed, but is not a PDF."""
        contract = self._make_contract(state='approved')
        signature_draft = self._make_version(contract, 'signature_draft')
        contract.write({'state': 'pending_signature'})
        image = self.env['documents.document'].create({
            'name': 'signature_page.jpg',
            'attachment_id': self.env['ir.attachment'].create({
                'name': 'signature_page.jpg', 'type': 'binary',
                'datas': base64.b64encode(_JPEG), 'mimetype': 'image/jpeg',
            }).id,
        })
        contract._process_contract_execution(
            source_version_id=signature_draft.id,
            execution_method='manual',
            execution_date=datetime.date(2026, 9, 10),
            executed_document_id=image.id,
            signatory='Jordan Blake',
        )
        self.assertTrue(contract.signed_artifact_available)
        self.assertFalse(contract.signed_artifact_is_pdf)
        self.assertIn('JPEG', contract.signed_artifact_label)

    def test_docx_is_generated_alongside_the_signed_pdf(self):
        contract = self._make_contract(state='approved')
        self._execute(contract)
        docx = contract._get_final_contract_attachment('docx')
        self.assertEqual(docx.mimetype, _DOCX_MIME)
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(docx.datas))) as archive:
            document_xml = archive.read('word/document.xml').decode('utf-8')
        # The redesigned header shows the status (EXECUTED), not the kind label.
        self.assertIn('executed', document_xml.lower())
        self.assertIn('authoritative signed artifact', document_xml)

    # -------------------------------------------------------------------------
    # The contract's own wording ends up in the exports
    # -------------------------------------------------------------------------

    def test_body_comes_from_the_contract_document(self):
        """A contract with no template still exports its real clause text."""
        contract = self._make_contract(state='approved')
        version = self._make_version(contract, 'signature_draft')
        version.sudo().write({
            'extracted_text': _CONTRACT_TEXT,
            'text_layer_status': 'available',
            'text_source': 'native',
        })
        content = contract._build_final_contract_content(version=version, kind='approved')
        self.assertEqual(content['content_origin'], 'document')
        headings = [entry['heading'] for entry in content['body']]
        self.assertIn('1. Definitions', headings)
        self.assertIn('2. Scope of Services', headings)
        self.assertIn('3. Governing Law', headings)
        body_text = ' '.join(
            block['text'] for entry in content['body'] for block in entry['blocks']
        )
        self.assertIn('information disclosed by either party', body_text)
        self.assertNotIn('held in the attached', body_text)

    def test_text_is_taken_from_the_executed_version_lineage(self):
        """An executed version holding only a scan still exports the wording.

        The signed artifact carries no text layer; the wording lives on the
        signature draft it was executed from.
        """
        contract = self._make_contract(state='approved')
        signature_draft = self._make_version(contract, 'signature_draft')
        signature_draft.sudo().write({
            'extracted_text': _CONTRACT_TEXT,
            'text_layer_status': 'available',
        })
        contract.write({'state': 'pending_signature'})
        image = self.env['documents.document'].create({
            'name': 'signature_page.jpg',
            'attachment_id': self.env['ir.attachment'].create({
                'name': 'signature_page.jpg', 'type': 'binary',
                'datas': base64.b64encode(_JPEG), 'mimetype': 'image/jpeg',
            }).id,
        })
        executed = contract._process_contract_execution(
            source_version_id=signature_draft.id,
            execution_method='manual',
            execution_date=datetime.date(2026, 9, 10),
            executed_document_id=image.id,
            signatory='Jordan Blake',
        )
        content = contract._build_final_contract_content(version=executed, kind='signed')
        self.assertEqual(content['content_origin'], 'document')
        self.assertEqual(
            content['content_version_label'],
            'v%s' % signature_draft.version_number,
        )
        self.assertIn('1. Definitions', [e['heading'] for e in content['body']])

    def test_real_clauses_reach_both_exports(self):
        contract = self._make_contract(state='approved')
        version = self._make_version(contract, 'signature_draft')
        version.sudo().write({
            'extracted_text': _CONTRACT_TEXT,
            'text_layer_status': 'available',
        })

        pdf_bytes, _m, _n = contract._render_contract_document(
            'pdf', version=version, kind='approved')
        pdf_text = self._pdf_text(pdf_bytes)
        self.assertIn('Scope of Services', pdf_text)
        self.assertIn('information disclosed by either party', pdf_text)
        self.assertNotIn('held in the attached', pdf_text)

        docx_bytes, _m, _n = contract._render_contract_document(
            'docx', version=version, kind='approved')
        docx_text = self._docx_text(docx_bytes)
        self.assertIn('Scope of Services', docx_text)
        self.assertIn('information disclosed by either party', docx_text)
        self.assertNotIn('held in the attached', docx_text)

    def test_text_is_extracted_from_the_file_when_not_cached(self):
        """No AI job has run, so the text layer is filled in on demand."""
        contract = self._make_contract(state='approved')
        version = self._make_version(
            contract, 'signature_draft', datas=self._text_docx(_CONTRACT_TEXT),
            mimetype=_DOCX_MIME, name='agreed.docx',
        )
        self.assertFalse(version.extracted_text)
        content = contract._build_final_contract_content(version=version, kind='approved')
        self.assertEqual(content['content_origin'], 'document')
        self.assertIn('1. Definitions', [e['heading'] for e in content['body']])
        version.invalidate_recordset(['extracted_text'])
        self.assertTrue(version.extracted_text, 'Extracted text must be cached back')

    def test_missing_wording_is_reported_not_papered_over(self):
        contract = self._make_contract(state='approved')
        version = self._make_version(contract, 'signature_draft')
        content = contract._build_final_contract_content(version=version, kind='approved')
        self.assertEqual(content['content_origin'], 'none')
        self.assertFalse(content['body'])
        pdf_text = self._pdf_text(contract._render_contract_document(
            'pdf', version=version, kind='approved')[0])
        self.assertIn('No contract wording could be recovered', pdf_text)

    # -------------------------------------------------------------------------
    # Signature data validation
    # -------------------------------------------------------------------------

    def test_signed_without_signer_details_makes_no_claim(self):
        contract = self._make_contract(state='approved')
        self._execute(contract)
        content = contract._build_final_contract_content(
            version=contract.final_version_id, kind='signed')
        self.assertEqual(content['signature_evidence'], 'none')
        pdf_text = self._pdf_text(contract._render_contract_document(
            'pdf', version=contract.final_version_id, kind='signed')[0])
        self.assertNotIn('Fully Signed', pdf_text)
        self.assertIn('no signer details were captured', pdf_text)

    def test_manual_signatory_is_reported(self):
        contract = self._make_contract(state='approved')
        signature_draft = self._make_version(contract, 'signature_draft')
        contract.write({'state': 'pending_signature'})
        executed = contract._process_contract_execution(
            source_version_id=signature_draft.id,
            execution_method='manual',
            execution_date=datetime.date(2026, 9, 10),
            executed_document_id=self._signed_document().id,
            signatory='Jordan Blake',
        )
        content = contract._build_final_contract_content(version=executed, kind='signed')
        self.assertEqual(content['signature_evidence'], 'manual')
        self.assertEqual(content['signatures'][0]['name'], 'Jordan Blake')
        self.assertEqual(content['signatures'][0]['signed_on'], '10 Sep 2026')

    def test_manual_sign_skips_append_when_docx_has_execution_section(self):
        """Uploaded / Base DOCX execution layout must not get a second Signatures table."""
        from odoo.addons.clm.services.final_contract_docx import (
            FinalContractDocxWriter,
        )

        contract = self._make_contract(state='approved')
        signature_draft = self._make_version(contract, 'signature_draft')
        contract.write({'state': 'pending_signature'})
        executed = contract._process_contract_execution(
            source_version_id=signature_draft.id,
            execution_method='manual',
            execution_date=datetime.date(2026, 10, 9),
            executed_document_id=self._signed_document().id,
            signatory='Waqar',
        )
        content = contract._build_final_contract_content(version=executed, kind='signed')
        self.assertEqual(content['signature_evidence'], 'manual')
        self.assertEqual(content['signatures'][0]['name'], 'Waqar')
        self.assertEqual(content['signatures'][0]['method'], 'Manual signature')

        raw = self._text_docx(
            '5. Execution and Signatures\n'
            'IN WITNESS WHEREOF, the Parties execute this Agreement.\n'
            'FOR Contract Provider Ltd.\n'
            'Name: Mitchell Admin\n'
            'Signature: Provider ltd\n'
            'Date: 10/09/2026\n'
            'END OF AGREEMENT\n'
        )
        out = FinalContractDocxWriter().append_signatures(raw, content)
        self.assertEqual(out, raw)
        text = self._docx_text(out)
        self.assertIn('Execution and Signatures', text)
        self.assertNotIn(
            'The parties executed this agreement as recorded below.', text,
        )
        # Structured metadata remains on the contract after the skip.
        again = contract._build_final_contract_content(version=executed, kind='signed')
        self.assertEqual(again['signature_evidence'], 'manual')
        self.assertEqual(again['signatures'][0]['name'], 'Waqar')

    def test_signature_image_is_embedded(self):
        contract = self._make_contract(state='approved')
        signature_draft = self._make_version(contract, 'signature_draft')
        contract.write({'state': 'pending_signature'})
        image = self.env['documents.document'].create({
            'name': 'signature_page.jpg',
            'attachment_id': self.env['ir.attachment'].create({
                'name': 'signature_page.jpg', 'type': 'binary',
                'datas': base64.b64encode(_JPEG), 'mimetype': 'image/jpeg',
            }).id,
        })
        executed = contract._process_contract_execution(
            source_version_id=signature_draft.id,
            execution_method='manual',
            execution_date=datetime.date(2026, 9, 10),
            executed_document_id=image.id,
        )
        content = contract._build_final_contract_content(version=executed, kind='signed')
        self.assertTrue(content['signature_image'])
        self.assertEqual(content['signature_image']['mimetype'], 'image/jpeg')

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    @staticmethod
    def _pdf_text(pdf_bytes):
        import pypdfium2
        document = pypdfium2.PdfDocument(io.BytesIO(pdf_bytes))
        return '\n'.join(
            page.get_textpage().get_text_range() for page in document
        )

    @staticmethod
    def _docx_text(docx_bytes):
        with zipfile.ZipFile(io.BytesIO(docx_bytes)) as archive:
            return re.sub(
                r'<[^>]+>', ' ', archive.read('word/document.xml').decode('utf-8')
            )

    @staticmethod
    def _text_docx(text):
        import docx
        document = docx.Document()
        for line in text.splitlines():
            document.add_paragraph(line)
        buffer = io.BytesIO()
        document.save(buffer)
        return buffer.getvalue()

    # -------------------------------------------------------------------------
    # Actions and access control
    # -------------------------------------------------------------------------

    def test_download_actions_return_scoped_urls(self):
        contract = self._make_contract(state='approved')
        self._make_version(contract, 'signature_draft')
        for action_name in ('action_download_final_pdf', 'action_download_final_docx'):
            action = getattr(contract, action_name)()
            self.assertEqual(action['type'], 'ir.actions.act_url')
            self.assertIn('/web/content/', action['url'])
            self.assertIn('download=true', action['url'])

    def test_view_final_pdf_opens_inline(self):
        contract = self._make_contract(state='approved')
        self._make_version(contract, 'signature_draft')
        action = contract.action_view_final_pdf()
        self.assertEqual(action['type'], 'ir.actions.act_window')
        self.assertEqual(action['res_model'], 'clm.pdf.preview.wizard')
        wizard = self.env['clm.pdf.preview.wizard'].browse(action['res_id'])
        preview = wizard.preview_html or ''
        self.assertIn('o_clm_pdf_iframe', preview)
        self.assertIn('/web/content/', preview)

    def test_final_contract_wizard_from_list(self):
        contract = self._make_contract(state='approved')
        self._make_version(contract, 'signature_draft')
        action = contract.action_open_final_contract_wizard()
        self.assertEqual(action['res_model'], 'clm.contract.final.wizard')
        self.assertEqual(action['target'], 'new')
        wizard = self.env['clm.contract.final.wizard'].with_context(
            active_model='clm.contract', active_id=contract.id,
        ).create({'contract_id': contract.id})
        pdf_action = wizard.action_view_pdf()
        self.assertEqual(pdf_action['res_model'], 'clm.pdf.preview.wizard')
        docx_action = wizard.action_download_docx()
        self.assertIn('download=true', docx_action['url'])

    def test_view_action_opens_generated_final_pdf(self):
        contract = self._make_contract(state='approved')
        self._execute(contract)
        action = contract.action_view_final_contract()
        self.assertEqual(action['type'], 'ir.actions.act_window')
        self.assertEqual(action['res_model'], 'clm.pdf.preview.wizard')
        wizard = self.env['clm.pdf.preview.wizard'].browse(action['res_id'])
        preview = wizard.preview_html or ''
        self.assertIn('o_clm_pdf_iframe', preview)
        self.assertIn('_FINAL.pdf', wizard.attachment_id.name)

    def test_view_signed_artifact_opens_upload(self):
        contract = self._make_contract(state='approved')
        self._execute(contract)
        action = contract.action_view_signed_artifact()
        self.assertEqual(action['type'], 'ir.actions.act_window')
        self.assertEqual(action['res_model'], 'clm.pdf.preview.wizard')
        wizard = self.env['clm.pdf.preview.wizard'].browse(action['res_id'])
        self.assertNotIn('_FINAL.pdf', wizard.attachment_id.name or '')
        self.assertIn('o_clm_pdf_iframe', wizard.preview_html or '')

    def test_view_signed_word_upload_converts_to_pdf(self):
        """Manual Word uploads must open in View Signed PDF via LibreOffice."""
        from odoo.addons.clm.services.docx_to_pdf import find_soffice
        from unittest.mock import patch

        contract = self._make_contract(state='approved')
        signature_draft = self._make_version(contract, 'signature_draft')
        contract.write({'state': 'pending_signature'})
        word_bytes = self._text_docx(
            '5. Execution and Signatures\n'
            'IN WITNESS WHEREOF.\n'
            'Signed manually.\n'
        )
        document = self.env['documents.document'].create({
            'name': 'Manually_Signed.doc',
            'attachment_id': self.env['ir.attachment'].create({
                'name': 'Manually_Signed.docx',
                'type': 'binary',
                'datas': base64.b64encode(word_bytes),
                'mimetype': _DOCX_MIME,
            }).id,
        })
        contract._process_contract_execution(
            source_version_id=signature_draft.id,
            execution_method='manual',
            execution_date=datetime.date(2026, 10, 12),
            executed_document_id=document.id,
            signatory='Waqar',
        )
        self.assertTrue(contract.signed_artifact_available)
        self.assertFalse(contract.signed_artifact_is_pdf)

        if find_soffice():
            action = contract.action_view_signed_artifact()
        else:
            with patch(
                'odoo.addons.clm.services.docx_to_pdf.word_bytes_to_pdf',
                return_value=_SIGNED_PDF,
            ):
                action = contract.action_view_signed_artifact()

        self.assertEqual(action['type'], 'ir.actions.act_window')
        self.assertEqual(action['res_model'], 'clm.pdf.preview.wizard')
        wizard = self.env['clm.pdf.preview.wizard'].browse(action['res_id'])
        self.assertEqual(wizard.attachment_id.mimetype, _PDF_MIME)
        self.assertTrue(
            wizard.attachment_id.name.startswith('CLM_Signed_Preview__'),
            wizard.attachment_id.name,
        )
        self.assertTrue(
            (wizard.attachment_id.raw or b'').startswith(b'%PDF')
            or base64.b64decode(wizard.attachment_id.datas or b'').startswith(b'%PDF')
        )
        # Original Word upload on the executed version must stay untouched.
        executed = contract.final_version_id
        original = executed.document_id.attachment_id
        self.assertEqual(original.mimetype, _DOCX_MIME)

    def test_view_signed_jpeg_still_rejected(self):
        contract = self._make_contract(state='approved')
        signature_draft = self._make_version(contract, 'signature_draft')
        contract.write({'state': 'pending_signature'})
        image = self.env['documents.document'].create({
            'name': 'signature_page.jpg',
            'attachment_id': self.env['ir.attachment'].create({
                'name': 'signature_page.jpg', 'type': 'binary',
                'datas': base64.b64encode(_JPEG), 'mimetype': 'image/jpeg',
            }).id,
        })
        contract._process_contract_execution(
            source_version_id=signature_draft.id,
            execution_method='manual',
            execution_date=datetime.date(2026, 10, 12),
            executed_document_id=image.id,
            signatory='Waqar',
        )
        with self.assertRaises(UserError) as err:
            contract.action_view_signed_artifact()
        self.assertIn('PDF or Word', str(err.exception))

    def test_legal_reviewer_without_sign_access_can_export(self):
        """Exports must not require Odoo Sign's own group.

        The signature block reads sign.request, which NextContract roles cannot see; the
        builder reads it with sudo once contract access is established.
        """
        contract = self._make_contract(state='approved')
        self._execute(contract)

        reviewer = new_test_user(
            self.env, login='clm_legal_reviewer',
            groups='base.group_user,clm.group_clm_legal',
        )
        self.assertFalse(reviewer.has_group('sign.group_sign_user'))
        as_reviewer = contract.with_user(reviewer)
        self.assertTrue(as_reviewer.final_contract_available)
        self.assertEqual(
            as_reviewer._get_final_contract_attachment('docx').mimetype, _DOCX_MIME
        )
        self.assertEqual(
            base64.b64decode(as_reviewer._get_signed_artifact().datas), _SIGNED_PDF
        )

    def test_user_without_clm_access_cannot_download(self):
        contract = self._make_contract(state='approved')
        self._make_version(contract, 'signature_draft')
        contract._get_final_contract_attachment('pdf')

        outsider = new_test_user(self.env, login='clm_no_access', groups='base.group_user')
        with self.assertRaises(AccessError):
            contract.with_user(outsider)._get_final_contract_attachment('pdf')

    def test_restricted_contract_is_not_downloadable_by_requester(self):
        contract = self._make_contract(state='approved')
        contract.write({'confidentiality_level': 'restricted'})
        self._make_version(contract, 'signature_draft')
        export = contract._get_final_contract_attachment('pdf')

        requester = new_test_user(
            self.env, login='clm_requester',
            groups='base.group_user,clm.group_clm_user',
        )
        # Not the business owner, not the legal owner, no department match:
        # the record rules keep the contract — and its export — out of reach.
        with self.assertRaises(AccessError):
            contract.with_user(requester).check_access('read')
        with self.assertRaises(AccessError):
            contract.with_user(requester)._get_final_contract_attachment('pdf')
        with self.assertRaises(AccessError):
            export.with_user(requester).check_access('read')
