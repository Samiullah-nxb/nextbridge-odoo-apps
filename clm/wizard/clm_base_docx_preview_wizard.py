# Part of NextContract. See LICENSE file for full copyright and licensing details.

import base64
import logging
from urllib.parse import quote

from markupsafe import Markup, escape
from odoo import api, fields, models
from odoo.exceptions import UserError

from odoo.addons.clm.services import docx_tag_editor as editor
from odoo.addons.clm.services.docx_to_pdf import docx_bytes_to_pdf, find_soffice
from odoo.addons.clm.services.pdf_preview import (
    pdf_attachment_preview_html,
    pdf_bytes_text_layer,
)

_logger = logging.getLogger(__name__)


class ClmBaseDocxPreviewWizard(models.TransientModel):
    """Read-only modal: Document Template Version as native PDF.js preview.

    Same viewer shell as contract-side Preview (Fullscreen, 100% zoom, open in
    tab). The DOCX is converted with LibreOffice when available; otherwise the
    older HTML extract is shown so preview still works without soffice.
    """

    _name = 'clm.base.docx.preview.wizard'
    _description = 'Document Template Preview'

    template_id = fields.Many2one('clm.template', required=True, ondelete='cascade')
    document_id = fields.Many2one('documents.document', required=True, readonly=True)
    kind = fields.Selection(
        [
            ('original', 'Original'),
            ('previous_changed', 'Change'),
        ],
        required=True,
        readonly=True,
    )
    pdf_attachment_id = fields.Many2one('ir.attachment', readonly=True, copy=False)
    # Set at create from the Template Version label so the dialog title matches
    # the version row even when the Documents file still has an older name.
    file_name = fields.Char(readonly=True)
    title = fields.Char(compute='_compute_preview', readonly=True)
    preview_html = fields.Html(compute='_compute_preview', sanitize=False, readonly=True)

    @api.depends('file_name', 'document_id.name')
    def _compute_display_name(self):
        for wiz in self:
            wiz.display_name = 'Preview(%s)' % wiz._preview_file_name()

    def _preview_file_name(self):
        self.ensure_one()
        if self.file_name:
            return self.file_name
        if self.document_id:
            return self.document_id.name or 'document.docx'
        return 'document.docx'

    @api.depends(
        'file_name',
        'document_id',
        'document_id.name',
        'document_id.attachment_id',
        'document_id.attachment_id.datas',
        'pdf_attachment_id',
        'pdf_attachment_id.datas',
        'kind',
    )
    def _compute_preview(self):
        for wiz in self:
            doc = wiz.document_id
            file_name = wiz._preview_file_name()
            # Keep aligned with the dialog title (action name / display_name).
            wiz.title = 'Preview(%s)' % file_name
            wiz.preview_html = wiz._build_preview_html(doc)

    def _docx_bytes(self, document):
        if not document:
            return b''
        attachment = document.attachment_id.with_context(bin_size=False)
        if not attachment or not attachment.datas:
            return b''
        return editor.bytes_from_b64(attachment.datas) or b''

    def prepare_pdf_preview(self):
        """Convert the Document Template DOCX to a PDF attachment for PDF.js."""
        self.ensure_one()
        if self.pdf_attachment_id:
            return self.pdf_attachment_id
        raw = self._docx_bytes(self.document_id)
        if not raw or raw[:2] != b'PK':
            return self.env['ir.attachment']
        if not find_soffice():
            return self.env['ir.attachment']
        try:
            pdf_bytes = docx_bytes_to_pdf(raw)
        except Exception:  # noqa: BLE001
            _logger.warning(
                'Base DOCX preview: LibreOffice conversion failed for document %s',
                self.document_id.id, exc_info=True,
            )
            return self.env['ir.attachment']
        base_name = (self.document_id.name or 'document_template.docx').rsplit('.', 1)[0]
        attachment = self.env['ir.attachment'].sudo().create({
            'name': '%s.pdf' % base_name,
            'type': 'binary',
            'datas': base64.b64encode(pdf_bytes),
            'mimetype': 'application/pdf',
            'res_model': self._name,
            'res_id': self.id,
        })
        attachment.generate_access_token()
        self.pdf_attachment_id = attachment.id
        return attachment

    def _build_preview_html(self, document):
        if not document:
            return Markup('<p class="text-muted">No document available.</p>')
        attachment = document.attachment_id.with_context(bin_size=False)
        if not attachment or not attachment.datas:
            return Markup(
                '<p class="text-muted">This Documents record has no file attachment.</p>'
            )
        try:
            raw = editor.bytes_from_b64(attachment.datas)
            if not raw:
                return Markup('<p class="text-muted">The attachment is empty.</p>')
            if raw[:2] != b'PK':
                return Markup(
                    '<p class="text-danger">Could not preview this file: '
                    'the attachment is not a valid .docx (ZIP) file.</p>'
                )
            pdf_att = self.pdf_attachment_id
            if pdf_att:
                return self._wrap_pdf_preview_html(pdf_att)
            # No LibreOffice / conversion failed — readable HTML fallback.
            preview = editor.extract_document_preview(raw)
            html = editor.build_full_preview_html(
                headers=preview.get('headers') or [],
                paragraphs=preview.get('paragraphs') or [],
            )
            if not (html or '').strip():
                return Markup('<p class="text-muted">No readable text in this DOCX.</p>')
            # No banner — modal title already names the file.
            return Markup(
                '<div class="o_clm_preview o_clm_preview_html">'
                '<div class="o_clm_base_docx_preview_body">%s</div></div>'
            ) % (Markup(html) if not isinstance(html, Markup) else html)
        except Exception as exc:
            return Markup(
                '<p class="text-danger">Could not preview this file: %s</p>'
            ) % escape(str(exc))

    def _wrap_pdf_preview_html(self, attachment):
        """Same native PDF.js shell used on the contract side."""
        self.ensure_one()
        attachment = attachment.sudo()
        if not attachment.access_token:
            attachment.generate_access_token()
        parts = [Markup('<div class="o_clm_preview o_clm_preview_pdf">')]
        cache_key = attachment.checksum or attachment.id
        if attachment.write_date:
            cache_key = '%s-%s' % (cache_key, int(attachment.write_date.timestamp()))
        parts.append(pdf_attachment_preview_html(
            attachment.id,
            filename=attachment.name or 'document.pdf',
            cache_key=cache_key,
            access_token=attachment.access_token,
        ))
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
            _logger.debug('Base DOCX preview: PDF text layer skipped', exc_info=True)
        parts.append(Markup('</div>'))
        return Markup('').join(parts)

    def action_download(self):
        """Download the original Document Template DOCX."""
        self.ensure_one()
        document = self.document_id
        if not document:
            raise UserError("No document to download.")
        attachment = document.attachment_id
        if not attachment:
            raise UserError("This Documents record has no file attachment.")
        filename = attachment.name or document.name or 'document_template.docx'
        if not str(filename).lower().endswith('.docx'):
            filename = '%s.docx' % filename
        return {
            'type': 'ir.actions.act_url',
            'url': '/web/content/%s?download=true&filename=%s' % (
                attachment.id, quote(str(filename)),
            ),
            'target': 'self',
        }

    def action_download_pdf(self):
        """Download the LibreOffice PDF used for preview (when available)."""
        self.ensure_one()
        attachment = self.pdf_attachment_id
        if not attachment:
            raise UserError(
                "No PDF preview is available. Install LibreOffice on the server "
                "or use Download for the Word file."
            )
        filename = attachment.name or 'document_template.pdf'
        return {
            'type': 'ir.actions.act_url',
            'url': '/web/content/%s?download=true&filename=%s' % (
                attachment.id, quote(str(filename)),
            ),
            'target': 'self',
        }
