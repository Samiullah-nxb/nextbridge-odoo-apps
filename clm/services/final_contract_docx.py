# Part of NextContract. See LICENSE file for full copyright and licensing details.
"""
Final Contract DOCX Writer

Renders the canonical content produced by FinalContractBuilder into a
Microsoft Word document using python-docx.

Architecture:
    FinalContractBuilder → content → FinalContractDocxWriter → DOCX bytes

Layouts
-------
``professional`` — Format 1 (no template). Enterprise legal/CLM look matching
the NextContract Professional Default Format design target. Preview / DOCX /
PDF all consume this same DOCX.

``classic`` — Formats 2–3 (and Format 4 signature append). Preserves the
previous built-in layout so those flows are unchanged.
"""

import base64
import io

from odoo.exceptions import UserError

try:
    import docx
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor
    PYTHON_DOCX_AVAILABLE = True
except ImportError:
    PYTHON_DOCX_AVAILABLE = False

_MUTED = (0x66, 0x66, 0x66)
_INK = (0x11, 0x11, 0x11)

# Professional Default Format palette (design target).
_P = {
    'ink': (0x16, 0x22, 0x33),
    'body': (0x30, 0x38, 0x41),
    'muted': (0x64, 0x70, 0x7D),
    'label': (0x59, 0x6C, 0x82),
    'header': (0x4C, 0x5F, 0x75),
    'header_muted': (0x87, 0x92, 0x9E),
    'footer': (0x78, 0x82, 0x8C),
    'clause': (0x35, 0x44, 0x56),
    'address': (0x55, 0x5F, 0x6C),
    'party_name': (0x1C, 0x27, 0x34),
    'accent': '415A77',
    'label_bg': 'F3F6F8',
    'card_bg': 'F7F9FB',
    'border': 'D9E0E7',
}


class FinalContractDocxWriter:
    """Renders final-contract content into a DOCX binary."""

    def __init__(self, layout='classic'):
        """``layout``: ``'professional'`` (Format 1) or ``'classic'`` (2–3)."""
        self.layout = layout if layout in ('professional', 'classic') else 'classic'

    def append_signatures(self, docx_bytes, content):
        """Append the recorded e-sign / manual signature block to an existing DOCX.

        Format 4 (Base DOCX) fills the Legal template via docxtpl and never
        reaches this writer's full layout. After both parties sign we still need
        their signature images on Download DOCX / the LibreOffice PDF, so the
        filled Base DOCX gets the same Signatures section the built-in writer
        would have produced — **only when the DOCX does not already contain its
        own execution / signature layout**.

        Many Base DOCX templates (and manually signed uploads) already include
        ``Execution and Signatures`` / ``IN WITNESS WHEREOF`` blocks. Appending
        the system evidence table on top of those produces a duplicate section.
        In that case the uploaded / filled DOCX is the authoritative content;
        execution metadata remains in the CLM records and is not re-rendered.

        Always uses the classic signature block so Format 4 output is not
        restyled by the professional Default Format.
        """
        if not PYTHON_DOCX_AVAILABLE:
            raise UserError(
                "Cannot append signatures: the 'python-docx' package is not installed."
            )
        if not docx_bytes:
            return docx_bytes
        if not content or content.get('signature_evidence') == 'none':
            return docx_bytes
        previous = self.layout
        self.layout = 'classic'
        try:
            document = docx.Document(io.BytesIO(docx_bytes))
            if self._docx_has_own_signature_layout(document):
                return docx_bytes
            self._write_signatures_classic(document, content)
        finally:
            self.layout = previous
        output = io.BytesIO()
        document.save(output)
        return output.getvalue()

    @staticmethod
    def _docx_has_own_signature_layout(document):
        """True when the DOCX already owns an execution / signature section.

        Detects Legal Base DOCX signature blocks and a previously appended
        system evidence table so preview / DOCX / PDF never duplicate them.
        Intentionally narrow: a lone word like ``signature`` in clause text
        does not count.
        """
        texts = []
        for paragraph in document.paragraphs:
            text = (paragraph.text or '').strip()
            if text:
                texts.append(text)
        for table in document.tables:
            for row in table.rows:
                for cell in row.cells:
                    text = (cell.text or '').strip()
                    if text:
                        texts.append(text)
        if not texts:
            return False
        joined = '\n'.join(texts)
        lower = joined.lower()

        # System evidence table already present (repeat preview / re-export).
        if 'the parties executed this agreement as recorded below' in lower:
            return True
        if (
            'signed on' in lower
            and 'manual signature' in lower
            and 'authorised signatory' in lower
        ):
            return True

        # Base DOCX / uploaded Word execution section.
        if 'execution and signatures' in lower:
            return True
        if 'in witness whereof' in lower:
            return True
        # Insert-Signature-Block style lines (Name / Title / Signature / Date).
        has_signature_line = any(
            t.lower().startswith('signature:') for t in texts
        )
        has_name_line = any(t.lower().startswith('name:') for t in texts)
        has_date_line = any(t.lower().startswith('date:') for t in texts)
        if has_signature_line and has_name_line and has_date_line:
            return True
        return False

    def render(self, content):
        if not PYTHON_DOCX_AVAILABLE:
            raise UserError(
                "DOCX generation requires the 'python-docx' Python package. "
                "Please install it on the Odoo server: pip install python-docx"
            )

        document = docx.Document()
        if self.layout == 'professional':
            self._setup_page_professional(document)
            self._write_running_titles_professional(document, content)
            self._write_cover_professional(document, content)
            self._write_contract_information(document, content)
            self._write_parties_professional(document, content)
            self._write_commercial_legal_panel(document, content)
            self._write_body_professional(document, content)
            self._write_changes_classic(document, content)
            self._write_deviations_classic(document, content)
            self._write_signatures_professional(document, content)
        else:
            self._setup_page_classic(document)
            self._write_cover_classic(document, content)
            self._write_parties_classic(document, content)
            self._write_terms_classic(document, content)
            self._write_body_classic(document, content)
            self._write_changes_classic(document, content)
            self._write_deviations_classic(document, content)
            self._write_signatures_classic(document, content)

        output = io.BytesIO()
        document.save(output)
        return output.getvalue()

    # =========================================================================
    # Professional layout (Format 1)
    # =========================================================================

    def _setup_page_professional(self, document):
        section = document.sections[0]
        # Dual-chrome header/footer need a little more band than the old
        # single-line brand so the grey rules clear the body text.
        section.header_distance = Cm(0.7)
        section.footer_distance = Cm(0.85)
        section.top_margin = Cm(2.95)
        section.bottom_margin = Cm(1.9)
        section.left_margin = Cm(1.83)
        section.right_margin = Cm(1.83)

        normal = document.styles['Normal']
        normal.font.name = 'Calibri'
        normal.font.size = Pt(9.5)
        normal.font.color.rgb = RGBColor(*_P['body'])
        normal.paragraph_format.space_after = Pt(5)
        normal.paragraph_format.line_spacing = 1.15

        for name, size, color in (
            ('Title', 18, _P['ink']),
            ('Heading 1', 10, _P['ink']),
            ('Heading 2', 9.5, _P['clause']),
            ('Heading 3', 9.5, _P['clause']),
            ('Heading 4', 9.5, _P['clause']),
        ):
            try:
                style = document.styles[name]
            except KeyError:
                continue
            style.font.name = 'Calibri'
            style.font.size = Pt(size)
            style.font.bold = True
            style.font.color.rgb = RGBColor(*color)
            style.paragraph_format.space_before = Pt(8)
            style.paragraph_format.space_after = Pt(4)

    def _write_running_titles_professional(self, document, content):
        """Dual-chrome legal header/footer shared by Preview / DOCX / PDF."""
        section = document.sections[0]
        ref = (content.get('reference') or '').strip()

        # ── Header: NEXTCONTRACT (left) · contract number (right) + bottom rule
        header = section.header
        header.is_linked_to_previous = False
        header.paragraphs[0].clear()
        header_table = header.add_table(rows=1, cols=2, width=Cm(17.34))
        self._set_table_autofit(header_table)
        self._style_chrome_table(header_table)
        left_cell, right_cell = header_table.rows[0].cells
        self._fill_chrome_cell(
            left_cell,
            text='NEXTCONTRACT',
            bold=True,
            size=9,
            color=_P['header'],
            align=WD_ALIGN_PARAGRAPH.LEFT,
            letter_spacing=80,
            edge='bottom',
        )
        self._fill_chrome_cell(
            right_cell,
            text=ref,
            bold=False,
            size=8,
            color=_P['header_muted'],
            align=WD_ALIGN_PARAGRAPH.RIGHT,
            edge='bottom',
        )

        # ── Footer: contract number (left) · Page X of Y (right) + top rule
        footer = section.footer
        footer.is_linked_to_previous = False
        footer.paragraphs[0].clear()
        footer_table = footer.add_table(rows=1, cols=2, width=Cm(17.34))
        self._set_table_autofit(footer_table)
        self._style_chrome_table(footer_table)
        left_f, right_f = footer_table.rows[0].cells
        self._fill_chrome_cell(
            left_f,
            text=ref,
            bold=False,
            size=8,
            color=_P['footer'],
            align=WD_ALIGN_PARAGRAPH.LEFT,
            edge='top',
        )
        self._fill_chrome_page_cell(right_f, edge='top')

    def _style_chrome_table(self, table):
        """Borderless full-width table used only for header/footer chrome."""
        tbl = table._tbl
        tblPr = tbl.tblPr if tbl.tblPr is not None else OxmlElement('w:tblPr')
        if tbl.tblPr is None:
            tbl.insert(0, tblPr)
        for old in tblPr.findall(qn('w:tblBorders')):
            tblPr.remove(old)
        borders = OxmlElement('w:tblBorders')
        for edge in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
            element = OxmlElement('w:%s' % edge)
            element.set(qn('w:val'), 'nil')
            element.set(qn('w:sz'), '0')
            element.set(qn('w:space'), '0')
            element.set(qn('w:color'), 'auto')
            borders.append(element)
        tblPr.append(borders)
        # Prefer 100% page width inside the header/footer band.
        for old in tblPr.findall(qn('w:tblW')):
            tblPr.remove(old)
        tblW = OxmlElement('w:tblW')
        tblW.set(qn('w:w'), '5000')
        tblW.set(qn('w:type'), 'pct')
        tblPr.append(tblW)

    def _fill_chrome_cell(
        self, cell, text='', bold=False, size=8, color=None,
        align=WD_ALIGN_PARAGRAPH.LEFT, letter_spacing=0, edge=None,
    ):
        cell.paragraphs[0].clear()
        paragraph = cell.paragraphs[0]
        paragraph.alignment = align
        paragraph.paragraph_format.space_before = Pt(0)
        paragraph.paragraph_format.space_after = Pt(2 if edge == 'bottom' else 0)
        if text:
            run = paragraph.add_run(text)
            run.bold = bool(bold)
            run.font.size = Pt(size)
            run.font.name = 'Calibri'
            if color:
                run.font.color.rgb = RGBColor(*color)
            if letter_spacing:
                self._set_run_letter_spacing(run, letter_spacing)
        self._set_chrome_cell_edge(cell, edge=edge)
        self._set_cell_margins(cell, top=40, start=0, bottom=40, end=0)

    def _fill_chrome_page_cell(self, cell, edge='top'):
        cell.paragraphs[0].clear()
        paragraph = cell.paragraphs[0]
        paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        paragraph.paragraph_format.space_before = Pt(2)
        paragraph.paragraph_format.space_after = Pt(0)

        def _style_run(run):
            run.font.size = Pt(8)
            run.font.name = 'Calibri'
            run.font.color.rgb = RGBColor(*_P['footer'])

        label = paragraph.add_run('Page ')
        _style_run(label)
        self._add_field(paragraph, 'PAGE')
        of_run = paragraph.add_run(' of ')
        _style_run(of_run)
        self._add_field(paragraph, 'NUMPAGES')
        for run in paragraph.runs:
            _style_run(run)
        self._set_chrome_cell_edge(cell, edge=edge)
        self._set_cell_margins(cell, top=40, start=0, bottom=40, end=0)

    @staticmethod
    def _set_chrome_cell_edge(cell, edge=None, color='E5E7EB', size='8'):
        """Draw only the requested edge (top or bottom rule); hide the rest."""
        tc = cell._tc
        tcPr = tc.get_or_add_tcPr()
        for old in tcPr.findall(qn('w:tcBorders')):
            tcPr.remove(old)
        borders = OxmlElement('w:tcBorders')
        for name in ('top', 'left', 'bottom', 'right'):
            element = OxmlElement('w:%s' % name)
            if edge and name == edge:
                element.set(qn('w:val'), 'single')
                element.set(qn('w:sz'), str(size))
                element.set(qn('w:space'), '4' if edge == 'bottom' else '2')
                element.set(qn('w:color'), color)
            else:
                element.set(qn('w:val'), 'nil')
                element.set(qn('w:sz'), '0')
                element.set(qn('w:space'), '0')
                element.set(qn('w:color'), 'auto')
            borders.append(element)
        tcPr.append(borders)

    @staticmethod
    def _set_run_letter_spacing(run, twentieths):
        """Word character spacing (twentieths of a point) for a small-caps feel."""
        rPr = run._r.get_or_add_rPr()
        for old in rPr.findall(qn('w:spacing')):
            rPr.remove(old)
        spacing = OxmlElement('w:spacing')
        spacing.set(qn('w:val'), str(int(twentieths)))
        rPr.append(spacing)

    def _write_cover_professional(self, document, content):
        title = document.add_paragraph()
        title.alignment = WD_ALIGN_PARAGRAPH.LEFT
        title.paragraph_format.space_before = Pt(0)
        title.paragraph_format.space_after = Pt(6)
        tr = title.add_run(content.get('title') or '')
        tr.bold = True
        tr.font.size = Pt(18)
        tr.font.color.rgb = RGBColor(*_P['ink'])
        tr.font.name = 'Calibri'
        # Thin accent rule under the title (same in DOCX / PDF / Preview).
        self._set_paragraph_bottom_border(title, color='4F81BD', sz=8, space=4)

        meta = document.add_paragraph()
        meta.paragraph_format.space_before = Pt(6)
        meta.paragraph_format.space_after = Pt(10)
        parts = []
        if content.get('reference'):
            parts.append(('ref', content['reference']))
        if content.get('contract_type'):
            parts.append(('muted', content['contract_type'].upper()))
        if content.get('kind') == 'draft':
            parts.append(('muted', 'DRAFT'))
        elif content.get('kind') == 'signed':
            parts.append(('muted', 'EXECUTED'))
        elif content.get('kind_label'):
            parts.append(('muted', content['kind_label'].upper()))
        version = (content.get('version_label') or '').strip()
        if version:
            parts.append(('muted', 'VERSION %s' % version.upper()))
        for index, (kind_part, text) in enumerate(parts):
            if index:
                sep = meta.add_run('   ·   ')
                sep.font.size = Pt(8.5)
                sep.font.color.rgb = RGBColor(*_P['muted'])
            run = meta.add_run(text)
            run.font.name = 'Calibri'
            if kind_part == 'ref':
                run.bold = True
                run.font.size = Pt(10)
                run.font.color.rgb = RGBColor(*_P['ink'])
            else:
                run.font.size = Pt(8.5)
                run.font.color.rgb = RGBColor(*_P['muted'])

        self._add_accent_bar(document)

        if content.get('kind') == 'signed':
            note = document.add_paragraph(
                'This Word document is the editable representation of the final '
                'contract. The signed PDF remains the authoritative signed artifact.'
            )
            self._shade_runs(note)

    def _write_contract_information(self, document, content):
        pairs = []
        for term in content.get('key_terms') or []:
            label = (term.get('label') or '').strip()
            value = term.get('value')
            value = ('' if value is None else str(value)).strip()
            if label and value:
                pairs.append((label, value))
        status = (content.get('status_label') or '').strip()
        if status:
            pairs.append(('Status', status))
        execution = content.get('execution') or {}
        if execution.get('executed_on'):
            pairs.append(('Executed On', execution['executed_on']))
        if execution.get('method_label'):
            pairs.append(('Execution Method', execution['method_label']))
        if not pairs:
            return
        self._section_label(document, 'CONTRACT INFORMATION')
        self._add_meta_grid(document, pairs)

    def _write_parties_professional(self, document, content):
        parties = content.get('parties') or []
        if not parties:
            return
        self._section_label(document, 'PARTIES')
        cols = 2 if len(parties) >= 2 else 1
        table = document.add_table(rows=1, cols=cols)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        self._set_table_autofit(table)

        cells = table.rows[0].cells
        for index, party in enumerate(parties[:cols]):
            self._fill_party_card(cells[index], party)
        remaining = parties[cols:]
        while remaining:
            row = table.add_row()
            chunk, remaining = remaining[:cols], remaining[cols:]
            for index, party in enumerate(chunk):
                self._fill_party_card(row.cells[index], party)
            for index in range(len(chunk), cols):
                self._style_card_cell(row.cells[index])
        document.add_paragraph()

    def _fill_party_card(self, cell, party):
        self._style_card_cell(cell)
        role = self._party_role_label(party.get('role') or '')
        name = (party.get('name') or '').strip()
        address = (party.get('address') or '').strip()

        cell.paragraphs[0].clear()
        p_role = cell.paragraphs[0]
        r = p_role.add_run(role)
        r.bold = True
        r.font.size = Pt(7.5)
        r.font.color.rgb = RGBColor(*_P['label'])
        r.font.name = 'Calibri'
        p_role.paragraph_format.space_after = Pt(2)

        p_name = cell.add_paragraph()
        rn = p_name.add_run(name)
        rn.bold = True
        rn.font.size = Pt(10)
        rn.font.color.rgb = RGBColor(*_P['party_name'])
        rn.font.name = 'Calibri'
        p_name.paragraph_format.space_after = Pt(2)

        if address:
            p_addr = cell.add_paragraph()
            ra = p_addr.add_run(address)
            ra.font.size = Pt(8.5)
            ra.font.color.rgb = RGBColor(*_P['address'])
            ra.font.name = 'Calibri'
            p_addr.paragraph_format.space_after = Pt(0)

        representative = (party.get('representative') or '').strip()
        if representative:
            label = (party.get('representative_label') or 'Representative').strip()
            p_rep = cell.add_paragraph()
            rr = p_rep.add_run('%s: %s' % (label, representative))
            rr.font.size = Pt(8)
            rr.font.color.rgb = RGBColor(*_P['muted'])
            rr.font.name = 'Calibri'

    @staticmethod
    def _party_role_label(role):
        key = (role or '').strip().lower()
        if key in ('company', 'provider', 'contract provider'):
            return 'CONTRACT PROVIDER'
        if key in ('counterparty', 'partner', 'customer', 'client'):
            return 'COUNTERPARTY'
        return (role or 'PARTY').strip().upper() or 'PARTY'

    def _write_commercial_legal_panel(self, document, content):
        pairs = []
        for group in (
            content.get('commercial_terms') or [],
            content.get('legal_terms') or [],
            content.get('other_terms') or [],
        ):
            for term in group:
                label = (term.get('label') or '').strip()
                value = term.get('value')
                value = ('' if value is None else str(value)).strip()
                if label and value:
                    pairs.append((label, value))
        if not pairs:
            return
        self._section_label(document, 'COMMERCIAL & LEGAL TERMS')
        per_row = 3
        for start in range(0, len(pairs), per_row):
            chunk = pairs[start:start + per_row]
            cols = len(chunk) * 2
            table = document.add_table(rows=1, cols=cols)
            table.alignment = WD_TABLE_ALIGNMENT.CENTER
            self._set_table_autofit(table)
            cells = table.rows[0].cells
            for index, (label, value) in enumerate(chunk):
                self._fill_label_cell(cells[index * 2], label.upper())
                self._fill_value_cell(cells[index * 2 + 1], value)
        document.add_paragraph()

    def _write_body_professional(self, document, content):
        self._section_label(document, 'CONTRACT TERMS AND CONDITIONS')
        if not content.get('body'):
            note = document.add_paragraph(
                'No contract wording could be recovered for this contract. Attach '
                'the agreed document to the final version, or assemble the '
                'contract from a published template, and export again.'
            )
            self._shade_runs(note, size=9)
            return

        for entry in content['body']:
            heading = (entry.get('heading') or '').strip()
            level = int(entry.get('level') or 1)
            blocks = entry.get('blocks') or []
            if heading:
                paragraph = document.add_heading(
                    heading, level=min(max(level, 1), 4),
                )
                paragraph.paragraph_format.keep_with_next = True
                color = _P['ink'] if level <= 1 else _P['clause']
                self._apply_color(paragraph, color)
            for block in blocks:
                text = block.get('text') or ''
                if block.get('kind') == 'li':
                    p = document.add_paragraph(text, style='List Bullet')
                else:
                    p = document.add_paragraph(text)
                p.paragraph_format.space_after = Pt(8)
                for run in p.runs:
                    run.font.size = Pt(9.5)
                    run.font.color.rgb = RGBColor(*_P['body'])
                    run.font.name = 'Calibri'

    def _write_signatures_professional(self, document, content):
        """Default Format signature cards — same block as the signing PDF.

        Side-by-side FOR AND ON BEHALF OF cards. Signature stays blank for the
        Odoo Sign stroke; Name / Date are printed (Base DOCX parity with
        ``{{ company_signatory }}`` / ``{{ signing_date }}``) so the Signed PDF
        shows who signed and when. Never switch to the Party/Signatory evidence table.
        """
        self._section_label(document, 'SIGNATURES')

        intro = document.add_paragraph(
            'The parties shall execute this agreement as set out below.'
        )
        intro.paragraph_format.space_after = Pt(8)

        parties = content.get('parties') or []
        if not parties:
            return
        signing_date = (content.get('signing_date') or '').strip()
        # Prefer recorded e-sign identity / date when rebuilding after signing.
        by_party = {
            (row.get('party') or '').strip().lower(): row
            for row in (content.get('signatures') or [])
            if (row.get('party') or '').strip()
        }
        cols = 2 if len(parties) >= 2 else 1
        table = document.add_table(rows=1, cols=cols)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        self._set_table_autofit(table)
        cells = table.rows[0].cells
        for index, party in enumerate(parties[:cols]):
            self._fill_signature_card(
                cells[index], party, signing_date=signing_date, evidence=by_party,
            )
        remaining = parties[cols:]
        while remaining:
            row = table.add_row()
            chunk, remaining = remaining[:cols], remaining[cols:]
            for index, party in enumerate(chunk):
                self._fill_signature_card(
                    row.cells[index], party,
                    signing_date=signing_date, evidence=by_party,
                )
        if content.get('kind') == 'signed' and content.get('signature_evidence') == 'none':
            # Names / date above come from the contract record, not from a
            # captured signature: say so rather than imply a verified signing.
            note = document.add_paragraph(
                'This contract is recorded as executed, but no signer details '
                'were captured. The signed document is the authoritative record '
                'of who signed and when.'
            )
            self._shade_runs(note, size=9)
        document.add_paragraph()

    def _fill_signature_card(self, cell, party, signing_date='', evidence=None):
        """Signature blank; Name / Date printed when known (signing PDF parity)."""
        self._style_card_cell(cell)
        cell.paragraphs[0].clear()
        p_role = cell.paragraphs[0]
        r = p_role.add_run('FOR AND ON BEHALF OF')
        r.bold = True
        r.font.size = Pt(7.5)
        r.font.color.rgb = RGBColor(*_P['label'])
        r.font.name = 'Calibri'
        p_role.paragraph_format.space_after = Pt(2)

        p_name = cell.add_paragraph()
        rn = p_name.add_run((party.get('name') or '').strip())
        rn.bold = True
        rn.font.size = Pt(10)
        rn.font.color.rgb = RGBColor(*_P['party_name'])
        rn.font.name = 'Calibri'
        p_name.paragraph_format.space_after = Pt(10)

        evidence_row = (evidence or {}).get((party.get('name') or '').strip().lower()) or {}
        signatory = (
            evidence_row.get('name')
            or party.get('signatory')
            or party.get('representative')
            or ''
        ).strip()
        date_value = (
            evidence_row.get('signed_on')
            or party.get('signing_date')
            or signing_date
            or ''
        ).strip()

        for label, value in (
            ('Signature', ''),
            ('Name', signatory),
            ('Date', date_value),
        ):
            p = cell.add_paragraph()
            line = (
                '%s: %s' % (label, value)
                if value else
                '%s: ______________________________' % label
            )
            rr = p.add_run(line)
            rr.font.size = Pt(8.5)
            rr.font.color.rgb = RGBColor(*_P['body'])
            rr.font.name = 'Calibri'
            # Slightly more than Name→Date so a Sign stroke fits, without a
            # large empty band on View Final / Download PDF.
            p.paragraph_format.space_after = Pt(12) if label == 'Signature' else Pt(6)

    def _section_label(self, document, text):
        paragraph = document.add_paragraph()
        paragraph.paragraph_format.space_before = Pt(12)
        paragraph.paragraph_format.space_after = Pt(5)
        paragraph.paragraph_format.keep_with_next = True
        run = paragraph.add_run(text)
        run.bold = True
        run.font.size = Pt(8)
        run.font.color.rgb = RGBColor(*_P['label'])
        run.font.name = 'Calibri'
        return paragraph

    def _add_accent_bar(self, document):
        table = document.add_table(rows=1, cols=1)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        cell = table.rows[0].cells[0]
        self._set_cell_shading(cell, _P['accent'])
        self._set_cell_borders(cell, color=_P['accent'], size='4')
        cell.paragraphs[0].clear()
        tr = table.rows[0]._tr
        trPr = tr.get_or_add_trPr()
        trHeight = OxmlElement('w:trHeight')
        # ~6pt rule — visible in Word/PDF and easy to map in HTML Preview.
        trHeight.set(qn('w:val'), '120')
        trHeight.set(qn('w:hRule'), 'exact')
        trPr.append(trHeight)
        # No trailing empty paragraph — spacing comes from the next section label.

    @staticmethod
    def _set_paragraph_bottom_border(paragraph, color='4F81BD', sz=8, space=4):
        """Thin bottom rule (sz is eighths of a point; space is points)."""
        pPr = paragraph._p.get_or_add_pPr()
        for old in pPr.findall(qn('w:pBdr')):
            pPr.remove(old)
        pBdr = OxmlElement('w:pBdr')
        bottom = OxmlElement('w:bottom')
        bottom.set(qn('w:val'), 'single')
        bottom.set(qn('w:sz'), str(sz))
        bottom.set(qn('w:space'), str(space))
        bottom.set(qn('w:color'), color)
        pBdr.append(bottom)
        pPr.append(pBdr)

    def _add_meta_grid(self, document, pairs):
        rows_data = []
        for index in range(0, len(pairs), 2):
            left = pairs[index]
            right = pairs[index + 1] if index + 1 < len(pairs) else None
            rows_data.append((left, right))

        table = document.add_table(rows=0, cols=4)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        self._set_table_autofit(table)
        for left, right in rows_data:
            row = table.add_row()
            cells = row.cells
            self._fill_label_cell(cells[0], left[0])
            self._fill_value_cell(cells[1], left[1])
            if right:
                self._fill_label_cell(cells[2], right[0])
                self._fill_value_cell(cells[3], right[1])
            else:
                self._fill_label_cell(cells[2], '')
                self._fill_value_cell(cells[3], '')
        document.add_paragraph()

    def _fill_label_cell(self, cell, text):
        self._style_label_cell(cell)
        cell.paragraphs[0].clear()
        run = cell.paragraphs[0].add_run(text or '')
        run.bold = True
        run.font.size = Pt(8)
        run.font.color.rgb = RGBColor(*_P['label'])
        run.font.name = 'Calibri'

    def _fill_value_cell(self, cell, text):
        self._style_value_cell(cell)
        cell.paragraphs[0].clear()
        run = cell.paragraphs[0].add_run(text or '')
        run.font.size = Pt(9.5)
        run.font.color.rgb = RGBColor(*_P['ink'])
        run.font.name = 'Calibri'

    def _style_label_cell(self, cell):
        self._set_cell_shading(cell, _P['label_bg'])
        self._set_cell_borders(cell, color=_P['border'], size='4')
        self._set_cell_margins(cell, top=80, start=110, bottom=80, end=110)
        self._set_cell_valign(cell, 'center')

    def _style_value_cell(self, cell):
        self._set_cell_borders(cell, color=_P['border'], size='4')
        self._set_cell_margins(cell, top=80, start=110, bottom=80, end=110)
        self._set_cell_valign(cell, 'center')

    def _style_card_cell(self, cell):
        self._set_cell_shading(cell, _P['card_bg'])
        self._set_cell_borders(cell, color=_P['border'], size='4')
        self._set_cell_margins(cell, top=120, start=140, bottom=120, end=140)
        self._set_cell_valign(cell, 'top')

    # =========================================================================
    # Classic layout (Formats 2–3 / signature append)
    # =========================================================================

    def _setup_page_classic(self, document):
        section = document.sections[0]
        section.top_margin = Cm(2.5)
        section.bottom_margin = Cm(2.5)
        section.left_margin = Cm(2.5)
        section.right_margin = Cm(2.0)

        normal = document.styles['Normal']
        normal.font.name = 'Calibri'
        normal.font.size = Pt(11)
        normal.paragraph_format.space_after = Pt(8)
        normal.paragraph_format.line_spacing = 1.15

        ink = RGBColor(*_INK)
        for style_name in (
            'Title', 'Heading 1', 'Heading 2', 'Heading 3', 'Heading 4',
        ):
            try:
                style = document.styles[style_name]
            except KeyError:
                continue
            style.font.color.rgb = ink
            style.font.bold = True

    def _add_heading_classic(self, document, text, level=1):
        paragraph = document.add_heading(text, level=level)
        self._apply_color(paragraph, _INK)
        return paragraph

    def _write_running_titles_classic(self, document, content):
        section = document.sections[0]
        header = section.header.paragraphs[0]
        header.text = ' — '.join(
            part for part in (content.get('reference'), content.get('title')) if part
        )
        header.alignment = WD_ALIGN_PARAGRAPH.CENTER
        self._shade_runs(header)

        footer = section.footer.paragraphs[0]
        footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
        footer.add_run('Page ')
        self._add_field(footer, 'PAGE')
        footer.add_run(' of ')
        self._add_field(footer, 'NUMPAGES')
        self._shade_runs(footer)

    def _write_cover_classic(self, document, content):
        self._write_running_titles_classic(document, content)

        title = document.add_paragraph(content.get('title') or '', style='Title')
        title.alignment = WD_ALIGN_PARAGRAPH.CENTER
        self._apply_color(title, _INK)

        subtitle_parts = [
            part for part in (content.get('reference'), content.get('contract_type')) if part
        ]
        if subtitle_parts:
            subtitle = document.add_paragraph(' · '.join(subtitle_parts))
            subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
            self._shade_runs(subtitle, size=11)

        meta = [
            ('Document', content.get('kind_label')),
            ('Contract Number', content.get('reference')),
            (
                'Version' if content.get('kind') == 'draft' else 'Final Version',
                content.get('version_label'),
            ),
            ('Status', content.get('status_label')),
        ]
        execution = content.get('execution') or {}
        if execution.get('executed_on'):
            meta.append(('Executed On', execution['executed_on']))
        if execution.get('method_label'):
            meta.append(('Execution Method', execution['method_label']))
        self._add_definition_table(document, meta)

        if content.get('kind') == 'signed':
            note = document.add_paragraph(
                'This Word document is the editable representation of the final '
                'contract. The signed PDF remains the authoritative signed artifact.'
            )
            self._shade_runs(note)

    def _write_parties_classic(self, document, content):
        if not content.get('parties'):
            return
        self._add_heading_classic(document, 'Parties', level=1)
        table = document.add_table(rows=1, cols=3)
        table.style = 'Table Grid'
        self._fill_header_row(table.rows[0], ('Party', 'Name', 'Address'))
        for party in content['parties']:
            cells = table.add_row().cells
            cells[0].text = party.get('role') or ''
            cells[1].text = party.get('name') or ''
            cells[2].text = party.get('address') or ''
        document.add_paragraph()

    def _write_terms_classic(self, document, content):
        groups = (
            ('Contract Terms', content.get('key_terms')),
            ('Commercial Terms', content.get('commercial_terms')),
            ('Legal Terms', content.get('legal_terms')),
            ('Other Terms', content.get('other_terms')),
        )
        for heading, terms in groups:
            if not terms:
                continue
            self._add_heading_classic(document, heading, level=1)
            self._add_definition_table(
                document, [(term['label'], term['value']) for term in terms]
            )

    def _write_body_classic(self, document, content):
        document.add_page_break()
        self._add_heading_classic(document, 'Contract Terms and Conditions', level=1)
        if not content.get('body'):
            note = document.add_paragraph(
                'No contract wording could be recovered for this contract. Attach '
                'the agreed document to the final version, or assemble the '
                'contract from a published template, and export again.'
            )
            self._shade_runs(note, size=10)
            return
        for entry in content['body']:
            if entry.get('heading'):
                self._add_heading_classic(
                    document, entry['heading'],
                    level=min((entry.get('level') or 1) + 1, 4),
                )
            for block in entry.get('blocks') or []:
                if block.get('kind') == 'li':
                    document.add_paragraph(block.get('text') or '', style='List Bullet')
                else:
                    document.add_paragraph(block.get('text') or '')

    def _write_changes_classic(self, document, content):
        if not content.get('approved_changes'):
            return
        if self.layout == 'professional':
            self._section_label(document, 'APPROVED CHANGES')
        else:
            self._add_heading_classic(document, 'Approved Changes', level=1)
        table = document.add_table(rows=1, cols=3)
        table.style = 'Table Grid'
        self._fill_header_row(table.rows[0], ('Version', 'Classification', 'Change'))
        for change in content['approved_changes']:
            cells = table.add_row().cells
            cells[0].text = change.get('version') or ''
            cells[1].text = change.get('classification') or ''
            cells[2].text = change.get('summary') or ''
        document.add_paragraph()

    def _write_deviations_classic(self, document, content):
        if not content.get('deviations'):
            return
        if self.layout == 'professional':
            self._section_label(document, 'APPLICABLE DEVIATIONS AND EXEMPTIONS')
        else:
            self._add_heading_classic(
                document, 'Applicable Deviations and Exemptions', level=1,
            )
        table = document.add_table(rows=1, cols=3)
        table.style = 'Table Grid'
        self._fill_header_row(table.rows[0], ('Clause', 'Deviation', 'Rationale'))
        for deviation in content['deviations']:
            cells = table.add_row().cells
            cells[0].text = deviation.get('clause') or ''
            cells[1].text = deviation.get('type') or ''
            cells[2].text = deviation.get('rationale') or ''
        document.add_paragraph()

    def _write_signatures_classic(self, document, content):
        self._add_heading_classic(document, 'Signatures', level=1)

        if content.get('signature_evidence') != 'none':
            intro = document.add_paragraph(
                'The parties executed this agreement as recorded below.'
            )
            intro.paragraph_format.space_after = Pt(12)
            table = document.add_table(rows=1, cols=5)
            table.style = 'Table Grid'
            self._fill_header_row(
                table.rows[0], ('Party', 'Signatory', 'Role', 'Signed On', 'Method')
            )
            for signature in content.get('signatures') or []:
                cells = table.add_row().cells
                cells[0].text = signature.get('party') or ''
                cells[1].text = signature.get('name') or ''
                cells[2].text = signature.get('role') or ''
                cells[3].text = signature.get('signed_on') or ''
                cells[4].text = signature.get('method') or ''
                image = signature.get('image')
                if image and image.get('base64'):
                    try:
                        raw = base64.b64decode(image['base64'])
                        caption = document.add_paragraph(
                            'Electronic signature — %s'
                            % (signature.get('name') or 'Signer')
                        )
                        self._shade_runs(caption, size=9)
                        document.add_picture(io.BytesIO(raw), width=Cm(5))
                    except Exception:
                        pass
            document.add_paragraph()
        else:
            if content.get('kind') == 'signed':
                note = document.add_paragraph(
                    'This contract is recorded as executed, but no signer details '
                    'were captured. The signed document is the authoritative record '
                    'of who signed and when.'
                )
                self._shade_runs(note, size=10)
            else:
                document.add_paragraph(
                    'The parties shall execute this agreement as set out below.'
                )
            for party in content.get('parties') or []:
                document.add_paragraph()
                document.add_paragraph(
                    'For and on behalf of %s' % (party.get('name') or '')
                )
                signatory = (
                    party.get('signatory')
                    or party.get('representative')
                    or ''
                ).strip()
                date_value = (
                    party.get('signing_date')
                    or content.get('signing_date')
                    or ''
                ).strip()
                document.add_paragraph('Signature: ______________________')
                document.add_paragraph(
                    'Name: %s' % (signatory or '______________________')
                )
                date_line = document.add_paragraph(
                    'Date: %s' % (date_value or '______________')
                )
                date_line.paragraph_format.space_after = Pt(18)

        image = content.get('signature_image')
        if image:
            caption = document.add_paragraph('Signed document on file:')
            self._shade_runs(caption, size=9)
            try:
                document.add_picture(io.BytesIO(image['bytes']), width=Cm(11))
            except Exception:
                fallback = document.add_paragraph(image.get('name') or '')
                self._shade_runs(fallback, size=9)

    # =========================================================================
    # Shared helpers
    # =========================================================================

    @staticmethod
    def _add_field(paragraph, instruction):
        run = paragraph.add_run()
        begin = OxmlElement('w:fldChar')
        begin.set(qn('w:fldCharType'), 'begin')
        instr = OxmlElement('w:instrText')
        instr.set(qn('xml:space'), 'preserve')
        instr.text = instruction
        end = OxmlElement('w:fldChar')
        end.set(qn('w:fldCharType'), 'end')
        run._r.append(begin)
        run._r.append(instr)
        run._r.append(end)

    @staticmethod
    def _shade_runs(paragraph, size=9):
        for run in paragraph.runs:
            run.font.size = Pt(size)
            run.font.color.rgb = RGBColor(*_MUTED)

    @staticmethod
    def _apply_color(paragraph, rgb_tuple):
        color = RGBColor(*rgb_tuple)
        for run in paragraph.runs:
            run.font.color.rgb = color

    @staticmethod
    def _fill_header_row(row, labels):
        for cell, label in zip(row.cells, labels):
            cell.text = label
            for paragraph in cell.paragraphs:
                for run in paragraph.runs:
                    run.bold = True

    def _add_definition_table(self, document, pairs):
        rows = [(label, value) for label, value in pairs if value]
        if not rows:
            return
        table = document.add_table(rows=0, cols=2)
        table.style = 'Table Grid'
        for label, value in rows:
            cells = table.add_row().cells
            cells[0].text = label
            cells[1].text = str(value)
            for paragraph in cells[0].paragraphs:
                for run in paragraph.runs:
                    run.bold = True
        table.columns[0].width = Cm(6)
        document.add_paragraph()

    @staticmethod
    def _set_table_autofit(table):
        tbl = table._tbl
        tblPr = tbl.tblPr if tbl.tblPr is not None else OxmlElement('w:tblPr')
        if tbl.tblPr is None:
            tbl.insert(0, tblPr)
        layout = tblPr.find(qn('w:tblLayout'))
        if layout is None:
            layout = OxmlElement('w:tblLayout')
            tblPr.append(layout)
        layout.set(qn('w:type'), 'autofit')

    @staticmethod
    def _set_cell_shading(cell, fill):
        tc = cell._tc
        tcPr = tc.get_or_add_tcPr()
        for old in tcPr.findall(qn('w:shd')):
            tcPr.remove(old)
        shd = OxmlElement('w:shd')
        shd.set(qn('w:fill'), fill)
        shd.set(qn('w:val'), 'clear')
        tcPr.append(shd)

    @staticmethod
    def _set_cell_borders(cell, color='D9E0E7', size='4'):
        tc = cell._tc
        tcPr = tc.get_or_add_tcPr()
        for old in tcPr.findall(qn('w:tcBorders')):
            tcPr.remove(old)
        borders = OxmlElement('w:tcBorders')
        for edge in ('top', 'left', 'bottom', 'right'):
            element = OxmlElement('w:%s' % edge)
            element.set(qn('w:val'), 'single')
            element.set(qn('w:sz'), size)
            element.set(qn('w:color'), color)
            element.set(qn('w:space'), '0')
            borders.append(element)
        tcPr.append(borders)

    @staticmethod
    def _set_cell_margins(cell, top=80, start=110, bottom=80, end=110):
        tc = cell._tc
        tcPr = tc.get_or_add_tcPr()
        for old in tcPr.findall(qn('w:tcMar')):
            tcPr.remove(old)
        margins = OxmlElement('w:tcMar')
        for edge, value in (
            ('top', top), ('start', start), ('bottom', bottom), ('end', end),
        ):
            element = OxmlElement('w:%s' % edge)
            element.set(qn('w:w'), str(value))
            element.set(qn('w:type'), 'dxa')
            margins.append(element)
        tcPr.append(margins)

    @staticmethod
    def _set_cell_valign(cell, value='center'):
        tc = cell._tc
        tcPr = tc.get_or_add_tcPr()
        for old in tcPr.findall(qn('w:vAlign')):
            tcPr.remove(old)
        valign = OxmlElement('w:vAlign')
        valign.set(qn('w:val'), value)
        tcPr.append(valign)
