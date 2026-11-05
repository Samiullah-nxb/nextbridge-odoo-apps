# Part of NextContract. See LICENSE file for full copyright and licensing details.
"""
Contract document rendering — the shared plumbing behind every contract export.

`clm.contract` gains:
  _build_final_contract_content(version, kind)  → canonical content dict
  _render_contract_document(fmt, version, kind) → (bytes, mimetype, filename)
  _clm_document_filename(fmt, version, kind)    → export filename

The QWeb report model feeds the same canonical content into
clm.report_clm_contract_document, so the PDF and the DOCX are two
renderings of one source.
"""

import base64
import json
import logging
import re
import unicodedata

from markupsafe import Markup, escape

from odoo import models
from odoo.exceptions import UserError

from odoo.addons.clm.services import docx_tag_editor as docx_editor

from ..services import FinalContractBuilder, FinalContractDocxWriter
from ..services.assembly_engine import DraftAssemblyEngine
from ..services.docx_renderer import DOCXTPL_AVAILABLE, DocxRenderer
from ..services.docx_to_pdf import docx_bytes_to_pdf, find_soffice
from ..services.pdf_preview import pdf_attachment_preview_html, pdf_bytes_text_layer

_logger = logging.getLogger(__name__)

_DOCX_MIME = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'

_FORMAT_MIME = {
    'pdf': 'application/pdf',
    'docx': _DOCX_MIME,
}

_REPORT_XMLID = 'clm.action_report_clm_contract_document'

# The generated document is the final contract in every case. It is never
# named "SIGNED": the signed artifact is a separate download that keeps its own
# filename, and a generated file must not be mistaken for it.
_BASENAME_SUFFIX = {
    'signed': 'FINAL',
    'approved': 'FINAL',
    'draft': 'DRAFT',
}

TERM_GROUPS = [
    {'key': 'key_terms', 'label': 'Contract Terms'},
    {'key': 'commercial_terms', 'label': 'Commercial Terms'},
    {'key': 'legal_terms', 'label': 'Legal Terms'},
    {'key': 'other_terms', 'label': 'Other Terms'},
]


class ClmContract(models.Model):
    _inherit = 'clm.contract'

    # -------------------------------------------------------------------------
    # Canonical content
    # -------------------------------------------------------------------------

    def _build_final_contract_content(self, version=None, kind='approved', snapshot=None):
        """Return the canonical content dict for this contract's document.

        With `version`, the content is that version's; with `snapshot` (a
        captured working copy), the content is exactly that snapshot.
        """
        self.ensure_one()
        return FinalContractBuilder(self, version=version, kind=kind, snapshot=snapshot).build()

    # -------------------------------------------------------------------------
    # Rendering
    # -------------------------------------------------------------------------

    def _render_contract_document(self, fmt='pdf', version=None, kind='approved'):
        """Render the contract document in `fmt`.

        Returns (bytes, mimetype, filename).

        Format 4 (Base DOCX): DOCX + PDF from the filled Document Template file.
        Format 1 (Default, no template): DOCX from FinalContractDocxWriter; PDF
        from that same DOCX via LibreOffice (QWeb only if LibreOffice is missing).
        Formats 2–3: DOCX from FinalContractDocxWriter; PDF via QWeb report.
        """
        self.ensure_one()
        if fmt not in _FORMAT_MIME:
            raise UserError(
                "Unknown contract document format '%s'. Choose 'pdf' or 'docx'." % fmt
            )
        filename = self._clm_document_filename(fmt, kind=kind, version=version)
        gen = self._clm_get_document_generation_context(version=version)

        if fmt == 'docx':
            data = self._render_contract_docx(version=version, kind=kind)
        elif gen['uses_base_docx']:
            data = self._render_contract_pdf_from_base_docx(version=version, kind=kind)
        elif gen.get('uses_default_docx_layout'):
            # Preview / DOCX / PDF share FinalContractDocxWriter output.
            data = self._render_contract_pdf_from_default_docx(
                version=version, kind=kind,
            )
        else:
            content = self._build_final_contract_content(version=version, kind=kind)
            data = self._render_contract_pdf(content)
        return data, _FORMAT_MIME[fmt], filename

    def _render_contract_docx(self, version=None, kind='approved'):
        """DOCX with locked Base DOCX when Format 4, otherwise built-in layout."""
        self.ensure_one()
        gen = self._clm_get_document_generation_context(version=version)
        base_docx_version = gen['base_docx_version']
        if gen['uses_base_docx']:
            # Format 4: fill the locked Base DOCX only. Headers/footers come
            # exclusively from that file — never FinalContractDocxWriter chrome.
            self._clm_require_base_docx_bytes(base_docx_version)
            if not DOCXTPL_AVAILABLE:
                raise UserError(
                    "This contract uses a Document Template File, but the "
                    "'docxtpl' package is not installed on the server. "
                    "Install it with: pip install docxtpl"
                )
            context = self._clm_docx_assembly_context(version=version)
            context['version'] = version
            context['base_docx_version'] = base_docx_version
            raw = DocxRenderer().render(context)
            return self._clm_docx_with_signatures(raw, version=version, kind=kind)

        content = self._build_final_contract_content(version=version, kind=kind)
        # Format 1 only: professional enterprise legal layout. Formats 2–3 keep
        # the classic built-in layout so their DOCX output is unchanged.
        # Prefer content['signature_layout'] so Preview / DOCX / PDF / Final /
        # Sign all share one decision from FinalContractBuilder.
        if content.get('signature_layout') == 'professional':
            layout = 'professional'
        else:
            layout = 'professional' if gen.get('uses_default_docx_layout') else 'classic'
        return FinalContractDocxWriter(layout=layout).render(content)

    def _clm_docx_with_signatures(self, raw, version=None, kind='approved'):
        """Optionally append e-sign / manual evidence onto Format 4 DOCX bytes.

        Skipped when ``kind`` is not signed, when there is no recorded evidence,
        or when the filled Base DOCX / uploaded file already has its own
        execution section (see ``FinalContractDocxWriter.append_signatures``).
        """
        if kind != 'signed' or not raw:
            return raw
        content = self._build_final_contract_content(version=version, kind=kind)
        if content.get('signature_evidence') == 'none':
            return raw
        return FinalContractDocxWriter().append_signatures(raw, content)

    def _render_contract_pdf_from_base_docx(self, version=None, kind='approved'):
        """PDF from the filled Base DOCX via LibreOffice (matches Word spacing).

        Always re-fills the locked Base DOCX first so PDF matches Generate DOCX
        (never converts a stale cached attachment from another path).
        """
        self.ensure_one()
        raw = self._render_contract_docx(version=version, kind=kind)
        return docx_bytes_to_pdf(raw)

    def _render_contract_pdf_from_default_docx(self, version=None, kind='approved'):
        """PDF for Default Format (no template) from the same DOCX as Generate DOCX.

        Canonical path::

            FinalContractBuilder → FinalContractDocxWriter → LibreOffice PDF

        Falls back to the QWeb report only when LibreOffice is not installed, so
        Generate PDF still works in environments without soffice.
        """
        self.ensure_one()
        raw = self._render_contract_docx(version=version, kind=kind)
        if find_soffice():
            return docx_bytes_to_pdf(raw)
        content = self._build_final_contract_content(version=version, kind=kind)
        return self._render_contract_pdf(content)

    def _clm_get_document_generation_context(self, version=None):
        """Authoritative generation decision for this contract (and optional version).

        Returns a dict::

            {
                'format': 'base_docx' | 'template_body' | 'default_template'
                          | 'default_no_template',
                'base_docx_version': clm.template.base.docx.version | empty,
                'uses_base_docx': bool,
                'uses_default_docx_layout': bool,
            }

        Preview, Generate DOCX/PDF, Document Version and Final Contract must
        all consume this decision rather than inventing their own.
        """
        self.ensure_one()
        fmt = (
            self._clm_document_generation_format()
            if hasattr(self, '_clm_document_generation_format') else 'default_no_template'
        )
        base = False
        if hasattr(self, '_clm_base_docx_version_for_render'):
            base = self._clm_base_docx_version_for_render(version=version)
        uses_base = bool(base) or fmt == 'base_docx'
        if uses_base:
            fmt = 'base_docx'
        return {
            'format': fmt,
            'base_docx_version': base,
            'uses_base_docx': uses_base,
            # Format 1 only — Preview/DOCX/PDF share FinalContractDocxWriter.
            'uses_default_docx_layout': fmt == 'default_no_template',
        }

    def _clm_uses_default_docx_layout(self, version=None):
        """True for Format 1 (no template): built-in DOCX is the layout source."""
        return self._clm_get_document_generation_context(
            version=version,
        ).get('uses_default_docx_layout')

    def _clm_pdf_from_html(self, body_html):
        """Legacy HTML→PDF path (preview-quality only; not used for Format 4 export)."""
        self.ensure_one()
        body = body_html if isinstance(body_html, str) else str(body_html or '')
        full_html = (
            '<!DOCTYPE html><html><head><meta charset="utf-8"/>'
            '<style type="text/css">'
            'html, body { margin: 0; padding: 0; background: #fff; color: #111; '
            'font-family: "Liberation Serif", "Times New Roman", Times, serif; '
            'font-size: 11pt; line-height: 1.5; }'
            'body { padding: 12mm 14mm; }'
            'p, div { margin: 0 0 0.65em; }'
            'img.o_clm_docx_img, img { max-width: 100%%; max-height: 180px; '
            'height: auto; width: auto; }'
            'table { border-collapse: collapse; width: 100%%; }'
            'td, th { border: 0.5pt solid #ccc; padding: 4px 6px; vertical-align: top; }'
            '</style></head><body>%s</body></html>'
        ) % body
        try:
            pdf = self.env['ir.actions.report'].sudo()._run_wkhtmltopdf([full_html])
        except Exception as error:
            raise UserError(
                "Could not generate PDF from HTML: %s. "
                "Check that wkhtmltopdf is installed on the Odoo server."
                % error
            ) from error
        if not pdf or not isinstance(pdf, (bytes, bytearray)) or pdf[:4] != b'%PDF':
            raise UserError(
                "The HTML PDF could not be produced "
                "(wkhtmltopdf did not return a valid PDF)."
            )
        return bytes(pdf)

    def _clm_html_from_docx_bytes(self, raw_bytes):
        """HTML preview of a .docx (paragraphs + tables, document order)."""
        self.ensure_one()
        if not raw_bytes or raw_bytes[:2] != b'PK':
            raise UserError("The document is not a valid .docx file.")
        html = docx_editor.build_ordered_document_preview_html(raw_bytes)
        if not (html or '').strip():
            return Markup('<p class="text-muted">No readable text in this DOCX.</p>')
        return Markup(html)

    def _clm_wrap_docx_preview_html(self, body_html, banner=None):
        """Wrap Base-DOCX HTML in the Version Preview shell (banner + page)."""
        self.ensure_one()
        banner = banner or {}
        banner_html = Markup('')
        if banner.get('message'):
            banner_html = Markup(
                '<div role="alert" class="alert %s o_clm_preview_banner">%s</div>'
            ) % (banner.get('css') or 'alert-info', banner['message'])
        return Markup(
            '<div class="o_clm_preview">'
            '%s'
            '<div class="o_clm_preview_page o_clm_docx_preview_body">%s</div>'
            '</div>'
        ) % (banner_html, Markup(body_html) if not isinstance(body_html, Markup) else body_html)

    def _clm_html_from_pdf_bytes(self, pdf_bytes):
        """Deprecated path — Preview must use a native PDF attachment viewer."""
        self.ensure_one()
        raise UserError(
            "PDF preview requires a stored PDF attachment for native browser rendering."
        )

    def _clm_wrap_pdf_preview_html(self, attachment, banner=None, warnings=None):
        """Native PDF viewer shell for the given generated PDF attachment.

        Preview and Download share this attachment — the iframe loads the same
        ``/web/content/<id>`` URL the download serves.
        """
        self.ensure_one()
        if not attachment:
            raise UserError("No PDF attachment available for preview.")
        attachment.ensure_one()
        banner = banner or {}
        parts = [Markup('<div class="o_clm_preview o_clm_preview_pdf">')]
        if banner.get('message'):
            parts.append(Markup(
                '<div role="alert" class="alert %s o_clm_preview_banner">%s</div>'
            ) % (banner.get('css') or 'alert-info', banner['message']))
        if warnings:
            items = Markup('').join(
                Markup('<li>%s</li>') % warning for warning in warnings
            )
            parts.append(Markup(
                '<div class="alert alert-warning o_clm_preview_warnings" role="alert">'
                '<strong>Consistency checks</strong><ul>%s</ul></div>'
            ) % items)
        cache_key = attachment.checksum or attachment.id
        if attachment.write_date:
            cache_key = '%s-%s' % (cache_key, int(attachment.write_date.timestamp()))
        if not attachment.access_token:
            attachment.generate_access_token()
        parts.append(pdf_attachment_preview_html(
            attachment.id,
            filename=attachment.name or 'document.pdf',
            cache_key=cache_key,
            access_token=attachment.access_token,
        ))
        # Searchable text for accessibility / tests — not shown visually.
        try:
            raw = attachment.with_context(bin_size=False).raw
            if not raw and attachment.datas:
                datas = attachment.datas
                if isinstance(datas, str):
                    datas = datas.encode('ascii')
                raw = base64.b64decode(datas, validate=False)
            text = pdf_bytes_text_layer(raw) if raw else ''
            if text:
                parts.append(Markup(
                    '<div class="o_clm_pdf_text_layer">%s</div>'
                ) % escape(text))
        except Exception:  # noqa: BLE001
            _logger.debug('NextContract: PDF text layer skipped', exc_info=True)
        parts.append(Markup('</div>'))
        return Markup('').join(parts)

    def _clm_attachment_raw_bytes(self, attachment):
        """Read attachment bytes even when ``datas`` is oddly encoded."""
        attachment = attachment.sudo()
        raw = attachment.with_context(bin_size=False).raw or b''
        if not raw and attachment.datas:
            datas = attachment.datas
            if isinstance(datas, str):
                datas = datas.encode('ascii')
            try:
                raw = base64.b64decode(datas, validate=False)
            except Exception:
                raw = b''
        return raw

    def _clm_prepare_attachment_for_pdf_preview(self, attachment):
        """Return a PDF attachment, converting Word (.doc/.docx) when needed."""
        attachment = attachment.sudo()
        raw = self._clm_attachment_raw_bytes(attachment)
        if raw.startswith(b'%PDF'):
            return attachment

        from odoo.addons.clm.services.docx_to_pdf import (
            guess_word_extension,
            word_bytes_to_pdf,
        )

        name = attachment.name or ''
        if not guess_word_extension(raw, filename=name):
            raise UserError(
                'Preview is only available for PDF or Word (.doc / .docx) '
                'documents. Use Download for this file format.'
            )

        pdf_bytes = word_bytes_to_pdf(raw, filename=name)
        base = name.rsplit('.', 1)[0] if name else 'document'
        safe = ''.join(ch if ch.isalnum() or ch in '-_' else '_' for ch in base)[:80]
        pdf_name = 'CLM_Preview__%s.pdf' % (safe or 'document')
        Attachment = self.env['ir.attachment'].sudo()
        existing = Attachment.search([
            ('res_model', '=', 'clm.contract'),
            ('res_id', '=', self.id),
            ('name', '=', pdf_name),
        ], limit=1)
        vals = {
            'name': pdf_name,
            'raw': pdf_bytes,
            'mimetype': 'application/pdf',
            'res_model': 'clm.contract',
            'res_id': self.id,
            'description': 'PDF preview converted from %s' % (name or attachment.id),
        }
        if existing:
            existing.write(vals)
            return existing
        return Attachment.create(vals)

    def _clm_action_open_pdf_preview(
        self, attachment, title='PDF Preview', banner=None, warnings=None,
        version=None,
    ):
        """Open the native in-app PDF viewer for an attachment (Preview = Download).

        Word uploads are converted to PDF automatically so View Signed PDF works
        after a manual .doc / .docx signature upload.
        """
        self.ensure_one()
        self.check_access('read')
        if not attachment:
            raise UserError('No PDF is available for preview.')
        attachment = self._clm_prepare_attachment_for_pdf_preview(attachment)
        attachment.generate_access_token()
        self.env.flush_all()
        wizard = self.env['clm.pdf.preview.wizard'].create({
            'contract_id': self.id,
            'attachment_id': attachment.id,
            'version_id': version.id if version else False,
            'banner_message': (banner or {}).get('message') or '',
            'banner_css': (banner or {}).get('css') or 'alert-info',
            'warnings_json': json.dumps(list(warnings or [])),
            'show_create_version': bool(
                self.env.context.get('clm_pdf_preview_show_create_version')
            ),
        })
        view = self.env.ref('clm.view_clm_pdf_preview_wizard_form')
        return {
            'type': 'ir.actions.act_window',
            'name': title,
            'res_model': 'clm.pdf.preview.wizard',
            'res_id': wizard.id,
            'view_mode': 'form',
            'views': [(view.id, 'form')],
            'target': 'new',
            'context': {'dialog_size': 'extra-large'},
        }

    def _clm_uses_base_docx_preview(self, version=None):
        """True when Preview / PDF must use the locked Base DOCX path."""
        self.ensure_one()
        return self._clm_get_document_generation_context(version=version)['uses_base_docx']

    def _clm_docx_assembly_context(self, version=None):
        """Assembly context for docxtpl, preferring a version content snapshot.

        Snapshots keep raw ``{{placeholders}}`` in clause wording (so the
        editor can still show tags). DOCX export must resolve those tags the
        same way live assembly does, or the Word file ships with unfilled tags.
        """
        self.ensure_one()
        template = self.template_id
        snapshot = (
            version.sudo().content_snapshot
            if version and 'content_snapshot' in version._fields else None
        )
        if snapshot:
            from odoo.addons.clm.services.clause_set_context import (
                clause_category_label,
            )
            engine = DraftAssemblyEngine(template, self)
            variables = dict(engine._resolve_variables())
            # Prefer frozen snapshot values when present (version fidelity).
            for key, value in (snapshot.get('variables') or {}).items():
                if value not in (None, ''):
                    variables[key] = value
            sections = []
            current = None
            for clause in snapshot.get('clauses') or []:
                section_title = clause.get('section') or ''
                if current is None or current['title'] != section_title:
                    current = {'title': section_title, 'clauses': []}
                    sections.append(current)
                content = DraftAssemblyEngine._substitute_variables(
                    clause.get('content') or '', variables,
                )
                category = (clause.get('category') or '').strip()
                # Older snapshots omitted category — resolve from the library
                # clause so {{ clause.category }} still fills in Base DOCX.
                if not category and clause.get('clause_id'):
                    lib = self.env['clm.clause'].browse(clause['clause_id']).exists()
                    if lib:
                        category = clause_category_label(lib) or ''
                current['clauses'].append({
                    'title': clause.get('title') or '',
                    'content': content,
                    'description': clause.get('description') or '',
                    'category': category,
                    'code': clause.get('code') or '',
                    'editability': 'editable',
                    'risk_level': '',
                    'required': bool(clause.get('required')),
                })
            return {
                'contract': self,
                'template': template,
                'variables': variables,
                'sections': sections,
                'version': version,
            }
        return DraftAssemblyEngine(template, self).assemble()

    def _render_contract_pdf(self, content):
        """Render the canonical content through Odoo's own QWeb/wkhtmltopdf engine."""
        self.ensure_one()
        report = self.env.ref(_REPORT_XMLID, raise_if_not_found=False)
        if not report:
            raise UserError(
                "The contract document report is not installed. "
                "Update the 'NextContract Draft Assembly' module and try again."
            )
        # force_report_rendering: Odoo quietly returns HTML instead of PDF while
        # tests are running. A contract export must be a real PDF or an error —
        # never HTML bytes wearing a .pdf name.
        pdf, report_type = report.sudo().with_context(
            force_report_rendering=True,
            clm_force_qweb_contract_pdf=True,
        )._render_qweb_pdf(
            _REPORT_XMLID,
            res_ids=self.ids,
            data={'clm_contract_contents': {self.id: content}},
        )
        if report_type != 'pdf':
            raise UserError(
                "The contract PDF could not be produced (the report engine "
                "returned '%s'). Check that wkhtmltopdf is installed on the "
                "Odoo server." % report_type
            )
        return pdf

    # -------------------------------------------------------------------------
    # Naming
    # -------------------------------------------------------------------------

    def _clm_document_basename(self, kind='approved', version=None):
        """e.g. CNT-2026-00021_Acme_Services_Agreement_V2_FINAL (no extension).

        The version number keeps each version's files apart: generating v2
        never replaces the file of v1. A version with frozen content is named
        after its own title, so renaming the contract later does not rename it.

        Also used as the report's `print_report_name`, which appends the
        extension itself.
        """
        self.ensure_one()
        snapshot = (
            version.sudo().content_snapshot
            if version and 'content_snapshot' in version._fields else None
        ) or {}
        reference = snapshot.get('reference') or self.contract_ref
        title = snapshot.get('title') or self.name
        parts = []
        if reference and reference != 'New':
            parts.append(self._slugify(reference))
        if title:
            parts.append(self._slugify(title))
        if version:
            parts.append('V%s' % version.version_number)
        parts.append(_BASENAME_SUFFIX.get(kind, 'FINAL'))
        return '_'.join(part for part in parts if part) or 'Contract'

    def _clm_document_filename(self, fmt='pdf', kind='approved', version=None):
        """e.g. CNT-2026-00021_Acme_Services_Agreement_V2_FINAL.pdf"""
        return '%s.%s' % (self._clm_document_basename(kind=kind, version=version), fmt)

    @staticmethod
    def _slugify(value):
        """ASCII, underscore-joined — safe in a Content-Disposition header.

        Hyphens are kept so a contract reference stays readable as
        CNT-2026-00021 rather than CNT_2026_00021.
        """
        normalised = unicodedata.normalize('NFKD', value or '')
        ascii_value = normalised.encode('ascii', 'ignore').decode('ascii')
        return re.sub(r'_+', '_', re.sub(r'[^A-Za-z0-9-]+', '_', ascii_value)).strip('_-')


class ReportClmContractDocument(models.AbstractModel):
    _name = 'report.clm.report_clm_contract_document'
    _description = 'Contract Document Report'

    def _get_report_values(self, docids, data=None):
        contracts = self.env['clm.contract'].browse(docids)
        # Callers that already built the content pass it in, so the document is
        # rendered from exactly the content the DOCX was rendered from. Printing
        # the report straight from the UI falls back to building it here.
        supplied = (data or {}).get('clm_contract_contents') or {}
        contents = {
            contract.id: supplied.get(contract.id) or contract._build_final_contract_content()
            for contract in contracts
        }
        return {
            'doc_ids': docids,
            'doc_model': 'clm.contract',
            'docs': contracts,
            'contents': contents,
            'term_groups': TERM_GROUPS,
        }


class IrActionsReportClmDocument(models.Model):
    """Route Format 4 Print through the locked Base DOCX → LibreOffice PDF path."""

    _inherit = 'ir.actions.report'

    def _render_qweb_pdf(self, report_ref, res_ids=None, data=None):
        report = self._get_report(report_ref)
        if (
            report.report_name == 'clm.report_clm_contract_document'
            and res_ids
            and not self.env.context.get('clm_force_qweb_contract_pdf')
        ):
            if isinstance(res_ids, int):
                res_ids = [res_ids]
            contracts = self.env['clm.contract'].browse(res_ids)
            if len(contracts) == 1 and contracts._clm_uses_base_docx_preview():
                version = False
                kind = 'draft'
                if hasattr(contracts, '_get_final_contract_version'):
                    final_v, final_kind = contracts._get_final_contract_version()
                    if final_v:
                        version = final_v
                        kind = final_kind or 'draft'
                if not version and hasattr(contracts, '_clm_current_version'):
                    version = contracts._clm_current_version()
                pdf = contracts._render_contract_pdf_from_base_docx(
                    version=version or None,
                    kind=kind,
                )
                return pdf, 'pdf'
            if len(contracts) == 1 and contracts._clm_uses_default_docx_layout():
                version = False
                kind = 'draft'
                if hasattr(contracts, '_get_final_contract_version'):
                    final_v, final_kind = contracts._get_final_contract_version()
                    if final_v:
                        version = final_v
                        kind = final_kind or 'draft'
                if not version and hasattr(contracts, '_clm_current_version'):
                    version = contracts._clm_current_version()
                pdf = contracts._render_contract_pdf_from_default_docx(
                    version=version or None,
                    kind=kind,
                )
                return pdf, 'pdf'
        return super()._render_qweb_pdf(report_ref, res_ids=res_ids, data=data)
