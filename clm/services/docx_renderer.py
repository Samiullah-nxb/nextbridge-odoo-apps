# Part of NextContract. See LICENSE file for full copyright and licensing details.
"""
DOCX Renderer (Phase 4)

Uses docxtpl to fill a Legal-admin-designed .docx base template with:
  - Resolved variables (Jinja2 {{ variable }} syntax)
  - Ordered clause content ({% for clause in section.clauses %})
  - Locked/restricted clause annotations
  - Conditional sections (already evaluated by DraftAssemblyEngine)

docxtpl documentation: https://docxtpl.readthedocs.io/

Base template design requirements (enforced as a pre-condition):
  - Headers and footers are owned by the Base DOCX (source of truth).
    Format 4 must preserve them as-is and must NEVER inject the Default
    Format (FinalContractDocxWriter) NEXTCONTRACT dual-chrome header/footer.
    Empty Base DOCX header/footer → empty generated header/footer.
  - Numbering styles must be defined in the base .docx
  - Use {{ variable_name }} for scalar variable substitution
  - Use {% for section in sections %} / {% endfor %} for clause blocks
  - Locked clause areas may use content controls in the base .docx (optional)

When either precondition is missing — docxtpl is not installed, or the template
carries no base .docx — rendering falls back to FinalContractDocxWriter, which
builds the document with python-docx from the same assembled content. That keeps
DOCX output working on a stock NextContract install instead of dead-ending in an error,
and keeps a single DOCX code path for drafts and final contracts alike.

Architecture:
    DraftAssemblyEngine → context → DocxRenderer → DOCX bytes
                                         ↳ docxtpl (base template)
                                         ↳ FinalContractDocxWriter (fallback)
"""

import base64
import io
import logging

try:
    from docxtpl import DocxTemplate
    DOCXTPL_AVAILABLE = True
except ImportError:
    DOCXTPL_AVAILABLE = False

from .final_contract_docx import FinalContractDocxWriter
from odoo.addons.clm.services.clause_set_context import (
    build_clause_set_from_sections,
)
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class DocxRenderer:
    """Renders an assembly context into a DOCX binary."""

    def render(self, context):
        """
        Accept a context dict from DraftAssemblyEngine.
        Returns raw DOCX bytes.
        """
        template = context['template']
        base_docx_version = context.get('base_docx_version')
        expects_base = bool(base_docx_version) or (
            template
            and getattr(template, 'has_base_document', False)
        )
        base_bytes = self._base_docx_bytes(
            template, base_docx_version=base_docx_version,
        )
        if expects_base:
            if not DOCXTPL_AVAILABLE:
                raise UserError(
                    "This contract uses a Document Template File, but the "
                    "'docxtpl' package is not installed on the server. "
                    "Install it with: pip install docxtpl"
                )
            if not base_bytes:
                if base_docx_version:
                    raise UserError(
                        "This contract is locked to Document Template version "
                        "'%s', but that file can no longer be read. NextContract "
                        "will not switch to another Template Version."
                        % base_docx_version.display_name
                    )
                raise UserError(
                    "Template '%s' is configured with a Document Template File, "
                    "but no readable Active version was found."
                    % (template.display_name if template else '?',)
                )
        elif not DOCXTPL_AVAILABLE or not base_bytes:
            # Format 1–3: built-in layout from assembled contract content.
            return self._render_without_base_template(context)

        # Compact Clause Collection spacing + bold titles before Jinja fill.
        from odoo.addons.clm.services.docx_tag_editor import (
            style_clause_loop_placeholders,
        )
        base_bytes = style_clause_loop_placeholders(base_bytes)

        doc = DocxTemplate(io.BytesIO(base_bytes))
        jinja_context = self._build_jinja_context(context)
        doc.render(jinja_context)

        output = io.BytesIO()
        doc.save(output)
        return self._remove_empty_paragraphs(output.getvalue())

    @staticmethod
    def _base_docx_bytes(template, base_docx_version=None, document=None):
        """Return raw bytes of the pinned or active Base DOCX, if any."""
        if base_docx_version:
            document = base_docx_version.document_id
        elif document:
            pass
        else:
            document = template.base_document_id if template else False
        if not document:
            return b''
        attachment = document.attachment_id if 'attachment_id' in document._fields else False
        # Prefer .raw with bin_size cleared — under bin_size=True, .datas is a
        # size label like b'57.25 Kb' and base64.b64decode raises Incorrect padding.
        if attachment:
            attachment = attachment.with_context(bin_size=False)
            raw = attachment.raw
            if raw:
                return raw if isinstance(raw, (bytes, bytearray)) else bytes(raw)
            from odoo.addons.clm.services.docx_tag_editor import bytes_from_b64
            decoded = bytes_from_b64(attachment.datas)
            if decoded:
                return decoded
        if 'datas' in document._fields:
            from odoo.addons.clm.services.docx_tag_editor import bytes_from_b64
            decoded = bytes_from_b64(document.with_context(bin_size=False).datas)
            if decoded:
                return decoded
        _logger.warning(
            "Template %s has Base DOCX '%s' but no file data was found.",
            template.display_name if template else '?', document.display_name,
        )
        return b''

    @staticmethod
    def _render_without_base_template(context):
        """Build the DOCX with python-docx from the assembled contract content."""
        contract = context['contract']
        content = contract._build_final_contract_content(
            version=context.get('version'), kind='draft',
        )
        # Default Format must use the same professional signature cards as
        # Preview / Generate DOCX / Generate PDF / View Final Contract.
        layout = 'classic'
        if hasattr(contract, '_clm_uses_default_docx_layout'):
            if contract._clm_uses_default_docx_layout(
                version=context.get('version'),
            ):
                layout = 'professional'
        elif not contract.template_id:
            layout = 'professional'
        return FinalContractDocxWriter(layout=layout).render(content)

    @staticmethod
    def _is_jinja_control_leftover(paragraph):
        """True for empty shells left by ``{% ... %}`` paragraphs after docxtpl.

        ``style_clause_loop_placeholders`` zeros space_before/after on Jinja
        control-tag paragraphs. Intentional Base DOCX blank spacers keep their
        original spacing (or inherit defaults as None) and must not be removed.
        """
        fmt = paragraph.paragraph_format
        before = fmt.space_before
        after = fmt.space_after
        return before == 0 and after == 0

    @staticmethod
    def _is_signature_block_spacer(paragraph, paragraphs, index):
        """True for intentional blank lines in the signature block.

        Insert Signature Block creates empty paragraphs above ``Signature:``
        (and between parties). Those often have space_before/after = 0, which
        would look like Jinja leftovers — keep them so the filled contract
        matches the Base DOCX spacing.
        """
        nxt = ''
        prev = ''
        if index + 1 < len(paragraphs):
            nxt = (paragraphs[index + 1].text or '').strip()
        if index > 0:
            prev = (paragraphs[index - 1].text or '').strip()
        if nxt.startswith('Signature:'):
            return True
        # Blank between parties (after Date, before next For) — keep.
        if nxt.startswith('For ') and prev.startswith('Date:'):
            return True
        # Do NOT keep blank after IN WITNESS — that gap looked oversized.
        return False

    @staticmethod
    def _remove_empty_paragraphs(docx_bytes):
        """Drop only Jinja control-tag leftover blanks — never Base layout spacers.

        After docxtpl fills ``{% for %}`` / ``{% endfor %}`` paragraphs, those
        paragraphs become empty. Removing *all* empty paragraphs used to collapse
        intentional blank lines (e.g. between Agreement No. and the intro).

        Preserve:
        - blank paragraphs with Base spacing / inherited defaults
        - blank paragraphs above Signature: in the signature block
        - image/logo-only paragraphs
        - page-break-only paragraphs
        """
        if not docx_bytes:
            return docx_bytes
        try:
            from docx import Document
            from docx.oxml.ns import qn
        except ImportError:
            return docx_bytes
        try:
            document = Document(io.BytesIO(docx_bytes))
        except Exception:
            return docx_bytes

        # python-docx has no paragraph.delete(); remove via XML.
        removed = 0
        paragraphs = list(document.paragraphs)
        for index, paragraph in enumerate(paragraphs):
            if (paragraph.text or '').strip():
                continue
            p = paragraph._element
            if p.xpath('.//w:br[@w:type="page"]'):
                continue
            # Keep logo / image-only paragraphs (text is empty but drawing remains).
            if (
                p.findall('.//' + qn('w:drawing'))
                or p.findall('.//' + qn('w:pict'))
                or p.findall('.//{urn:schemas-microsoft-com:vml}imagedata')
            ):
                continue
            prev = (paragraphs[index - 1].text or '').strip() if index else ''
            nxt = (
                (paragraphs[index + 1].text or '').strip()
                if index + 1 < len(paragraphs) else ''
            )
            # Drop oversized blank(s) between IN WITNESS and first For.
            # Walk back through empty neighbors to see if this gap sits under WITNESS.
            after_witness = False
            look = index - 1
            while look >= 0:
                look_text = (paragraphs[look].text or '').strip()
                if look_text.startswith('IN WITNESS'):
                    after_witness = True
                    break
                if look_text:
                    break
                look -= 1
            before_for = False
            look = index + 1
            while look < len(paragraphs):
                look_text = (paragraphs[look].text or '').strip()
                if look_text.startswith('For '):
                    before_for = True
                    break
                if look_text:
                    break
                look += 1
            if after_witness and before_for:
                parent = p.getparent()
                if parent is not None:
                    parent.remove(p)
                    removed += 1
                continue
            # Keep Title→Signature (and similar) spacers from the signature block.
            if DocxRenderer._is_signature_block_spacer(paragraph, paragraphs, index):
                continue
            # Keep intentional Base DOCX blank spacers; only strip Jinja leftovers.
            if not DocxRenderer._is_jinja_control_leftover(paragraph):
                continue
            parent = p.getparent()
            if parent is not None:
                parent.remove(p)
                removed += 1
        if not removed:
            return docx_bytes
        out = io.BytesIO()
        document.save(out)
        return out.getvalue()

    @staticmethod
    def _build_jinja_context(context):
        """
        Convert assembly context into a flat Jinja2 dict for docxtpl.

        The base .docx template can use:
          {{ contract_name }}, {{ partner_name }}, etc.  — scalar variables
          {% for section in sections %}                   — section loop
            {% for clause in section.clauses %}
              {{ clause.title }}, {{ clause.category }}, {{ clause.content }}
            {% endfor %}
          {% endfor %}
        """
        jinja_ctx = dict(context['variables'])  # all resolved scalars

        # Build plain-text clause content for DOCX (strip HTML tags).
        # Resolve any leftover {{placeholders}} inside clause wording — docxtpl
        # will not re-evaluate tags that sit inside a rendered string value.
        from .assembly_engine import DraftAssemblyEngine

        sections_for_jinja = []
        for section in context['sections']:
            clauses_for_jinja = []
            for clause in section['clauses']:
                plain_content = _strip_html(clause['content'])
                plain_content = DraftAssemblyEngine._substitute_variables(
                    plain_content, jinja_ctx,
                )
                clauses_for_jinja.append({
                    'title': clause['title'],
                    'content': plain_content,
                    'description': clause.get('description') or '',
                    'category': clause.get('category') or '',
                    'key': clause.get('code') or '',
                    'code': clause.get('code') or '',
                    'editability': clause['editability'],
                    'risk_level': clause['risk_level'] or '',
                    'required': clause['required'],
                    'is_locked': clause['editability'] == 'locked',
                    'is_restricted': clause['editability'] == 'restricted',
                })
            sections_for_jinja.append({
                'title': section['title'],
                'clauses': clauses_for_jinja,
            })

        jinja_ctx['sections'] = sections_for_jinja
        # Rebuild clause_set from already-substituted plain sections so
        # {{ clause_set.clauses.*.content }} has no leftover tags either.
        jinja_ctx['clause_set'] = build_clause_set_from_sections(
            sections_for_jinja,
            strip_html=None,
        )
        jinja_ctx['template_name'] = context['template'].name
        jinja_ctx['template_version'] = context['template'].version_number
        return jinja_ctx


def _strip_html(html):
    """Remove HTML tags, returning plain text suitable for DOCX paragraphs."""
    import html as html_lib
    import re
    if not html:
        return ''
    text = str(html)
    # Html fields may store tags entity-escaped (&lt;p&gt;...); unescape first.
    if '&lt;' in text and re.search(
        r'&lt;/?\s*(?:p|div|li|ul|ol|br|h[1-6]|span|blockquote)\b', text, re.I
    ):
        text = html_lib.unescape(text)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'&nbsp;', ' ', text)
    text = re.sub(r'&amp;', '&', text)
    text = re.sub(r'&lt;', '<', text)
    text = re.sub(r'&gt;', '>', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()
