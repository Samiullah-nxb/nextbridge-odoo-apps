# Part of NextContract. See LICENSE file for full copyright and licensing details.
"""Base DOCX formatting fidelity: spacing, blanks, and variable fill.

The Base DOCX is the source of truth. docxtpl fill + post-processing must keep
paragraph properties (w:pPr), intentional blank paragraphs, and run formatting.
"""

import base64
import io
import zipfile

from lxml import etree
from odoo.tests.common import TransactionCase

from odoo.addons.clm.services.docx_renderer import DocxRenderer
from odoo.addons.clm.services import docx_tag_editor as docx_editor


W_NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'


def _body_paragraphs(raw_bytes):
    root = etree.fromstring(zipfile.ZipFile(io.BytesIO(raw_bytes)).read('word/document.xml'))
    body = root.find('{%s}body' % W_NS)
    return [child for child in body if child.tag == '{%s}p' % W_NS]


def _para_text(p_el):
    return ''.join(t.text or '' for t in p_el.iter('{%s}t' % W_NS))


def _para_spacing(p_el):
    pPr = p_el.find('{%s}pPr' % W_NS)
    if pPr is None:
        return {}
    sp = pPr.find('{%s}spacing' % W_NS)
    if sp is None:
        return {}
    out = {}
    before = sp.get('{%s}before' % W_NS)
    after = sp.get('{%s}after' % W_NS)
    line = sp.get('{%s}line' % W_NS)
    if before is not None:
        out['before'] = before
    if after is not None:
        out['after'] = after
    if line is not None:
        out['line'] = line
    return out


def _para_align(p_el):
    pPr = p_el.find('{%s}pPr' % W_NS)
    if pPr is None:
        return None
    jc = pPr.find('{%s}jc' % W_NS)
    if jc is None:
        return None
    return jc.get('{%s}val' % W_NS)


def _blank_indices(raw_bytes):
    indices = []
    for i, p_el in enumerate(_body_paragraphs(raw_bytes)):
        if not _para_text(p_el).strip():
            indices.append(i)
    return indices


def _fidelity_base_docx():
    """Build a Base DOCX covering the acceptance spacing cases."""
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
    from docx.shared import Pt, Twips

    document = Document()
    # Drop the default empty first paragraph if present so indices are stable.
    if document.paragraphs:
        p = document.paragraphs[0]._element
        p.getparent().remove(p)
    p0 = document.add_paragraph()
    run0 = p0.add_run('FIDELITY TITLE')
    run0.bold = True
    p0.paragraph_format.space_after = Pt(12)

    p_name = document.add_paragraph('{{ contract_name }}')
    p_name.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    p_name.paragraph_format.space_after = Pt(6)

    p_ref = document.add_paragraph('Agreement No.: {{ contract_ref }}')
    p_ref.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    p_ref.paragraph_format.space_after = Pt(6)

    # Intentional blank spacer (the layout gap users see after Agreement No.).
    blank = document.add_paragraph('')
    blank.paragraph_format.space_after = Pt(6)

    intro = document.add_paragraph(
        'This Agreement is entered into as of {{ effective_date }} '
        'by {{ partner_name }}.'
    )
    intro.paragraph_format.space_before = Pt(0)
    intro.paragraph_format.space_after = Pt(10)
    intro.paragraph_format.line_spacing_rule = WD_LINE_SPACING.ONE_POINT_FIVE

    blank2 = document.add_paragraph('')
    blank2.paragraph_format.space_after = Pt(6)

    heading = document.add_paragraph()
    heading.add_run('1. Term').bold = True
    heading.paragraph_format.space_before = Pt(14)
    heading.paragraph_format.space_after = Pt(4)

    body = document.add_paragraph('Normal body with {{ company_name }}.')
    body.paragraph_format.space_after = Pt(8)
    body.paragraph_format.left_indent = Twips(144)  # 0.1"

    # Clause collection loop (Jinja control tags become empty after fill).
    document.add_paragraph('{% for section in sections %}')
    document.add_paragraph('{% for clause in section.clauses %}')
    document.add_paragraph('{{ clause.title }}')
    document.add_paragraph('{{ clause.category }}')
    document.add_paragraph('{{ clause.content }}')
    document.add_paragraph('{% endfor %}')
    document.add_paragraph('{% endfor %}')

    trailing = document.add_paragraph('END MARKER')
    trailing.paragraph_format.space_before = Pt(12)

    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


class TestBaseDocxFormattingFidelity(TransactionCase):

    def setUp(self):
        super().setUp()
        self.contract_type = self.env.ref('clm.contract_type_nda')
        self.clause = self.env['clm.clause'].create({
            'name': 'Confidentiality',
            'category': 'confidentiality',
            'standard_wording': '<p>Keep secrets of {{partner_name}}.</p>',
            'risk_level': 'medium',
        })
        self.clause.action_submit_review()
        self.clause.action_approve()
        self.clause.action_publish()

        raw = _fidelity_base_docx()
        self.template = self.env['clm.template'].create({
            'name': 'Fidelity Base DOCX Template',
            'contract_type_id': self.contract_type.id,
            'base_docx_upload': base64.b64encode(raw),
            'base_docx_upload_filename': 'fidelity.docx',
            'state': 'draft',
        })
        self.env['clm.template.clause.line'].create({
            'template_id': self.template.id,
            'clause_id': self.clause.id,
            'sequence': 10,
            'section': 'Confidentiality',
            'required': True,
        })
        self.template.action_approve()
        self.template.action_publish()

        self.partner = self.env['res.partner'].create({'name': 'Acme Fidelity'})
        self.company = self.env.company
        self.base_raw = (
            self.template._active_base_docx_version()
            .document_id.attachment_id.with_context(bin_size=False).raw
        )

    def _render(self, contract=None):
        contract = contract or self.env['clm.contract'].create({
            'name': 'Fidelity Contract Name',
            'contract_type_id': self.contract_type.id,
            'partner_id': self.partner.id,
            'template_id': self.template.id,
        })
        context = contract._clm_docx_assembly_context(version=False)
        context['base_docx_version'] = contract.base_docx_version_id
        return contract, DocxRenderer().render(context)

    def test_01_intentional_blank_paragraphs_preserved(self):
        base_blanks = _blank_indices(self.base_raw)
        self.assertGreaterEqual(len(base_blanks), 2)

        _contract, rendered = self._render()
        rendered_paras = _body_paragraphs(rendered)
        texts = [_para_text(p) for p in rendered_paras]

        # Agreement No. filled, then a blank, then intro — order preserved.
        ref_idx = next(
            i for i, t in enumerate(texts)
            if t.startswith('Agreement No.:')
        )
        self.assertTrue(texts[ref_idx].startswith('Agreement No.:'))
        self.assertEqual(texts[ref_idx + 1].strip(), '')
        self.assertIn('This Agreement is entered into', texts[ref_idx + 2])

        # Blank after intro still present before "1. Term".
        intro_idx = next(i for i, t in enumerate(texts) if t.startswith('This Agreement'))
        term_idx = next(i for i, t in enumerate(texts) if t.strip() == '1. Term')
        self.assertLess(intro_idx, term_idx)
        self.assertTrue(any(
            not texts[i].strip()
            for i in range(intro_idx + 1, term_idx)
        ))

    def test_02_paragraph_spacing_and_alignment_preserved_after_variable_fill(self):
        base_paras = _body_paragraphs(self.base_raw)
        # Index map in _fidelity_base_docx: 0 title, 1 name, 2 ref, 3 blank, 4 intro
        self.assertEqual(_para_align(base_paras[1]), 'right')
        self.assertEqual(_para_spacing(base_paras[2]).get('after'), '120')  # 6pt
        self.assertEqual(_para_spacing(base_paras[3]).get('after'), '120')
        intro_base = _para_spacing(base_paras[4])
        self.assertIn('line', intro_base)

        _contract, rendered = self._render()
        rendered_paras = _body_paragraphs(rendered)
        texts = [_para_text(p) for p in rendered_paras]

        name_idx = next(i for i, t in enumerate(texts) if t == 'Fidelity Contract Name')
        ref_idx = next(i for i, t in enumerate(texts) if t.startswith('Agreement No.:'))
        intro_idx = next(i for i, t in enumerate(texts) if t.startswith('This Agreement'))

        self.assertEqual(_para_align(rendered_paras[name_idx]), 'right')
        self.assertEqual(_para_align(rendered_paras[ref_idx]), 'right')
        self.assertEqual(
            _para_spacing(rendered_paras[ref_idx]).get('after'),
            _para_spacing(base_paras[2]).get('after'),
        )
        blank_after_ref = rendered_paras[ref_idx + 1]
        self.assertEqual(_para_text(blank_after_ref).strip(), '')
        self.assertEqual(
            _para_spacing(blank_after_ref).get('after'),
            _para_spacing(base_paras[3]).get('after'),
        )
        self.assertEqual(
            _para_spacing(rendered_paras[intro_idx]).get('line'),
            intro_base.get('line'),
        )

    def test_03_jinja_control_leftovers_still_removed_not_blanks(self):
        """{% for %} empty shells (zeroed spacing) drop; Base blanks stay."""
        from docx import Document
        from docx.shared import Pt

        document = Document()
        if document.paragraphs:
            p = document.paragraphs[0]._element
            p.getparent().remove(p)
        document.add_paragraph('KEEP')
        intentional = document.add_paragraph('')
        intentional.paragraph_format.space_after = Pt(6)
        leftover = document.add_paragraph('')
        leftover.paragraph_format.space_before = Pt(0)
        leftover.paragraph_format.space_after = Pt(0)
        document.add_paragraph('TAIL')
        buf = io.BytesIO()
        document.save(buf)
        cleaned = DocxRenderer._remove_empty_paragraphs(buf.getvalue())
        texts = [_para_text(p) for p in _body_paragraphs(cleaned)]
        self.assertEqual(texts, ['KEEP', '', 'TAIL'])

    def test_04_preview_html_includes_blank_and_docx_spacing(self):
        contract, rendered = self._render()
        html = contract._clm_html_from_docx_bytes(rendered)
        self.assertIn('Fidelity Contract Name', html)
        self.assertIn('Agreement No.:', html)
        # Blank spacer paragraphs become empty <p>…<br/>…</p> with DOCX margins.
        self.assertIn('<br/>', html)
        self.assertIn('margin-bottom:', html)
        self.assertIn('text-align:right', html)

        # Working-copy preview path shows the final PDF pages from Base DOCX.
        from odoo.addons.clm.services.docx_to_pdf import find_soffice
        if not find_soffice():
            self.skipTest('LibreOffice not available for PDF preview')
        contract.invalidate_recordset(['contract_preview_html'])
        preview = contract.contract_preview_html or ''
        self.assertIn('Fidelity Contract Name', preview)
        self.assertIn('o_clm_pdf_preview', preview)
        self.assertIn('o_clm_pdf_iframe', preview)
        self.assertIn('/web/content/', preview)

    def test_05_clause_loop_expands_without_collapsing_header_spacing(self):
        contract, rendered = self._render()
        texts = [_para_text(p) for p in _body_paragraphs(rendered)]
        self.assertIn('Confidentiality', ''.join(texts))
        ref_idx = next(i for i, t in enumerate(texts) if t.startswith('Agreement No.:'))
        self.assertEqual(texts[ref_idx + 1].strip(), '')
        # Control-tag text must not survive fill.
        joined = '\n'.join(texts)
        self.assertNotIn('{% for', joined)
        self.assertNotIn('{% endfor', joined)

    def test_06_run_bold_and_indent_preserved(self):
        _contract, rendered = self._render()
        paras = _body_paragraphs(rendered)
        texts = [_para_text(p) for p in paras]
        title_idx = texts.index('FIDELITY TITLE')
        title_runs = paras[title_idx].findall('.//{%s}r' % W_NS)
        self.assertTrue(any(
            r.find('{%s}rPr' % W_NS) is not None
            and r.find('{%s}rPr' % W_NS).find('{%s}b' % W_NS) is not None
            for r in title_runs
        ))
        body_idx = next(i for i, t in enumerate(texts) if t.startswith('Normal body'))
        pPr = paras[body_idx].find('{%s}pPr' % W_NS)
        ind = pPr.find('{%s}ind' % W_NS) if pPr is not None else None
        self.assertIsNotNone(ind)
        self.assertEqual(ind.get('{%s}left' % W_NS), '144')

    def test_07_style_clause_zeros_only_control_tags(self):
        styled = docx_editor.style_clause_loop_placeholders(self.base_raw)
        from docx import Document
        doc = Document(io.BytesIO(styled))
        for paragraph in doc.paragraphs:
            text = (paragraph.text or '').strip()
            if text.startswith('{%') and text.endswith('%}'):
                self.assertEqual(paragraph.paragraph_format.space_before, 0)
                self.assertEqual(paragraph.paragraph_format.space_after, 0)
            elif text == '{{ clause.content }}':
                self.assertEqual(paragraph.paragraph_format.space_before, 152400)  # 12pt
            elif text == '{{ clause.category }}':
                self.assertTrue(any(run.italic for run in paragraph.runs))
            elif not text:
                # Intentional blanks must keep non-zero / inherited after.
                after = paragraph.paragraph_format.space_after
                self.assertFalse(after == 0 and paragraph.paragraph_format.space_before == 0)

    def test_08_version_snapshot_fills_clause_category(self):
        """Frozen versions must still print {{ clause.category }} in Base DOCX."""
        from odoo.addons.clm.services.clause_set_context import (
            clause_category_label,
        )

        contract, _live = self._render()
        expected = clause_category_label(self.clause)
        self.assertTrue(expected)

        # Simulate older snapshots that omitted category at freeze time.
        snap = contract._clm_capture_content()
        for row in snap.get('clauses') or []:
            row.pop('category', None)
        version = contract._clm_create_content_version(
            version_type='internal_draft',
            snapshot=snap,
            source='Fidelity category test',
        )

        raw = contract._render_contract_docx(version=version, kind='draft')
        texts = [_para_text(p) for p in _body_paragraphs(raw)]
        self.assertIn(expected, texts)

        # New captures store category for future versions.
        captured = contract._clm_capture_content()
        self.assertTrue(captured['clauses'])
        self.assertEqual(captured['clauses'][0].get('category'), expected)
