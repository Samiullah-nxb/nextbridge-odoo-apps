# Part of NextContract. See LICENSE file for full copyright and licensing details.
"""
Versioned contract content.

A contract version created through the contract editor (Generate Draft, Create
New Version, an amendment) freezes what the contract says — terms, parties,
template body, clause wording and placeholder values — in `content_snapshot`.
The snapshot is written once, when the version is created, and can never be
changed afterwards, not even through sudo: editing the contract always produces
a new version.

Every document of a version — preview, DOCX, PDF, comparison — is rendered from
its snapshot. Generated files are kept per version (the version number is part
of the file name), so generating Version 2 never replaces Version 1's files.
"""

import base64
import io
import logging
import re

from markupsafe import Markup

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools.misc import format_datetime

from ..services import FinalContractBuilder

_logger = logging.getLogger(__name__)

_DOCUMENT_FORMATS = ('docx', 'pdf')


class ClmContractVersionContent(models.Model):
    _inherit = 'clm.contract.version'

    content_snapshot = fields.Json(
        string='Content Snapshot', readonly=True, copy=False,
        help='The contract content frozen when this version was created: terms, '
             'parties, template body, clause wording and placeholder values.'
    )
    base_docx_version_id = fields.Many2one(
        'clm.template.base.docx.version',
        string='Base DOCX Version',
        readonly=True,
        copy=False,
        ondelete='restrict',
        index=True,
        help='Exact Document Template File version pinned when this contract '
             'version was created. Regenerating DOCX / PDF always uses this '
             'file, not the template\'s current Active version.',
    )
    has_content_snapshot = fields.Boolean(
        string='Content Captured', compute='_compute_has_content_snapshot', store=True,
        help='This version carries its own frozen contract content. Its preview, '
             'DOCX and PDF are rendered from that content, never from the live contract.'
    )
    content_preview_html = fields.Html(
        string='Version Preview', compute='_compute_content_preview_html', sanitize=False,
    )
    generated_docx_id = fields.Many2one(
        'ir.attachment', string='Generated DOCX', readonly=True, copy=False, ondelete='set null',
    )
    generated_pdf_id = fields.Many2one(
        'ir.attachment', string='Generated PDF', readonly=True, copy=False, ondelete='set null',
    )
    is_signed_version = fields.Boolean(
        string='Signed', compute='_compute_is_signed_version',
        help='Signed versions, and the drafts they were signed from, are locked.'
    )

    @api.depends('content_snapshot')
    def _compute_has_content_snapshot(self):
        for version in self:
            version.has_content_snapshot = bool(version.content_snapshot)

    @api.depends('version_type')
    def _compute_is_signed_version(self):
        for version in self:
            version.is_signed_version = version.version_type == 'executed'

    def _compute_content_preview_html(self):
        """Preview is opened via ``action_preview_version`` (PDF wizard), not on form load.

        Eager PDF generation during compute left empty ``ir.attachment`` rows and
        broke iframe preview after Generate Draft.
        """
        for version in self:
            version.content_preview_html = False

    # -------------------------------------------------------------------------
    # Immutability
    # -------------------------------------------------------------------------

    def write(self, vals):
        if 'content_snapshot' in vals:
            frozen = self.filtered('content_snapshot')
            if frozen:
                raise UserError(
                    "Version %s is frozen: its content was captured when it was created "
                    "and cannot be changed. Edit the contract and create a new version "
                    "instead." % ', '.join('v%s' % v.version_number for v in frozen)
                )
        if 'base_docx_version_id' in vals:
            frozen = self.filtered('base_docx_version_id')
            if frozen:
                raise UserError(
                    "Version %s already has a pinned Document Template File and "
                    "cannot change it."
                    % ', '.join('v%s' % v.version_number for v in frozen)
                )
        return super().write(vals)

    # -------------------------------------------------------------------------
    # Content
    # -------------------------------------------------------------------------

    def _clm_document_kind(self):
        """Which document this version renders as: 'signed', 'approved' or 'draft'."""
        self.ensure_one()
        return 'signed' if self.version_type == 'executed' else 'draft'

    def _clm_content_lines(self):
        """This version's contract content as plain lines (used by redlines)."""
        self.ensure_one()
        content = self.contract_id._build_final_contract_content(version=self, kind='draft')
        return FinalContractBuilder.content_lines(content)

    def _clm_render_version_preview(self):
        """Preview = native PDF viewer of the same attachment as Download PDF.

        All four generation formats materialise one final PDF attachment via
        ``_clm_generate_document('pdf')``, then the browser renders it.
        """
        self.ensure_one()
        contract = self.contract_id
        type_label = dict(
            self._fields['version_type']._description_selection(self.env)
        ).get(self.version_type, '')
        banner = self._clm_version_preview_banner(type_label)
        try:
            # Same attachment Download PDF will serve.
            attachment = self._clm_generate_document('pdf')
            if not self._clm_attachment_has_payload(attachment, 'pdf'):
                raise UserError(
                    'The generated PDF has no file data. Try Download PDF, '
                    'or regenerate the draft and open Preview again.'
                )
            content = contract.sudo()._build_final_contract_content(
                version=self, kind=self._clm_document_kind(),
            )
            return contract._clm_wrap_pdf_preview_html(
                attachment, banner, warnings=content.get('warnings') or [],
            )
        except Exception as error:  # noqa: BLE001
            _logger.warning(
                'NextContract: PDF preview of version %s failed: %s',
                self.version_number, error,
            )
            return Markup(
                '<div class="o_clm_preview">'
                '<div role="alert" class="alert alert-warning o_clm_preview_banner">%s</div>'
                '<div class="alert alert-danger" role="alert">%s</div>'
                '</div>'
            ) % (
                banner.get('message') or '',
                'Could not build the PDF preview: %s' % error,
            )

    def _clm_version_preview_banner(self, type_label):
        self.ensure_one()
        if self.content_snapshot:
            captured = self.content_snapshot.get('captured_at')
            when = format_datetime(self.env, fields.Datetime.to_datetime(captured)) if captured else ''
            message = (
                'Version v%s (%s). Its content was frozen when the version was created%s; '
                'later edits to the contract do not change it.'
            ) % (self.version_number, type_label, ' on %s' % when if when else '')
            css = 'alert-info'
        else:
            message = (
                'Version v%s (%s) was created before contract content was frozen on '
                'versions: its wording comes from its document, and its terms are '
                'shown as currently recorded on the contract.'
            ) % (self.version_number, type_label)
            css = 'alert-secondary'
        if self.is_signed_version:
            message += ' This version is signed and locked.'
            css = 'alert-warning'
        message = (
            '%s Preview shows the final PDF document (same file as Download PDF).'
            % message
        ).strip()
        return {'css': css, 'message': message}

    def _clm_preview_docx_attachment(self):
        """DOCX used for on-screen preview: Documents link, else Generated DOCX."""
        self.ensure_one()
        attachment = self._clm_docx_attachment()
        if attachment:
            return attachment
        generated = self.generated_docx_id
        if not generated:
            return self.env['ir.attachment']
        mime = (generated.mimetype or '')
        name = (generated.name or '').lower()
        if 'wordprocessingml.document' in mime or name.endswith('.docx'):
            return generated.with_context(bin_size=False)
        return self.env['ir.attachment']

    def _clm_render_base_docx_preview(self, banner):
        """HTML from the version's locked Base DOCX (same fill as Generate DOCX)."""
        self.ensure_one()
        contract = self.contract_id
        # Always refill from the locked Base DOCX so Preview matches Generate DOCX
        # (do not serve a stale attachment produced by another renderer).
        raw = contract._render_contract_docx(
            version=self, kind=self._clm_document_kind(),
        )
        body = contract._clm_html_from_docx_bytes(raw)
        return contract._clm_wrap_docx_preview_html(body, banner)

    def _clm_docx_attachment(self):
        self.ensure_one()
        attachment = self.document_id.attachment_id if self.document_id else False
        if not attachment:
            return self.env['ir.attachment']
        if 'wordprocessingml.document' not in (attachment.mimetype or ''):
            return self.env['ir.attachment']
        return attachment.with_context(bin_size=False)

    def _clm_attachment_bytes(self, attachment):
        """Raw file bytes from an ir.attachment (never the bin_size display string)."""
        attachment.ensure_one()
        # Prefer .raw — Binary.datas may be b'24.57 Kb' when bin_size is in the context.
        raw = attachment.with_context(bin_size=False).raw
        if raw:
            return raw if isinstance(raw, (bytes, bytearray)) else bytes(raw)
        datas = attachment.with_context(bin_size=False).datas
        if not datas:
            raise UserError("Attachment '%s' has no file data." % attachment.name)
        if isinstance(datas, bytes) and datas[:2] == b'PK':
            return datas
        if isinstance(datas, str):
            datas = datas.encode('ascii')
        try:
            return base64.b64decode(datas, validate=False)
        except Exception as error:
            raise UserError(
                "Could not read attachment '%s': %s" % (attachment.name, error)
            ) from error

    def _clm_extract_docx_plain_text(self):
        """Plain text from this version's uploaded DOCX (paragraphs + tables)."""
        self.ensure_one()
        try:
            import docx
        except ImportError as error:
            raise UserError(
                "Reading DOCX requires the 'python-docx' library."
            ) from error
        attachment = self._clm_docx_attachment()
        if not attachment:
            raise UserError("Version %s has no DOCX document attached." % self.version_number)
        try:
            document = docx.Document(io.BytesIO(self._clm_attachment_bytes(attachment)))
        except Exception as error:  # BadZipFile, PackageNotFoundError, KeyError ...
            raise UserError(
                "Version %s is not a readable Word (.docx) document." % self.version_number
            ) from error
        lines = [p.text.strip() for p in document.paragraphs if p.text.strip()]
        for table in document.tables:
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                if cells:
                    lines.append(' | '.join(cells))
        return '\n'.join(lines)

    def _clm_term_overrides_from_docx_text(self, text):
        """Detect notice-period edits in an uploaded DOCX (cover table or prose)."""
        overrides = {}
        patterns = (
            r'Notice Period\s*\|?\s*(\d+)\s*days?',
            r"giving\s+(\d+)\s*days?['\u2019]?\s*prior\s+written\s+notice",
            r'payable\s+within\s+(\d+)\s*days?\s+of\s+the\s+invoice',
            r'within\s+(\d+)\s*days?\s+of\s+the\s+invoice\s+date',
        )
        for pattern in patterns:
            match = re.search(pattern, text or '', flags=re.IGNORECASE)
            if match:
                days = match.group(1)
                overrides['notice_period_days'] = days
                overrides['notice_period'] = '%s days' % days
                break
        return overrides

    def _clm_is_counterparty_upload(self):
        """True when this version's Word file is the counterparty's upload."""
        self.ensure_one()
        snap = self.content_snapshot or {}
        if snap.get('captured_from') == 'counterparty_upload':
            return True
        name = ''
        if self.document_id:
            name = (self.document_id.name or '').lower()
        return '_counterparty.docx' in name

    def _clm_apply_term_overrides_to_snapshot(self, snapshot, overrides):
        """Return a deep-copied snapshot with variable / key-term overrides applied."""
        import copy
        snap = copy.deepcopy(snapshot or {})
        if not overrides:
            return snap
        variables = dict(snap.get('variables') or {})
        variables.update(overrides)
        snap['variables'] = variables
        for group_key in ('key_terms', 'commercial_terms', 'legal_terms', 'other_terms'):
            rows = snap.get(group_key) or []
            for row in rows:
                label = (row.get('label') or '').strip().lower()
                if label == 'notice period' and overrides.get('notice_period'):
                    row['value'] = overrides['notice_period']
        return snap

    def _clm_snapshot_for_counterparty_upload(self, parent_version=None):
        """Build a frozen HTML snapshot for a portal upload (same layout as v1).

        Starts from the parent version's snapshot (or a live capture), then overlays
        terms detected in the uploaded Word file so Preview matches the frozen
        HTML layout while reflecting counterparty edits (e.g. 30 → 60 days).
        """
        self.ensure_one()
        parent = parent_version or self.parent_version_id
        if parent and parent.content_snapshot:
            base = parent.content_snapshot
        else:
            base = self.contract_id._clm_capture_content()
        text = ''
        if self._clm_docx_attachment():
            try:
                text = self._clm_extract_docx_plain_text()
            except UserError:
                text = ''
        overrides = self._clm_term_overrides_from_docx_text(text)
        snap = self._clm_apply_term_overrides_to_snapshot(base, overrides)
        snap['captured_at'] = fields.Datetime.to_string(fields.Datetime.now())
        snap['captured_from'] = 'counterparty_upload'
        return snap

    def _clm_render_uploaded_docx_preview(self, type_label):
        self.ensure_one()
        contract = self.contract_id
        attachment = self._clm_docx_attachment()
        banner = {
            'css': 'alert-info',
            'message': (
                'Version v%s (%s). Preview is taken from the uploaded Word document '
                '(counterparty / file upload), not from the live contract fields.'
            ) % (self.version_number, type_label),
        }
        try:
            raw = self._clm_attachment_bytes(attachment)
            body = contract._clm_html_from_docx_bytes(raw)
            return contract._clm_wrap_docx_preview_html(body, banner)
        except Exception:  # noqa: BLE001 — fall back to plain text
            text = self._clm_extract_docx_plain_text()
            body = Markup('').join(
                Markup('<p>%s</p>') % line for line in text.splitlines() if line
            )
            return contract._clm_wrap_docx_preview_html(body, banner)

    # -------------------------------------------------------------------------
    # Generated documents
    # -------------------------------------------------------------------------

    def _clm_final_pdf_bytes(self):
        """PDF bytes for this version — same renderer Download PDF uses.

        Does not write an attachment. ``_clm_generate_document('pdf')`` stores
        these bytes so Preview and Download share one generation path.
        """
        self.ensure_one()
        contract = self.contract_id
        contract.check_access('read')
        # Technical docs / Base DOCX attachments may be restricted; Preview and
        # Download still need the bytes after the contract-level read check.
        return self.sudo()._clm_final_pdf_bytes_sudo()

    def _clm_final_pdf_bytes_sudo(self):
        self.ensure_one()
        contract = self.contract_id
        kind = self._clm_document_kind()

        uploaded = self.env['ir.attachment']
        if (not self.content_snapshot) or self._clm_is_counterparty_upload():
            uploaded = self._clm_docx_attachment()

        if uploaded:
            from ..services.docx_to_pdf import docx_bytes_to_pdf, find_soffice
            try:
                if find_soffice():
                    return docx_bytes_to_pdf(self._clm_attachment_bytes(uploaded))
            except Exception as error:  # noqa: BLE001
                _logger.warning(
                    'NextContract: LibreOffice PDF from upload failed for v%s: %s',
                    self.version_number, error,
                )
            return self._clm_pdf_bytes_from_uploaded_docx_plain()

        if self.is_signed_version and self.generated_pdf_id:
            return self._clm_attachment_bytes(self.generated_pdf_id)

        data, _mime, _name = contract._render_contract_document(
            'pdf', version=self, kind=kind,
        )
        return data

    def _clm_pdf_bytes_from_uploaded_docx_plain(self):
        """reportlab PDF bytes from uploaded DOCX plain text (no attachment write)."""
        self.ensure_one()
        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.pdfgen import canvas
            from reportlab.lib.units import mm
        except ImportError as error:
            raise UserError(
                "Generating PDF from an uploaded Word file requires reportlab."
            ) from error

        text_content = self._clm_extract_docx_plain_text()
        buffer = io.BytesIO()
        pdf = canvas.Canvas(buffer, pagesize=A4)
        width, height = A4
        left, top, bottom = 20 * mm, height - 20 * mm, 20 * mm
        max_chars = 95
        y = top
        pdf.setFont('Helvetica', 10)
        for line in text_content.splitlines() or ['']:
            for chunk in self._clm_wrap_plain_text_line(line, max_chars):
                if y < bottom:
                    pdf.showPage()
                    pdf.setFont('Helvetica', 10)
                    y = top
                pdf.drawString(left, y, chunk)
                y -= 14
        pdf.save()
        return buffer.getvalue()

    def _clm_generate_document(self, fmt):
        """Render this version as `fmt` and keep the file on the contract.

        The file is named after the version (…_V2_DRAFT.docx), so each version
        keeps its own files. A signed version's files are produced once and
        then served as they are.

        Counterparty / portal uploads keep their uploaded Word file as the
        source of truth for DOCX/PDF. Regenerating from the content snapshot
        would revive the pre-negotiation notice period.

        PDF bytes always come from ``_clm_final_pdf_bytes`` so Preview and
        Download PDF share one generation path.
        """
        self.ensure_one()
        if fmt not in _DOCUMENT_FORMATS:
            raise UserError("Unknown document format '%s'. Choose 'docx' or 'pdf'." % fmt)
        contract = self.contract_id
        contract.check_access('read')

        field_name = 'generated_%s_id' % fmt
        version = self.sudo()
        kind = self._clm_document_kind()
        filename = contract._clm_document_filename(fmt, kind=kind, version=self)
        Attachment = self.env['ir.attachment'].sudo()
        existing = Attachment.search([
            ('res_model', '=', 'clm.contract'),
            ('res_id', '=', contract.id),
            ('name', '=', filename),
        ], limit=1)
        if existing and not self._clm_attachment_has_payload(existing, fmt):
            _logger.warning(
                'NextContract: replacing empty %s attachment %s for v%s',
                fmt, existing.id, self.version_number,
            )
            existing = existing.sudo()

        uploaded = self.env['ir.attachment']
        if (not self.content_snapshot) or self._clm_is_counterparty_upload():
            uploaded = self._clm_docx_attachment()

        if uploaded and fmt == 'docx':
            attachment = uploaded
        elif self.is_signed_version and self._clm_attachment_has_payload(
            existing or version[field_name], fmt,
        ):
            attachment = existing or version[field_name]
        elif fmt == 'pdf':
            data = self._clm_final_pdf_bytes()
            attachment = self._clm_store_generated_attachment(
                filename, data, 'application/pdf', existing,
            )
        else:
            # Content snapshot (or live fields) → proper NextContract render.
            data, mimetype, filename = contract._render_contract_document(
                fmt, version=self, kind=kind
            )
            attachment = self._clm_store_generated_attachment(
                filename, data, mimetype, existing,
            )

        # sudo: the link is technical metadata, also on immutable executed versions.
        # Skip preview compute while linking — avoids empty PDF rows during Generate Draft.
        if version[field_name] != attachment:
            version.with_context(clm_skip_version_preview_compute=True).write(
                {field_name: attachment.id},
            )
        return attachment

    def _clm_store_generated_attachment(self, filename, data, mimetype, existing=False):
        """Persist generated DOCX/PDF bytes without empty filestore rows.

        Writing ``datas`` (base64) while ``content_preview_html`` is computing
        can leave ``file_size=0`` attachments that break the PDF iframe. Prefer
        binary ``raw`` — same fix as working-copy preview.
        """
        self.ensure_one()
        if isinstance(data, memoryview):
            data = data.tobytes()
        if not data:
            raise UserError(
                "Could not generate '%s' — the renderer returned an empty file."
                % filename
            )
        data = bytes(data)
        if mimetype == 'application/pdf' and not data.startswith(b'%PDF'):
            raise UserError(
                "Could not generate a valid PDF for '%s'." % filename
            )
        vals = {
            'name': filename,
            'raw': data,
            'mimetype': mimetype,
            'res_model': 'clm.contract',
            'res_id': self.contract_id.id,
        }
        Attachment = self.env['ir.attachment'].sudo()
        attachment = existing.sudo() if existing else Attachment.create(vals)
        if existing:
            attachment.write(vals)
        self.env.flush_all()
        attachment.invalidate_recordset(['raw', 'file_size', 'checksum', 'store_fname'])
        fmt = 'pdf' if mimetype == 'application/pdf' else 'docx'
        if not self._clm_attachment_has_payload(attachment, fmt):
            attachment.write({'raw': data})
            self.env.flush_all()
            attachment.invalidate_recordset(['raw', 'file_size', 'checksum'])
        if not self._clm_attachment_has_payload(attachment, fmt):
            raise UserError(
                "Could not save '%s' — attachment storage has no file data." % filename
            )
        if mimetype == 'application/pdf':
            attachment.generate_access_token()
        return attachment

    @staticmethod
    def _clm_attachment_has_payload(attachment, fmt):
        """True when the attachment has real file bytes for ``fmt``."""
        if not attachment:
            return False
        raw = attachment.with_context(bin_size=False).raw or b''
        if not raw:
            return False
        if fmt == 'pdf':
            return raw.startswith(b'%PDF')
        return True

    def _clm_pdf_attachment_from_uploaded_docx(self, filename, existing=False):
        """Build a PDF attachment from the uploaded DOCX plain text."""
        self.ensure_one()
        data = self._clm_pdf_bytes_from_uploaded_docx_plain()
        return self._clm_store_generated_attachment(
            filename, data, 'application/pdf', existing,
        )

    @staticmethod
    def _clm_wrap_plain_text_line(line, max_chars):
        """Wrap a line on word boundaries — never mid-word like chunk[:95]."""
        line = line or ''
        if len(line) <= max_chars:
            return [line]
        words = line.split(' ')
        rows = []
        current = ''
        for word in words:
            if not current:
                # Extremely long token: hard-split only as a last resort.
                while len(word) > max_chars:
                    rows.append(word[:max_chars])
                    word = word[max_chars:]
                current = word
                continue
            candidate = '%s %s' % (current, word)
            if len(candidate) <= max_chars:
                current = candidate
            else:
                rows.append(current)
                while len(word) > max_chars:
                    rows.append(word[:max_chars])
                    word = word[max_chars:]
                current = word
        if current or not rows:
            rows.append(current)
        return rows

    @staticmethod
    def _clm_download_action(attachment):
        return {
            'type': 'ir.actions.act_url',
            'url': '/web/content/%s?download=true&filename=%s' % (attachment.id, attachment.name),
            'target': 'self',
        }

    # -------------------------------------------------------------------------
    # UI actions
    # -------------------------------------------------------------------------

    def action_preview_version(self):
        self.ensure_one()
        contract = self.contract_id
        try:
            attachment = self._clm_generate_document('pdf')
            if not self._clm_attachment_has_payload(attachment, 'pdf'):
                raise UserError(
                    'The PDF could not be saved for preview. Try Download PDF, '
                    'or regenerate the draft.'
                )
        except UserError:
            raise
        except Exception as error:  # noqa: BLE001
            _logger.warning(
                'NextContract: could not materialise PDF for version %s preview: %s',
                self.version_number, error,
            )
            raise UserError(
                'Could not build the PDF preview: %s' % error
            ) from error
        self.env.flush_all()
        type_label = dict(
            self._fields['version_type']._description_selection(self.env)
        ).get(self.version_type, '')
        banner = self._clm_version_preview_banner(type_label)
        content = contract.sudo()._build_final_contract_content(
            version=self, kind=self._clm_document_kind(),
        )
        return contract._clm_action_open_pdf_preview(
            attachment,
            title='Version Preview — v%s' % self.version_number,
            banner=banner,
            warnings=content.get('warnings') or [],
            version=self,
        )

    def action_generate_docx(self):
        self.ensure_one()
        return self._clm_download_action(self._clm_generate_document('docx'))

    def action_generate_pdf(self):
        self.ensure_one()
        return self._clm_download_action(self._clm_generate_document('pdf'))
