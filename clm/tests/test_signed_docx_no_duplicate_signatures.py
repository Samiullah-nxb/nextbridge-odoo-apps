# Part of NextContract. See LICENSE file for full copyright and licensing details.
"""Signed Format 4 DOCX must not get a duplicate system Signatures section.

When a Base DOCX (or uploaded Word file) already contains an Execution and
Signatures layout, ``append_signatures`` must leave those bytes alone while
CLM still keeps signatory / method metadata on the contract.
"""

import base64
import io
import re
import zipfile

from odoo.tests.common import TransactionCase

from odoo.addons.clm.services.final_contract_docx import FinalContractDocxWriter
from odoo.addons.clm.services.docx_to_pdf import find_soffice


def _docx_bytes(*paragraphs):
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


def _docx_plain(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        xml = archive.read('word/document.xml').decode('utf-8')
    return ' '.join(re.sub(r'<[^>]+>', ' ', xml).split())


_EVIDENCE_INTRO = 'The parties executed this agreement as recorded below.'
_EVIDENCE_MARKERS = (
    _EVIDENCE_INTRO,
    'Signed On',
    'Manual signature',
)

_EXECUTION_PARAS = (
    '5. Execution and Signatures',
    'IN WITNESS WHEREOF, the Parties have caused this Agreement to be executed.',
    'FOR Contract Provider Ltd.',
    'Name: Mitchell Admin',
    'Title: Chief Technical Officer',
    'Signature: Provider ltd',
    'Date: 10/09/2026',
    'FOR Contract Recipient Ltd',
    'Name: Dan George',
    'Title: CEO / Legal Manager',
    'Signature: ______________________',
    'Date: 10/09/2026',
    'END OF AGREEMENT',
)


class TestSignedDocxNoDuplicateSignatures(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids |= cls.env.ref('clm.group_clm_legal_admin')
        cls.contract_type = cls.env.ref('clm.contract_type_nda')
        cls.partner = cls.env['res.partner'].create({'name': 'Contract Recipient Ltd'})
        cls.clause = cls.env['clm.clause'].create({
            'name': 'Confidentiality',
            'category': 'confidentiality',
            'standard_wording': '<p>Keep secrets for {{partner_name}}.</p>',
            'risk_level': 'low',
        })
        cls.clause.action_submit_review()
        cls.clause.action_approve()
        cls.clause.action_publish()

    def _make_template(self, *paragraphs):
        template = self.env['clm.template'].create({
            'name': 'Signed Dup Template',
            'contract_type_id': self.contract_type.id,
            'base_docx_upload': base64.b64encode(_docx_bytes(*paragraphs)),
            'base_docx_upload_filename': 'signed_dup_base.docx',
            'state': 'draft',
        })
        self.env['clm.template.clause.line'].create({
            'template_id': template.id,
            'clause_id': self.clause.id,
            'sequence': 10,
            'section': 'Confidentiality',
            'required': True,
        })
        template.action_approve()
        template.action_publish()
        return template

    def _manual_content(self, signatory='Waqar'):
        return {
            'kind': 'signed',
            'signature_evidence': 'manual',
            'signatures': [{
                'party': 'Contract Provider Ltd.',
                'name': signatory,
                'role': 'Authorised signatory',
                'signed_on': '09 Oct 2026',
                'method': 'Manual signature',
            }],
            'parties': [],
        }

    def _assert_no_evidence_table(self, text):
        for marker in _EVIDENCE_MARKERS:
            self.assertNotIn(marker, text)

    def test_01_existing_execution_section_skips_system_table(self):
        """Upload-style DOCX with Execution and Signatures must not get a 2nd block."""
        raw = _docx_bytes(*_EXECUTION_PARAS)
        out = FinalContractDocxWriter().append_signatures(raw, self._manual_content())
        text = _docx_plain(out)
        self.assertIn('5. Execution and Signatures', text)
        self.assertIn('Mitchell Admin', text)
        self.assertIn('END OF AGREEMENT', text)
        self._assert_no_evidence_table(text)
        # Bytes unchanged when skipping (authoritative source preserved).
        self.assertEqual(out, raw)

    def test_02_manual_metadata_still_available_after_skip(self):
        """Skipping the append must not drop structured signature metadata."""
        content = self._manual_content(signatory='Jordan Blake')
        raw = _docx_bytes(
            '5. Execution and Signatures',
            'IN WITNESS WHEREOF.',
            'Signature: ______________________',
            'Name: ______________________',
            'Date: ______________',
        )
        out = FinalContractDocxWriter().append_signatures(raw, content)
        self.assertEqual(out, raw)
        self.assertEqual(content['signature_evidence'], 'manual')
        self.assertEqual(content['signatures'][0]['name'], 'Jordan Blake')
        self.assertEqual(content['signatures'][0]['method'], 'Manual signature')
        self.assertEqual(content['signatures'][0]['role'], 'Authorised signatory')
        self.assertEqual(content['signatures'][0]['signed_on'], '09 Oct 2026')

    def test_03_doc_without_signature_section_still_gets_system_block(self):
        raw = _docx_bytes(
            'Agreement body only.',
            'Party {{partner_name}} agrees to the terms.',
        )
        out = FinalContractDocxWriter().append_signatures(raw, self._manual_content())
        text = _docx_plain(out)
        self.assertIn('Signatures', text)
        self.assertIn(_EVIDENCE_INTRO, text)
        self.assertIn('Signed On', text)
        self.assertIn('Manual signature', text)
        self.assertIn('Waqar', text)

    def test_04_repeated_append_does_not_accumulate(self):
        raw = _docx_bytes('Body clause without any signature layout.')
        writer = FinalContractDocxWriter()
        content = self._manual_content()
        once = writer.append_signatures(raw, content)
        twice = writer.append_signatures(once, content)
        thrice = writer.append_signatures(twice, content)
        self.assertEqual(_docx_plain(once).count(_EVIDENCE_INTRO), 1)
        self.assertEqual(_docx_plain(twice).count(_EVIDENCE_INTRO), 1)
        self.assertEqual(_docx_plain(thrice).count(_EVIDENCE_INTRO), 1)
        self.assertEqual(twice, once)
        self.assertEqual(thrice, once)

    def test_05_format4_signed_docx_and_pdf_keep_existing_block_only(self):
        template = self._make_template(
            'BASE MARKER BEFORE SIGNATURES',
            '5. Execution and Signatures',
            'IN WITNESS WHEREOF, the Parties execute this Agreement.',
            'FOR Contract Provider Ltd.',
            'Name: Mitchell Admin',
            'Signature: ______________________',
            'Date: 10/09/2026',
            'END OF AGREEMENT',
        )
        contract = self.env['clm.contract'].create({
            'name': 'Format4 No Dup',
            'contract_type_id': self.contract_type.id,
            'partner_id': self.partner.id,
            'template_id': template.id,
            'state': 'approved',
        })
        version = self.env['clm.contract.version'].create({
            'contract_id': contract.id,
            'version_type': 'signature_draft',
            'status': 'active',
            'change_summary': 'Signature draft',
        })
        # Format 4 fill without signed append, then apply signed evidence path.
        filled = contract._render_contract_docx(version=version, kind='approved')
        docx_raw = FinalContractDocxWriter().append_signatures(
            filled, self._manual_content(),
        )
        docx_text = _docx_plain(docx_raw)
        self.assertIn('5. Execution and Signatures', docx_text)
        self.assertIn('END OF AGREEMENT', docx_text)
        self._assert_no_evidence_table(docx_text)
        self.assertEqual(docx_raw, filled)

        # Contract helper must also skip when kind=signed and Base DOCX owns layout.
        signed_content = dict(contract._build_final_contract_content(
            version=version, kind='approved',
        ))
        signed_content.update(self._manual_content())
        orig_build = type(contract)._build_final_contract_content

        def _signed_build(self_contract, version=None, kind='approved', snapshot=None):
            return signed_content

        type(contract)._build_final_contract_content = _signed_build
        try:
            via_helper = contract._clm_docx_with_signatures(
                filled, version=version, kind='signed',
            )
        finally:
            type(contract)._build_final_contract_content = orig_build
        self.assertEqual(via_helper, filled)

        if find_soffice():
            from odoo.addons.clm.services.docx_to_pdf import docx_bytes_to_pdf
            pdf_raw = docx_bytes_to_pdf(docx_raw)
            import pypdfium2
            pdf = pypdfium2.PdfDocument(pdf_raw)
            pdf_text = ' '.join(
                page.get_textpage().get_text_range() for page in pdf
            )
            self.assertIn('Execution and Signatures', pdf_text)
            self.assertNotIn(_EVIDENCE_INTRO, pdf_text)

    def test_06_default_format_still_renders_professional_signatures(self):
        """Format 1 (no template) is unaffected by the Format 4 append guard."""
        contract = self.env['clm.contract'].create({
            'name': 'Default Format Signatures',
            'contract_type_id': self.contract_type.id,
            'partner_id': self.partner.id,
            'state': 'draft',
        })
        version = self.env['clm.contract.version'].create({
            'contract_id': contract.id,
            'version_type': 'internal_draft',
            'status': 'active',
            'change_summary': 'v1',
        })
        raw = contract._render_contract_docx(version=version, kind='approved')
        text = _docx_plain(raw)
        self.assertIn('FOR AND ON BEHALF OF', text)
        self.assertNotIn(_EVIDENCE_INTRO, text)

    def test_07_version_generate_docx_does_not_mutate_base_on_repeat(self):
        template = self._make_template(
            '5. Execution and Signatures',
            'IN WITNESS WHEREOF.',
            'Signature: ______________________',
            'Name: ______________________',
            'Date: ______________',
        )
        contract = self.env['clm.contract'].create({
            'name': 'Repeat Preview Contract',
            'contract_type_id': self.contract_type.id,
            'partner_id': self.partner.id,
            'template_id': template.id,
            'state': 'approved',
        })
        version = self.env['clm.contract.version'].create({
            'contract_id': contract.id,
            'version_type': 'signature_draft',
            'status': 'active',
            'change_summary': 'Signature draft',
        })
        filled = contract._render_contract_docx(version=version, kind='approved')
        writer = FinalContractDocxWriter()
        content = self._manual_content()
        first = writer.append_signatures(filled, content)
        second = writer.append_signatures(first, content)
        first_text = _docx_plain(first)
        second_text = _docx_plain(second)
        self.assertEqual(first_text.count('Execution and Signatures'), 1)
        self.assertEqual(second_text.count('Execution and Signatures'), 1)
        self.assertEqual(first_text.count(_EVIDENCE_INTRO), 0)
        self.assertEqual(second_text.count(_EVIDENCE_INTRO), 0)
        self.assertEqual(first, filled)
        self.assertEqual(second, first)

    def test_08_four_generation_formats_still_resolve(self):
        """Guard must not break Format 1–4 document-generation routing."""
        # Format 1 — no template.
        c1 = self.env['clm.contract'].create({
            'name': 'Fmt1',
            'contract_type_id': self.contract_type.id,
            'partner_id': self.partner.id,
        })
        self.assertEqual(c1._clm_document_generation_format(), 'default_no_template')

        # Format 2 — template without body / base DOCX (fallback).
        t2 = self.env['clm.template'].create({
            'name': 'Fmt2 Template',
            'contract_type_id': self.contract_type.id,
            'state': 'draft',
        })
        self.env['clm.template.clause.line'].create({
            'template_id': t2.id,
            'clause_id': self.clause.id,
            'sequence': 10,
            'section': 'Confidentiality',
            'required': True,
        })
        t2.action_approve()
        t2.action_publish()
        c2 = self.env['clm.contract'].create({
            'name': 'Fmt2',
            'contract_type_id': self.contract_type.id,
            'partner_id': self.partner.id,
            'template_id': t2.id,
        })
        self.assertEqual(c2._clm_document_generation_format(), 'default_template')

        # Format 3 — template with body.
        t3 = self.env['clm.template'].create({
            'name': 'Fmt3 Template',
            'contract_type_id': self.contract_type.id,
            'content': '<p>Template body for {{partner_name}}.</p>',
            'state': 'draft',
        })
        self.env['clm.template.clause.line'].create({
            'template_id': t3.id,
            'clause_id': self.clause.id,
            'sequence': 10,
            'section': 'Confidentiality',
            'required': True,
        })
        t3.action_approve()
        t3.action_publish()
        c3 = self.env['clm.contract'].create({
            'name': 'Fmt3',
            'contract_type_id': self.contract_type.id,
            'partner_id': self.partner.id,
            'template_id': t3.id,
        })
        self.assertEqual(c3._clm_document_generation_format(), 'template_body')

        # Format 4 — template with Base DOCX.
        t4 = self._make_template('Format 4 body only.')
        c4 = self.env['clm.contract'].create({
            'name': 'Fmt4',
            'contract_type_id': self.contract_type.id,
            'partner_id': self.partner.id,
            'template_id': t4.id,
        })
        self.assertEqual(c4._clm_document_generation_format(), 'base_docx')
        self.assertTrue(c4._clm_get_document_generation_context()['uses_base_docx'])
