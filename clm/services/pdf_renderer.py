# Part of NextContract. See LICENSE file for full copyright and licensing details.
"""
PDF Renderer (Phase 4)

Uses WeasyPrint to convert the assembled HTML contract into a PDF.

WeasyPrint consumes the HTML content stored in clm.template.content and
clm.clause.standard_wording natively — no intermediate format conversion needed.

WeasyPrint is optional. When it is not installed, rendering falls back to Odoo's
own QWeb / wkhtmltopdf report engine (clm.report_clm_contract_document),
which ships with every Odoo deployment. Both paths render the same assembled
contract, so PDF output works on a stock NextContract install rather than dead-ending in
an error.

Installation requirement (optional, for the WeasyPrint path):
  pip install weasyprint
  System packages: libpango-1.0-0, libcairo2 (see WeasyPrint docs)

Architecture:
    DraftAssemblyEngine → context → PdfRenderer → PDF bytes
                                        ↳ WeasyPrint (when installed)
                                        ↳ QWeb / wkhtmltopdf (fallback)
"""

try:
    import weasyprint
    WEASYPRINT_AVAILABLE = True
except ImportError:
    WEASYPRINT_AVAILABLE = False

# Base CSS for contract documents
_CONTRACT_CSS = """
@page {
    size: A4;
    margin: 2.5cm 2cm 2.5cm 2.5cm;
    @top-center { content: string(contract-title); font-size: 9pt; color: #666; }
    @bottom-right { content: "Page " counter(page) " of " counter(pages); font-size: 9pt; color: #666; }
}

body {
    font-family: 'Times New Roman', Times, serif;
    font-size: 11pt;
    line-height: 1.5;
    color: #1a1a1a;
}

h1.contract-title {
    string-set: contract-title content();
    font-size: 16pt;
    text-align: center;
    margin-bottom: 2em;
    border-bottom: 2px solid #333;
    padding-bottom: 0.5em;
}

h2.section-title {
    font-size: 13pt;
    font-weight: bold;
    margin-top: 2em;
    margin-bottom: 0.5em;
    border-bottom: 1px solid #ccc;
    page-break-after: avoid;
}

h3.clause-title {
    font-size: 11pt;
    font-weight: bold;
    margin-top: 1em;
    margin-bottom: 0.3em;
    page-break-after: avoid;
}

.clause-content { margin-bottom: 1em; }

.lock-notice {
    font-size: 9pt;
    color: #c00;
    font-style: italic;
    border-left: 3px solid #c00;
    padding-left: 0.5em;
    margin-bottom: 0.5em;
}

.restricted-notice {
    font-size: 9pt;
    color: #e65c00;
    font-style: italic;
    border-left: 3px solid #e65c00;
    padding-left: 0.5em;
    margin-bottom: 0.5em;
}

.optional-label {
    font-size: 9pt;
    color: #888;
    font-style: italic;
}

.variable-table { width: 100%; border-collapse: collapse; margin-bottom: 2em; font-size: 10pt; }
.variable-table td { padding: 4px 8px; border: 1px solid #ddd; }
.variable-table td:first-child { font-weight: bold; width: 40%; }

.draft-watermark {
    position: fixed;
    top: 40%;
    left: 10%;
    font-size: 72pt;
    color: rgba(200,200,200,0.3);
    transform: rotate(-45deg);
    z-index: -1;
}
"""


class PdfRenderer:
    """
    Renders an assembly context into a PDF binary using WeasyPrint.

    The HTML is built directly from the template content and clause HTML fields,
    preserving all formatting without intermediate conversion.
    """

    def render(self, context):
        """
        Accept a context dict from DraftAssemblyEngine.
        Returns raw PDF bytes.
        """
        contract = context['contract']
        version = context.get('version')
        # Format 4: PDF must match the Base DOCX, not the QWeb cover sheet.
        gen = (
            contract._clm_get_document_generation_context(version=version)
            if hasattr(contract, '_clm_get_document_generation_context') else {}
        )
        if context.get('base_docx_version') or gen.get('uses_base_docx'):
            return contract._render_contract_pdf_from_base_docx(
                version=version, kind='draft',
            )

        if not WEASYPRINT_AVAILABLE:
            return self._render_with_qweb(context)

        html_content = self._build_html(context)
        css = weasyprint.CSS(string=_CONTRACT_CSS)
        pdf_bytes = weasyprint.HTML(string=html_content).write_pdf(stylesheets=[css])
        return pdf_bytes

    @staticmethod
    def _render_with_qweb(context):
        """Render through Odoo's built-in report engine (no extra dependency)."""
        contract = context['contract']
        content = contract._build_final_contract_content(version=context.get('version'), kind='draft')
        return contract._render_contract_pdf(content)

    def _build_html(self, context):
        """Build a full HTML document from the assembly context."""
        contract = context['contract']
        template = context['template']
        variables = context['variables']
        sections = context['sections']

        parts = []
        parts.append('<!DOCTYPE html><html><head><meta charset="utf-8"/></head><body>')
        parts.append(f'<div class="draft-watermark">DRAFT</div>')

        # Title
        parts.append(f'<h1 class="contract-title">{contract.name}</h1>')

        # Variable summary table
        if variables:
            parts.append('<table class="variable-table">')
            for k, v in variables.items():
                parts.append(f'<tr><td>{k.replace("_", " ").title()}</td><td>{v or "—"}</td></tr>')
            parts.append('</table>')

        # Sections and clauses
        for section in sections:
            parts.append(f'<h2 class="section-title">{section["title"]}</h2>')
            for clause in section['clauses']:
                optional_label = '' if clause['required'] else ' <span class="optional-label">(Optional)</span>'
                parts.append(f'<h3 class="clause-title">{clause["title"]}{optional_label}</h3>')

                if clause['editability'] == 'locked':
                    parts.append(
                        '<p class="lock-notice">⚠ LOCKED CLAUSE — '
                        'Modification requires Legal Admin authorisation and creates an audit event.</p>'
                    )
                elif clause['editability'] == 'restricted':
                    parts.append(
                        '<p class="restricted-notice">⚠ RESTRICTED CLAUSE — Legal Administrators only.</p>'
                    )

                parts.append(f'<div class="clause-content">{clause["content"] or ""}</div>')

        # Footer note
        parts.append(
            '<hr/><p style="font-size:9pt;color:#999;text-align:center;">'
            f'Generated from template: {template.name} v{template.version_number} | '
            'Phase 4 PDF Output'
            '</p>'
        )
        parts.append('</body></html>')
        return '\n'.join(parts)
