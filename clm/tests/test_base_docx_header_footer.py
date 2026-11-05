# Part of NextContract. See LICENSE file for full copyright and licensing details.
"""Base DOCX header/footer is the source of truth (Format 4).

Default Format dual-chrome (NEXTCONTRACT / Page X of Y) must never be injected
into Format 4 output. Preserve Base DOCX header/footer content (or absence).
"""

import base64
import io
import re
import zipfile

from odoo.tests.common import TransactionCase

from odoo.addons.clm.services.docx_to_pdf import find_soffice


def _docx_bytes(paragraphs, header=None, footer=None):
    """Build a Base DOCX. Only touch header/footer when content is supplied."""
    from docx import Document

    document = Document()
    if document.paragraphs:
        p = document.paragraphs[0]._element
        p.getparent().remove(p)
    for text in paragraphs:
        document.add_paragraph(text)
    section = document.sections[0]
    if header is not None:
        section.header.paragraphs[0].text = header
    if footer is not None:
        section.footer.paragraphs[0].text = footer
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


def _chrome_parts(raw_bytes):
    """Return (header_names, footer_names, plain_chrome_text, chrome_xml)."""
    headers, footers, chunks = [], [], []
    with zipfile.ZipFile(io.BytesIO(raw_bytes)) as archive:
        for name in archive.namelist():
            if '/header' in name or name.startswith('word/header'):
                headers.append(name)
                chunks.append(archive.read(name).decode('utf-8'))
            elif '/footer' in name or name.startswith('word/footer'):
                footers.append(name)
                chunks.append(archive.read(name).decode('utf-8'))
    xml = ' '.join(chunks)
    plain = ' '.join(re.sub(r'<[^>]+>', ' ', xml).split())
    return headers, footers, plain, xml


def _chrome_has_content(raw_bytes):
    """True when header/footer XML has visible text or drawings."""
    _h, _f, plain, xml = _chrome_parts(raw_bytes)
    if plain.strip():
        return True
    return 'w:drawing' in xml or 'v:imagedata' in xml


def _pdf_plain(raw_bytes):
    import pypdfium2
    pdf = pypdfium2.PdfDocument(raw_bytes)
    text = ' '.join(page.get_textpage().get_text_range() for page in pdf)
    return ' '.join(text.split())


class TestBaseDocxHeaderFooter(TransactionCase):

    def setUp(self):
        super().setUp()
        self.contract_type = self.env.ref('clm.contract_type_nda')
        self.clause = self.env['clm.clause'].create({
            'name': 'HF Confidentiality',
            'category': 'confidentiality',
            'standard_wording': '<p>Keep {{partner_name}} secret.</p>',
            'risk_level': 'low',
        })
        self.clause.action_submit_review()
        self.clause.action_approve()
        self.clause.action_publish()
        self.partner = self.env['res.partner'].create({'name': 'HF Partner Ltd'})

    def _publish_template(self, raw, filename='hf_base.docx'):
        template = self.env['clm.template'].create({
            'name': 'HF Base DOCX Template %s' % filename,
            'contract_type_id': self.contract_type.id,
            'content': '<h1>TEMPLATE BODY MUST NOT APPEAR</h1>',
            'base_docx_upload': base64.b64encode(raw),
            'base_docx_upload_filename': filename,
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

    def _draft_docx(self, template, name='HF Contract'):
        contract = self.env['clm.contract'].create({
            'name': name,
            'contract_type_id': self.contract_type.id,
            'partner_id': self.partner.id,
            'template_id': template.id,
        })
        self.assertTrue(contract._clm_get_document_generation_context()['uses_base_docx'])
        version = contract._generate_draft(renderer='docx').result_version_id
        raw = version._clm_generate_document('docx').with_context(bin_size=False).raw
        return contract, version, raw

    def test_01_no_header_no_footer_stays_empty(self):
        """Base DOCX without H/F must not gain Default Format chrome."""
        base = _docx_bytes([
            'NO HF MARKER',
            'Party {{partner_name}}.',
        ])
        self.assertFalse(_chrome_has_content(base))
        template = self._publish_template(base, 'no_hf.docx')
        _contract, version, raw = self._draft_docx(template, 'No HF Contract')

        _h, _f, chrome, chrome_xml = _chrome_parts(raw)
        self.assertNotIn('NEXTCONTRACT', chrome)
        self.assertNotIn('NEXTCONTRACT', chrome_xml)
        self.assertNotIn('NUMPAGES', chrome_xml)
        self.assertFalse(
            _chrome_has_content(raw),
            'Generated DOCX must not invent header/footer content: %r' % chrome,
        )
        self.assertIn('NO HF MARKER', raw and zipfile.ZipFile(io.BytesIO(raw)).read(
            'word/document.xml'
        ).decode('utf-8'))

        if find_soffice():
            pdf = version._clm_generate_document('pdf').with_context(bin_size=False).raw
            pdf_text = _pdf_plain(pdf)
            self.assertIn('NO HF MARKER', pdf_text)
            self.assertNotIn('NEXTCONTRACT', pdf_text)

    def test_02_custom_header_and_footer_preserved(self):
        """Custom Base DOCX H/F survive Generate DOCX / PDF; no extra chrome."""
        base = _docx_bytes(
            ['CUSTOM HF BODY', 'Hello {{partner_name}}.'],
            header='CUSTOM HEADER {{partner_name}}',
            footer='CUSTOM FOOTER {{effective_date}}',
        )
        self.assertTrue(_chrome_has_content(base))
        template = self._publish_template(base, 'with_hf.docx')
        _contract, version, raw = self._draft_docx(template, 'With HF Contract')

        _h, _f, chrome, chrome_xml = _chrome_parts(raw)
        self.assertIn('CUSTOM HEADER', chrome)
        self.assertIn('HF Partner Ltd', chrome)
        self.assertIn('CUSTOM FOOTER', chrome)
        self.assertNotIn('NEXTCONTRACT', chrome)
        self.assertNotIn('NEXTCONTRACT', chrome_xml)
        self.assertNotIn('NUMPAGES', chrome_xml)

        if find_soffice():
            pdf = version._clm_generate_document('pdf').with_context(bin_size=False).raw
            pdf_text = _pdf_plain(pdf)
            self.assertIn('CUSTOM HEADER', pdf_text)
            self.assertIn('CUSTOM FOOTER', pdf_text)
            self.assertIn('CUSTOM HF BODY', pdf_text)
            self.assertNotIn('NEXTCONTRACT', pdf_text)

    def test_03_header_only_no_footer_invented(self):
        base = _docx_bytes(
            ['HEADER ONLY BODY'],
            header='ONLY HEADER MARKER',
        )
        template = self._publish_template(base, 'header_only.docx')
        _c, _v, raw = self._draft_docx(template, 'Header Only')
        _h, _footers, chrome, chrome_xml = _chrome_parts(raw)
        self.assertIn('ONLY HEADER MARKER', chrome)
        self.assertNotIn('NEXTCONTRACT', chrome)
        # Never invent Default Format page chrome in the footer band.
        self.assertNotIn('NUMPAGES', chrome_xml)
        self.assertNotIn('NEXTCONTRACT', chrome_xml)

    def test_04_footer_only_no_header_invented(self):
        base = _docx_bytes(
            ['FOOTER ONLY BODY'],
            footer='ONLY FOOTER MARKER',
        )
        template = self._publish_template(base, 'footer_only.docx')
        _c, _v, raw = self._draft_docx(template, 'Footer Only')
        _headers, _f, chrome, chrome_xml = _chrome_parts(raw)
        self.assertIn('ONLY FOOTER MARKER', chrome)
        self.assertNotIn('NEXTCONTRACT', chrome)
        self.assertNotIn('NUMPAGES', chrome_xml)

    def test_05_default_format_still_has_dual_chrome(self):
        """Format 1 (no template) must keep NEXTCONTRACT running chrome."""
        contract = self.env['clm.contract'].create({
            'name': 'Default Format HF Guard',
            'contract_type_id': self.contract_type.id,
            'partner_id': self.partner.id,
            'contract_ref': 'CNT-HF-DEFAULT-001',
        })
        gen = contract._clm_get_document_generation_context()
        self.assertFalse(gen['uses_base_docx'])
        self.assertTrue(gen.get('uses_default_docx_layout'))
        raw = contract._render_contract_docx(kind='draft')
        _h, _f, chrome, chrome_xml = _chrome_parts(raw)
        self.assertIn('NEXTCONTRACT', chrome)
        self.assertIn('CNT-HF-DEFAULT-001', chrome)
        self.assertIn('PAGE', chrome_xml)
        self.assertIn('NUMPAGES', chrome_xml)
