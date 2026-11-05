# Part of NextContract. See LICENSE file for full copyright and licensing details.
"""Default Format (no template) Preview / DOCX / PDF consistency.

Format 1 must share one layout source: FinalContractBuilder →
FinalContractDocxWriter. Preview extracts HTML from that DOCX; PDF converts
the same DOCX via LibreOffice (QWeb only if soffice is missing).

Formats 2–4 must keep their prior routing (not forced onto the Default path).
"""

import base64
import io
import re
import zipfile

from odoo.tests.common import TransactionCase

from odoo.addons.clm.services.docx_to_pdf import find_soffice


def _docx_plain(raw_bytes):
    with zipfile.ZipFile(io.BytesIO(raw_bytes)) as archive:
        xml = archive.read('word/document.xml').decode('utf-8')
    return ' '.join(re.sub(r'<[^>]+>', ' ', xml).split())


def _docx_chrome_plain(raw_bytes):
    """Plain text from header/footer parts (running chrome, not body)."""
    chunks = []
    with zipfile.ZipFile(io.BytesIO(raw_bytes)) as archive:
        for name in archive.namelist():
            if '/header' in name or name.startswith('word/header'):
                chunks.append(archive.read(name).decode('utf-8'))
            elif '/footer' in name or name.startswith('word/footer'):
                chunks.append(archive.read(name).decode('utf-8'))
    joined = ' '.join(chunks)
    return ' '.join(re.sub(r'<[^>]+>', ' ', joined).split()), joined


def _pdf_plain(raw_bytes):
    import pypdfium2
    pdf = pypdfium2.PdfDocument(raw_bytes)
    text = ' '.join(page.get_textpage().get_text_range() for page in pdf)
    return ' '.join(text.split())


def _strip_html(html):
    return ' '.join(re.sub(r'<[^>]+>', ' ', html or '').split())


class TestDefaultFormatRenderingConsistency(TransactionCase):

    def setUp(self):
        super().setUp()
        self.contract_type = self.env.ref('clm.contract_type_services')
        self.partner = self.env['res.partner'].create({
            'name': 'Acme Services Ltd',
            'street': '100 Market Street',
            'city': 'London',
        })
        self.department = self.env['hr.department'].create({
            'name': 'Legal Ops',
        }) if 'hr.department' in self.env else False
        self.legal_owner = self.env.user

    def _make_default_contract(self, **values):
        vals = {
            'name': 'Employment Services Agreement — Acme',
            'contract_type_id': self.contract_type.id,
            'partner_id': self.partner.id,
            'effective_date': '2026-01-15',
            'expiry_date': '2027-01-14',
            'user_id': self.legal_owner.id,
        }
        if self.department:
            vals['department_id'] = self.department.id
        vals.update(values)
        return self.env['clm.contract'].create(vals)

    def _add_clauses(self, contract):
        Clause = self.env['clm.contract.clause']
        Clause.create([
            {
                'contract_id': contract.id,
                'sequence': 10,
                'section': 'Services',
                'title': 'Scope of Services',
                'content': (
                    '<p>Provider shall deliver consulting services to '
                    '<strong>{{partner_name}}</strong>.</p>'
                ),
                'source': 'custom',
            },
            {
                'contract_id': contract.id,
                'sequence': 20,
                'section': 'Termination',
                'title': 'Termination Notice',
                'content': (
                    '<p>Either party may terminate by giving '
                    '{{notice_period_days}} days written notice.</p>'
                ),
                'source': 'custom',
            },
            {
                'contract_id': contract.id,
                'sequence': 30,
                'section': 'Payment',
                'title': 'Fees',
                'content': '<p>Fees are due within thirty (30) days of invoice.</p>',
                'source': 'custom',
            },
        ])
        if 'notice_period_days' in contract._fields:
            contract.notice_period_days = 30

    def _new_version(self, contract, summary='Initial Default Format version'):
        wizard = self.env['clm.contract.version.wizard'].with_context(
            default_contract_id=contract.id,
        ).create({'change_summary': summary})
        action = wizard.action_create_version()
        return self.env['clm.contract.version'].browse(action['res_id'])

    def _assert_shared_markers(self, text, markers, forbidden=()):
        for marker in markers:
            self.assertIn(marker, text, 'Missing %r in output' % marker)
        for bad in forbidden:
            self.assertNotIn(bad, text, 'Unexpected %r in output' % bad)

    # ------------------------------------------------------------------
    # Format routing
    # ------------------------------------------------------------------

    def test_01_format_detection_no_template(self):
        contract = self._make_default_contract()
        self.assertFalse(contract.template_id)
        self.assertEqual(contract._clm_document_generation_format(), 'default_no_template')
        gen = contract._clm_get_document_generation_context()
        self.assertTrue(gen['uses_default_docx_layout'])
        self.assertFalse(gen['uses_base_docx'])
        self.assertTrue(contract._clm_uses_default_docx_layout())

    def test_02_other_formats_not_default_layout(self):
        # Format 3 — Template Body
        body_tmpl = self.env['clm.template'].create({
            'name': 'Body Template',
            'contract_type_id': self.contract_type.id,
            'content': '<h1>BODY ONLY</h1>',
        })
        body_tmpl.action_approve()
        body_tmpl.action_publish()
        body_contract = self.env['clm.contract'].create({
            'name': 'Body Contract',
            'contract_type_id': self.contract_type.id,
            'partner_id': self.partner.id,
            'template_id': body_tmpl.id,
        })
        self.assertEqual(body_contract._clm_document_generation_format(), 'template_body')
        self.assertFalse(body_contract._clm_uses_default_docx_layout())
        # Classic layout markers — not the professional Default Format chrome.
        version = body_contract._generate_draft(renderer='docx').result_version_id
        classic_text = _docx_plain(version._clm_generate_document('docx').raw)
        self.assertIn('BODY ONLY', classic_text)
        self.assertNotIn('CONTRACT INFORMATION', classic_text)
        self.assertNotIn('NEXTCONTRACT  |  CONTRACT DOCUMENT', classic_text)

        # Format 2 — template, no body, no Base DOCX
        bare = self.env['clm.template'].create({
            'name': 'Bare Template',
            'contract_type_id': self.contract_type.id,
        })
        bare.action_approve()
        bare.action_publish()
        bare_contract = self.env['clm.contract'].create({
            'name': 'Bare Contract',
            'contract_type_id': self.contract_type.id,
            'partner_id': self.partner.id,
            'template_id': bare.id,
        })
        self.assertEqual(bare_contract._clm_document_generation_format(), 'default_template')
        self.assertFalse(bare_contract._clm_uses_default_docx_layout())

    # ------------------------------------------------------------------
    # Canonical content shared by Preview / DOCX / PDF
    # ------------------------------------------------------------------

    def test_03_preview_docx_share_canonical_content(self):
        contract = self._make_default_contract()
        self._add_clauses(contract)
        version = self._new_version(contract)

        markers = (
            'Employment Services Agreement — Acme',
            'Acme Services Ltd',
            'Scope of Services',
            'Termination Notice',
            'Fees',
            'consulting services',
            '30 days written notice',
            'thirty (30) days of invoice',
            'CONTRACT INFORMATION',
            'PARTIES',
            'CONTRACT PROVIDER',
            'COUNTERPARTY',
            'CONTRACT TERMS AND CONDITIONS',
            'SIGNATURES',
            'FOR AND ON BEHALF OF',
            # Signature blank for Odoo Sign; Name / Date printed (Base DOCX parity).
            'Signature: ______________________________',
            'Name: %s' % self.legal_owner.name,
            'Name: Acme Services Ltd',
        )

        docx_raw = version._clm_generate_document('docx').raw
        docx_text = _docx_plain(docx_raw)
        self._assert_shared_markers(docx_text, markers)

        preview = version._clm_render_version_preview()
        preview_text = _strip_html(preview)
        self._assert_shared_markers(preview_text, markers)
        # Version preview must be a native PDF iframe of the Download PDF.
        self.assertIn('o_clm_pdf_preview', preview)
        self.assertIn('o_clm_pdf_iframe', preview)
        self.assertIn('/web/content/', preview)
        self.assertIn('CONTRACT TERMS AND CONDITIONS', preview_text)
        self.assertIn('final PDF document', preview)

        contract.invalidate_recordset(['contract_preview_html'])
        working = contract.contract_preview_html or ''
        working_text = _strip_html(working)
        self._assert_shared_markers(working_text, markers)
        self.assertIn('o_clm_pdf_preview', working)
        self.assertIn('o_clm_pdf_iframe', working)
        self.assertIn('final PDF document', working)

    def test_04_pdf_matches_docx_when_libreoffice_available(self):
        if not find_soffice():
            self.skipTest('LibreOffice not available')
        contract = self._make_default_contract()
        self._add_clauses(contract)
        version = self._new_version(contract)

        markers = (
            'Employment Services Agreement — Acme',
            'Acme Services Ltd',
            'Scope of Services',
            'Termination Notice',
            '30 days written notice',
            'CONTRACT TERMS AND CONDITIONS',
            'CONTRACT INFORMATION',
        )
        docx_text = _docx_plain(version._clm_generate_document('docx').raw)
        pdf_att = version._clm_generate_document('pdf')
        pdf_bytes = base64.b64decode(pdf_att.datas)
        self.assertTrue(pdf_bytes.startswith(b'%PDF'))
        pdf_text = _pdf_plain(pdf_bytes)

        self._assert_shared_markers(docx_text, markers)
        self._assert_shared_markers(pdf_text, markers)

        # Same canonical DOCX is used for PDF conversion.
        direct_pdf = contract._render_contract_pdf_from_default_docx(
            version=version, kind='draft',
        )
        self.assertTrue(direct_pdf.startswith(b'%PDF'))
        self._assert_shared_markers(_pdf_plain(direct_pdf), markers)

    def test_05_section_and_clause_order(self):
        contract = self._make_default_contract()
        self._add_clauses(contract)
        version = self._new_version(contract)

        docx_text = _docx_plain(version._clm_generate_document('docx').raw)
        preview_text = _strip_html(version._clm_render_version_preview())

        for blob in (docx_text, preview_text):
            i_scope = blob.find('Scope of Services')
            i_term = blob.find('Termination Notice')
            i_fees = blob.find('Fees')
            self.assertGreater(i_scope, -1)
            self.assertGreater(i_term, i_scope)
            self.assertGreater(i_fees, i_term)

    def test_06_dynamic_fields_and_optional_empty(self):
        contract = self._make_default_contract(expiry_date=False)
        self._add_clauses(contract)
        version = self._new_version(contract)

        markers = (
            'Employment Services Agreement — Acme',
            'Acme Services Ltd',
            '15 Jan 2026',  # locale-formatted effective date from FinalContractBuilder
        )
        docx_text = _docx_plain(version._clm_generate_document('docx').raw)
        preview_text = _strip_html(version._clm_render_version_preview())
        for blob in (docx_text, preview_text):
            self._assert_shared_markers(blob, markers)
            # Missing expiry must not invent a placeholder that only one path shows.
            self.assertNotIn('{{expiry_date}}', blob)

    def test_07_render_document_routes_default_pdf_through_docx(self):
        contract = self._make_default_contract()
        self._add_clauses(contract)
        version = self._new_version(contract)

        docx_data, docx_mime, _ = contract._render_contract_document(
            'docx', version=version, kind='draft',
        )
        self.assertIn(
            'wordprocessingml',
            docx_mime,
        )
        self.assertIn('Scope of Services', _docx_plain(docx_data))

        if find_soffice():
            pdf_data, pdf_mime, _ = contract._render_contract_document(
                'pdf', version=version, kind='draft',
            )
            self.assertEqual(pdf_mime, 'application/pdf')
            self.assertTrue(pdf_data.startswith(b'%PDF'))
            self.assertIn('Scope of Services', _pdf_plain(pdf_data))

    def test_08_signed_final_keeps_default_signature_cards(self):
        """View Final = Default Format cards with Name/Date; Signature blank."""
        from odoo.addons.clm.services.final_contract_docx import (
            FinalContractDocxWriter,
        )

        contract = self._make_default_contract()
        self._add_clauses(contract)
        content = contract._build_final_contract_content(kind='approved')
        content = dict(content)
        content['kind'] = 'signed'
        content['signature_evidence'] = 'esign'
        content['signature_layout'] = 'professional'
        content['signing_date'] = '08 Oct 2026'
        company_name = contract.company_id.partner_id.name
        # Align party names with e-sign rows so evidence Name/Date apply.
        content['parties'] = [
            {
                'role': 'Company',
                'name': company_name,
                'representative': self.legal_owner.name,
            },
            {
                'role': 'Counterparty',
                'name': 'Acme Services Ltd',
                'representative': 'Acme Services Ltd',
            },
        ]
        content['signatures'] = [
            {
                'party': company_name,
                'name': self.legal_owner.name,
                'role': 'Signer 1',
                'signed_on': '05 Oct 2026',
                'method': 'Electronic signature (Odoo Sign)',
                'image': {'mimetype': 'image/png', 'base64': 'AAAA'},
            },
            {
                'party': 'Acme Services Ltd',
                'name': 'Dan George',
                'role': 'Customer',
                'signed_on': '05 Oct 2026',
                'method': 'Electronic signature (Odoo Sign)',
            },
        ]

        raw = FinalContractDocxWriter(layout='professional').render(content)
        text = _docx_plain(raw)
        self.assertIn('FOR AND ON BEHALF OF', text)
        self.assertIn(
            'The parties shall execute this agreement as set out below.',
            text,
        )
        self.assertIn('Signature: ______________________________', text)
        self.assertIn('Name: %s' % self.legal_owner.name, text)
        self.assertIn('Name: Dan George', text)
        self.assertIn('Date: 05 Oct 2026', text)
        # Must not show the evidence-table chrome on professional cards.
        self.assertNotIn('Electronic signature (Odoo Sign)', text)
        self.assertNotIn('Signed On', text)
        self.assertNotIn('The parties executed this agreement as recorded below.', text)

    def test_09_signature_section_identical_across_kinds(self):
        """Draft / approved / signed Default Format share one signature block."""
        contract = self._make_default_contract()
        self._add_clauses(contract)
        version = self._new_version(contract)
        content0 = contract._build_final_contract_content(
            version=version, kind='draft',
        )
        signing_date = content0.get('signing_date') or ''

        markers = (
            'SIGNATURES',
            'The parties shall execute this agreement as set out below.',
            'FOR AND ON BEHALF OF',
            'Signature: ______________________________',
            'Name: %s' % self.legal_owner.name,
            'Name: Acme Services Ltd',
            'Date: %s' % signing_date,
        )
        forbidden = (
            'Electronic signature (Odoo Sign)',
            'Signed On',
            'The parties executed this agreement as recorded below.',
            'For and on behalf of',  # classic QWeb wording
        )

        for kind in ('draft', 'approved', 'signed'):
            content = contract._build_final_contract_content(
                version=version, kind=kind,
            )
            self.assertEqual(content.get('signature_layout'), 'professional')
            docx_text = _docx_plain(
                contract._render_contract_docx(version=version, kind=kind)
            )
            self._assert_shared_markers(docx_text, markers, forbidden=forbidden)

            # QWeb fallback (no LibreOffice) must use the same professional cards.
            qweb_html = contract.env['ir.qweb']._render(
                'clm.report_clm_contract_document_article',
                {'content': content},
            )
            qweb_text = _strip_html(str(qweb_html))
            self._assert_shared_markers(qweb_text, markers, forbidden=forbidden)

    def test_10_header_footer_dual_chrome(self):
        """Running header/footer: brand + ref / ref + Page X of Y."""
        contract = self._make_default_contract(contract_ref='CNT-2026-HF-001')
        self._add_clauses(contract)
        version = self._new_version(contract)
        raw = version._clm_generate_document('docx').raw
        chrome_text, chrome_xml = _docx_chrome_plain(raw)

        self.assertIn('NEXTCONTRACT', chrome_text)
        self.assertIn('CNT-2026-HF-001', chrome_text)
        self.assertIn('Page', chrome_text)
        self.assertIn('of', chrome_text)
        # Word field instructions live in footer XML.
        self.assertIn('PAGE', chrome_xml)
        self.assertIn('NUMPAGES', chrome_xml)
        # Brand should not remain the old right-aligned-only chrome alone.
        self.assertGreaterEqual(chrome_text.count('CNT-2026-HF-001'), 1)
