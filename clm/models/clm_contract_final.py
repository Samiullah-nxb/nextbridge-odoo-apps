# Part of NextContract. See LICENSE file for full copyright and licensing details.
"""
Final Contract — the outcome of the lifecycle.

Resolves which version *is* the final contract, and serves it as a viewable /
downloadable document in PDF and DOCX.

Resolution priority (PRD §7 of the Final Contract spec):

  1. The executed version's signed document — the authoritative signed artifact.
     It is served byte-for-byte and is never regenerated or modified.
  2. Otherwise the final approved version (signature draft / final legal draft).
  3. Otherwise there is no final contract, and no export buttons are shown.

Both formats are rendered from that one version through
clm.contract._render_contract_document(), so the PDF and the DOCX always
represent the same contract. When the final version already carries a file in
the requested format, that file is served as-is rather than re-rendered.

Access control is Odoo's own: every export is an ir.attachment scoped to the
contract (res_model / res_id), so /web/content applies the contract's record
rules. No public URLs, no tokens.
"""

import base64
import logging

from odoo import models, fields, api
from odoo.exceptions import AccessError, UserError

_logger = logging.getLogger(__name__)

_PDF_MIME = 'application/pdf'
_DOCX_MIME = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
_DOC_MIME = 'application/msword'
_WORD_MIMES = frozenset({
    _DOCX_MIME,
    _DOC_MIME,
    'application/vnd.ms-word',
    'application/vnd.ms-word.document.macroEnabled.12',
})
# Cached LibreOffice conversion of a signed Word upload for in-app PDF preview.
_SIGNED_PREVIEW_PREFIX = 'CLM_Signed_Preview__'

# Versions that can stand as "the final approved contract", best first.
_APPROVED_TYPES = ('signature_draft', 'final_legal_draft')

# A final approved contract is only meaningful once the contract has actually
# been approved; before that the document on file is still a working draft.
_APPROVED_STATES = frozenset({
    'approved', 'pending_signature', 'executed', 'active',
    'expiring_soon', 'renewed', 'expired', 'terminated', 'archived',
})


def _format_label(mimetype):
    """A short, human name for a file format ('image/jpeg' → 'JPEG')."""
    if not mimetype:
        return 'file'
    if mimetype == _PDF_MIME:
        return 'PDF'
    if mimetype == _DOCX_MIME:
        return 'DOCX'
    return (mimetype.rsplit('/', 1)[-1] or 'file').upper()


class ClmContractFinal(models.Model):
    _inherit = 'clm.contract'

    final_version_id = fields.Many2one(
        'clm.contract.version', string='Final Version',
        compute='_compute_final_contract',
        help='The version that constitutes the final contract.'
    )
    final_version_label = fields.Char(
        string='Final Version Number', compute='_compute_final_contract'
    )
    final_contract_available = fields.Boolean(
        string='Final Contract Available', compute='_compute_final_contract'
    )
    final_contract_kind = fields.Selection([
        ('approved', 'Final Approved Contract'),
        ('signed', 'Final Signed Contract'),
    ], string='Final Contract', compute='_compute_final_contract')
    final_contract_label = fields.Char(
        string='Final Contract Type', compute='_compute_final_contract'
    )
    final_contract_status = fields.Char(
        string='Final Contract Status', compute='_compute_final_contract'
    )
    final_contract_document_id = fields.Many2one(
        'documents.document', string='Final Contract Document',
        compute='_compute_final_contract',
        help='The Odoo Documents record holding the final contract file, when '
             'the final version has one.'
    )
    signed_artifact_available = fields.Boolean(
        string='Signed Document Available', compute='_compute_signed_artifact',
        help='A signed document exists and can be downloaded exactly as signed.'
    )
    signed_artifact_is_pdf = fields.Boolean(
        string='Signed Document Is PDF', compute='_compute_signed_artifact'
    )
    signed_artifact_label = fields.Char(
        string='Signed Document', compute='_compute_signed_artifact',
        help='Name and format of the authoritative signed document.'
    )

    # -------------------------------------------------------------------------
    # Resolution
    # -------------------------------------------------------------------------

    def _get_final_contract_version(self):
        """Return (version, kind) — kind is 'signed', 'approved' or False.

        Versions are read through sudo. Which version is the final contract is
        a property of the contract, and the answer is shown wherever the
        contract is shown, including the Contracts list. Only the Business
        Requester, Legal Reviewer and Legal Admin roles have read access to
        clm.contract.version itself, so reading it as the user raises
        AccessError for the Read-Only, Approver, Finance and Procurement roles
        that can read the contract perfectly well. Nothing here widens what can
        be *downloaded*: every export still goes through check_access('read')
        on the contract and is served from a contract-scoped attachment.
        """
        self.ensure_one()
        versions = self.sudo().version_ids
        executed = versions.filtered(lambda v: v.version_type == 'executed')
        if executed:
            return max(executed, key=lambda v: v.version_number), 'signed'

        if self.state in _APPROVED_STATES:
            approved = versions.filtered(
                lambda v: v.version_type in _APPROVED_TYPES and v.status == 'active'
            )
            if approved:
                return max(approved, key=lambda v: v.version_number), 'approved'

        return self.env['clm.contract.version'], False

    @api.depends('state', 'version_ids.version_type', 'version_ids.status',
                 'version_ids.version_number', 'version_ids.document_id',
                 'signature_status')
    def _compute_final_contract(self):
        for contract in self:
            version, kind = contract._get_final_contract_version()
            contract.final_version_id = version
            contract.final_contract_kind = kind or False
            contract.final_contract_available = bool(kind)
            contract.final_version_label = 'v%s' % version.version_number if version else ''
            contract.final_contract_document_id = version.document_id or False
            if kind == 'signed':
                contract.final_contract_label = 'Final Signed Contract'
                contract.final_contract_status = 'Signed'
            elif kind == 'approved':
                contract.final_contract_label = 'Final Approved Contract'
                contract.final_contract_status = (
                    'Approved — awaiting signature'
                    if contract.signature_status == 'pending' else 'Approved'
                )
            else:
                contract.final_contract_label = ''
                contract.final_contract_status = ''

    @api.depends('state', 'version_ids.version_type', 'version_ids.status',
                 'version_ids.document_id', 'sign_request_ids.state')
    def _compute_signed_artifact(self):
        for contract in self:
            # Read-only: never create/copy attachments during form load.
            try:
                artifact = contract._get_signed_artifact_source()
            except Exception:
                _logger.exception(
                    'Failed to resolve signed artifact for contract %s',
                    contract.id,
                )
                artifact = self.env['ir.attachment']
            contract.signed_artifact_available = bool(artifact)
            is_pdf = bool(artifact) and artifact.mimetype == _PDF_MIME
            contract.signed_artifact_is_pdf = is_pdf
            if not artifact:
                contract.signed_artifact_label = ''
            elif is_pdf:
                contract.signed_artifact_label = artifact.name
            else:
                contract.signed_artifact_label = '%s (%s)' % (
                    artifact.name, _format_label(artifact.mimetype),
                )

    # -------------------------------------------------------------------------
    # Source documents
    # -------------------------------------------------------------------------

    def _get_signed_artifact_source(self):
        """Locate the authoritative signed file without copying it."""
        self.ensure_one()
        _version, kind = self._get_final_contract_version()
        if kind != 'signed':
            return self.env['ir.attachment']

        attachment = self._find_sign_completed_attachment()
        if not attachment:
            version = self.final_version_id
            document = version.sudo().document_id if version else False
            attachment = document.attachment_id if document else False
        if not attachment:
            return self.env['ir.attachment']
        attachment = attachment.sudo()
        # Prefer raw bytes — ``datas`` can be oddly encoded and break form loads.
        if not attachment.raw and not attachment.datas:
            return self.env['ir.attachment']
        return attachment

    def _get_signed_artifact(self):
        """The authoritative signed document, exactly as it was produced.

        Priority, per the signed-contract rule:
          1. Odoo Sign's completed document for this contract.
          2. The file attached to the executed version (a manual signature
             upload — a counter-signed PDF, a scan, a photo of the signed page).

        Returns an empty recordset when the contract has no signed artifact, so
        a generated PDF can never be presented as the signed one.

        Download / view actions receive a contract-scoped copy so users can
        open the file under the contract's own access rules.
        """
        self.ensure_one()
        source = self._get_signed_artifact_source()
        if not source:
            return self.env['ir.attachment']
        return self._contract_scoped_copy(source)

    def _find_sign_completed_attachment(self):
        """Odoo Sign's completed document, when Sign actually produced one."""
        self.ensure_one()
        if 'sign_request_ids' not in self._fields:
            return self.env['ir.attachment']
        requests = self.sudo().sign_request_ids.filtered(lambda r: r.state == 'signed')
        # An amended contract has been signed more than once: only the signature
        # of the final version's own draft is its signed artifact.
        version, _kind = self._get_final_contract_version()
        if version and version.source_version_id:
            requests = requests.filtered(
                lambda r: r.clm_source_version_id == version.source_version_id
            )
        for request in requests.sorted(lambda r: r.id, reverse=True):
            attachments = request.completed_document_attachment_ids.filtered(
                lambda a: a.mimetype == _PDF_MIME
            )
            if not attachments:
                continue
            # Prefer the signed contract PDF over the Certificate of Completion.
            signed_doc = attachments.filtered(
                lambda a: 'certificate of completion' not in (a.name or '').lower()
            )
            return (signed_doc[:1] or attachments[:1])
        return self.env['ir.attachment']

    def _contract_scoped_copy(self, attachment):
        """Return an attachment on this contract holding `attachment`'s bytes.

        The signed document produced by Odoo Sign lives on sign.request, which
        NextContract users have no access to. Rather than widening Sign's permissions or
        touching the signed file, the bytes are mirrored onto a contract-scoped
        attachment so the contract's own record rules govern the download.
        """
        self.ensure_one()
        attachment = attachment.sudo()
        if attachment.res_model == 'clm.contract' and attachment.res_id == self.id:
            return attachment

        Attachment = self.env['ir.attachment'].sudo()
        existing = Attachment.search([
            ('res_model', '=', 'clm.contract'),
            ('res_id', '=', self.id),
            ('name', '=', attachment.name),
        ], limit=1)

        raw = attachment.raw or b''
        if not raw and attachment.datas:
            raw = self._clm_safe_b64decode(attachment.datas)
        if not raw:
            _logger.warning(
                'Signed artifact %s for contract %s has no readable content',
                attachment.id, self.id,
            )
            return self.env['ir.attachment']

        try:
            if existing:
                if existing.checksum != attachment.checksum:
                    existing.write({'raw': raw, 'mimetype': attachment.mimetype})
                return existing
            return Attachment.create({
                'name': attachment.name,
                'raw': raw,
                'mimetype': attachment.mimetype or _PDF_MIME,
                'res_model': 'clm.contract',
                'res_id': self.id,
            })
        except Exception:
            _logger.exception(
                'Could not mirror signed artifact %s onto contract %s',
                attachment.id, self.id,
            )
            # Fall back to the source so downloads can still work for admins.
            return attachment

    @staticmethod
    def _clm_safe_b64decode(datas):
        """Decode Binary ``datas`` even when padding is missing."""
        if not datas:
            return b''
        if isinstance(datas, memoryview):
            datas = datas.tobytes()
        if isinstance(datas, bytes):
            # Already raw PDF / binary content, not base64.
            if datas.startswith(b'%PDF') or datas.startswith(b'\x89PNG'):
                return datas
            s = datas
        else:
            s = str(datas).encode('ascii', errors='ignore')
        s = s.strip().replace(b'\n', b'').replace(b'\r', b'')
        pad = (-len(s)) % 4
        if pad:
            s = s + (b'=' * pad)
        try:
            return base64.b64decode(s)
        except Exception:
            return b''

    # -------------------------------------------------------------------------
    # Export materialisation
    # -------------------------------------------------------------------------

    def _get_final_contract_attachment(self, fmt):
        """The generated final contract in `fmt` ('pdf' or 'docx').

        Both formats are rendered from one canonical content dict, so they
        carry the same contract wording, parties, dates, number, version and
        signature information. The signed artifact is served separately by
        _get_signed_artifact() and is never regenerated.

        Exception: a counterparty / portal upload is the document source of
        truth for that version — regenerating from Base DOCX would revive
        clauses the counterparty removed. Final DOCX/PDF then follow the same
        upload path as header Generate DOCX / Generate PDF.
        """
        self.ensure_one()
        self.check_access('read')
        version, kind = self._get_final_contract_version()
        if not kind:
            raise UserError(
                'Final contract is not available yet.\n\n'
                'A contract has a final document once it has been approved for '
                'signature or executed.'
            )
        # Prefer the Word file that was actually signed (signature draft /
        # counterparty upload) when the executed version has no document.
        content_version = self._clm_final_content_version(version)
        if self._clm_final_uses_upload(content_version):
            return self._render_final_export_from_upload(
                fmt, content_version, kind, filename_version=version,
            )
        return self._render_final_export(fmt, content_version, kind)

    def _clm_final_content_version(self, version):
        """Version whose document/wording should feed View Final / Download PDF|DOCX.

        Executed versions created via Odoo Sign historically omitted
        ``document_id``. Fall back to the signature-draft (source/parent) so
        Final never regenerates from an older Base DOCX.
        """
        if not version:
            return version
        if version._clm_docx_attachment() if hasattr(version, '_clm_docx_attachment') else version.document_id:
            return version
        for candidate in (version.source_version_id, version.parent_version_id):
            if not candidate:
                continue
            if hasattr(candidate, '_clm_docx_attachment') and candidate._clm_docx_attachment():
                return candidate
            if candidate.document_id:
                return candidate
        # Last resort: highest active/signature_draft with a DOCX on this contract.
        siblings = version.contract_id.sudo().version_ids.filtered(
            lambda v: v.version_type in (
                'signature_draft', 'negotiated_draft', 'approved', 'draft',
            ) and v.document_id
        ).sorted('version_number', reverse=True)
        for sibling in siblings:
            if hasattr(sibling, '_clm_docx_attachment') and sibling._clm_docx_attachment():
                return sibling
            if sibling.document_id:
                return sibling
        return version

    def _clm_final_uses_upload(self, version):
        """True when the final version's uploaded Word file is authoritative."""
        if not version:
            return False
        if not hasattr(version, '_clm_is_counterparty_upload'):
            return False
        if (not version.content_snapshot) or version._clm_is_counterparty_upload():
            return bool(version._clm_docx_attachment())
        return False

    def _render_final_export_from_upload(self, fmt, version, kind, filename_version=None):
        """Cache FINAL pdf/docx bytes from the version's uploaded Word file."""
        self.ensure_one()
        if fmt not in ('pdf', 'docx'):
            raise UserError("Unknown contract document format '%s'." % fmt)
        uploaded = version._clm_docx_attachment()
        if not uploaded:
            raise UserError(
                'The final contract version has no uploaded Word document on file.'
            )
        label_version = filename_version or version
        filename = self._clm_document_filename(fmt, kind=kind, version=label_version)
        Attachment = self.env['ir.attachment'].sudo()
        existing = Attachment.search([
            ('res_model', '=', 'clm.contract'),
            ('res_id', '=', self.id),
            ('name', '=', filename),
        ], limit=1)

        raw_docx = version._clm_attachment_bytes(uploaded)
        if fmt == 'docx':
            data = raw_docx
            mimetype = (
                'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
            )
        else:
            from odoo.addons.clm.services.docx_to_pdf import docx_bytes_to_pdf
            data = docx_bytes_to_pdf(raw_docx)
            mimetype = 'application/pdf'

        # Always refresh from the upload so a previously cached Base-DOCX FINAL
        # file cannot keep removed clauses (e.g. Force Majeure).
        # Prefer ``raw`` over ``datas`` so filestore never gets an empty row.
        vals = {
            'name': filename,
            'raw': data if isinstance(data, (bytes, bytearray)) else bytes(data),
            'mimetype': mimetype,
            'res_model': 'clm.contract',
            'res_id': self.id,
        }
        if existing:
            existing.write(vals)
            return existing
        return Attachment.create(vals)

    def _render_final_export(self, fmt, version, kind):
        """Render and cache the generated export for `fmt`."""
        self.ensure_one()
        # Named after the version, so the final file of an amended contract never
        # replaces the file of the version it amended.
        filename = self._clm_document_filename(fmt, kind=kind, version=version)
        Attachment = self.env['ir.attachment'].sudo()
        existing = Attachment.search([
            ('res_model', '=', 'clm.contract'),
            ('res_id', '=', self.id),
            ('name', '=', filename),
        ], limit=1)
        if (
            existing
            and kind != 'signed'
            and existing.file_size
            and not self._is_final_export_stale(existing, version)
        ):
            raw = existing.with_context(bin_size=False).raw or b''
            if fmt != 'pdf' or raw.startswith(b'%PDF'):
                return existing

        data, mimetype, _filename = self._render_contract_document(
            fmt, version=version, kind=kind
        )
        if isinstance(data, memoryview):
            data = data.tobytes()
        data = bytes(data or b'')
        if not data or (fmt == 'pdf' and not data.startswith(b'%PDF')):
            raise UserError(
                "Could not generate a valid %s for the final contract."
                % fmt.upper()
            )
        vals = {
            'name': filename,
            'raw': data,
            'mimetype': mimetype,
            'res_model': 'clm.contract',
            'res_id': self.id,
        }
        if existing:
            existing.write(vals)
            return existing
        return Attachment.create(vals)

    def _is_final_export_stale(self, attachment, version):
        """True when the contract, final version, or PDF template changed after the export."""
        self.ensure_one()
        stamps = [self.write_date, version.write_date]
        stamps += self.field_value_ids.mapped('write_date')
        report_view = self.env.ref(
            'clm.report_clm_contract_document_body', raise_if_not_found=False,
        )
        if report_view and report_view.write_date:
            stamps.append(report_view.write_date)
        # E-sign completion / Sign PDF updates must refresh generated finals.
        if 'sign_request_ids' in self._fields:
            stamps += self.sudo().sign_request_ids.mapped('write_date')
            for req in self.sudo().sign_request_ids:
                stamps += req.request_item_ids.mapped('write_date')
        stamps = [stamp for stamp in stamps if stamp]
        if not stamps or not attachment.write_date:
            return True
        return attachment.write_date < max(stamps)

    def _attachment_url(self, attachment, download=True):
        return '/web/content/%s?download=%s&filename=%s' % (
            attachment.id, 'true' if download else 'false', attachment.name,
        )

    def _final_contract_url(self, fmt, download=True):
        self.ensure_one()
        return self._attachment_url(
            self._get_final_contract_attachment(fmt), download=download
        )

    # -------------------------------------------------------------------------
    # UI actions
    # -------------------------------------------------------------------------

    def action_view_final_contract(self):
        """Open the generated Final Contract PDF in the browser.

        This is the NextContract final document (cover, terms, signatures
        block), not the raw file uploaded at execution. The signed upload /
        Odoo Sign artifact is available via Download Signed Document.
        """
        self.ensure_one()
        return self.action_view_final_pdf()

    def action_view_signed_artifact(self):
        """Open the authoritative signed upload / Sign artifact in-app.

        PDF uploads open as-is. Word (.doc / .docx) manual uploads are converted
        to PDF via LibreOffice so the in-app viewer can show them. The original
        signed file on the executed version is left unchanged.
        """
        self.ensure_one()
        artifact = self._ensure_signed_artifact_pdf()
        if not artifact:
            raise UserError(
                'This contract has no signed document on file.\n\n'
                'Use View Contract PDF for the generated final contract document.'
            )
        converted = bool(
            artifact.name and artifact.name.startswith(_SIGNED_PREVIEW_PREFIX)
        )
        banner_message = (
            'Signed Word document converted to PDF for preview. '
            'Download Signed PDF still offers the original upload.'
            if converted else
            'Odoo Sign signed document — the same PDF as Download Signed PDF.'
        )
        return self._clm_action_open_pdf_preview(
            artifact,
            title='Signed PDF',
            banner={'css': 'alert-success', 'message': banner_message},
        )

    def _ensure_signed_artifact_pdf(self):
        """Return a PDF attachment for View Signed PDF (convert Word if needed)."""
        self.ensure_one()
        artifact = self._get_signed_artifact()
        if not artifact:
            return self.env['ir.attachment']
        if self._clm_attachment_is_pdf(artifact):
            return artifact
        if self._clm_attachment_is_word(artifact):
            return self._clm_signed_word_to_pdf_attachment(artifact)
        raise UserError(
            'Preview is only available for PDF or Word (.doc / .docx) documents.\n\n'
            'Use Download Signed PDF for this file format, or upload a PDF / Word '
            'copy when marking the contract as manually signed.'
        )

    @staticmethod
    def _clm_attachment_bytes(attachment):
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

    @classmethod
    def _clm_attachment_is_pdf(cls, attachment):
        """True only when the file bytes are actually a PDF (name can lie)."""
        if not attachment:
            return False
        raw = cls._clm_attachment_bytes(attachment)
        return bool(raw and raw.startswith(b'%PDF'))

    @classmethod
    def _clm_attachment_is_word(cls, attachment):
        if not attachment:
            return False
        from odoo.addons.clm.services.docx_to_pdf import guess_word_extension
        raw = cls._clm_attachment_bytes(attachment)
        name = attachment.name or ''
        if guess_word_extension(raw, filename=name):
            return True
        mimetype = (attachment.mimetype or '').lower()
        return mimetype in _WORD_MIMES

    def _clm_signed_preview_pdf_name(self, source):
        base = (source.name or 'signed_document').rsplit('.', 1)[0]
        # Keep the name filesystem-safe and stable across preview clicks.
        safe = ''.join(ch if ch.isalnum() or ch in '-_' else '_' for ch in base)[:80]
        return '%s%s.pdf' % (_SIGNED_PREVIEW_PREFIX, safe or 'signed_document')

    def _clm_signed_word_to_pdf_attachment(self, source):
        """Convert a signed Word upload to a cached contract-scoped PDF."""
        self.ensure_one()
        from odoo.addons.clm.services.docx_to_pdf import word_bytes_to_pdf

        source = source.sudo()
        raw = self._clm_attachment_bytes(source)
        pdf_bytes = word_bytes_to_pdf(raw, filename=source.name)
        pdf_name = self._clm_signed_preview_pdf_name(source)
        Attachment = self.env['ir.attachment'].sudo()
        existing = Attachment.search([
            ('res_model', '=', 'clm.contract'),
            ('res_id', '=', self.id),
            ('name', '=', pdf_name),
        ], limit=1)
        vals = {
            'name': pdf_name,
            'raw': pdf_bytes,
            'mimetype': _PDF_MIME,
            'res_model': 'clm.contract',
            'res_id': self.id,
            'description': 'LibreOffice preview of signed Word upload %s' % (
                source.name or source.id,
            ),
        }
        if existing:
            existing.write(vals)
            return existing
        return Attachment.create(vals)

    @staticmethod
    def _can_read(record):
        """True when the current user may open `record` in Odoo."""
        try:
            record.check_access('read')
        except AccessError:
            return False
        return True

    def action_download_final_pdf(self):
        """The generated complete contract PDF — never the signed artifact."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_url',
            'url': self._final_contract_url('pdf'),
            'target': 'self',
        }

    def action_view_final_pdf(self):
        """Open the generated Contract PDF in the in-app native PDF viewer."""
        self.ensure_one()
        attachment = self._get_final_contract_attachment('pdf')
        version, kind = self._get_final_contract_version()
        warnings = []
        try:
            content = self._build_final_contract_content(
                version=version, kind=kind or 'approved',
            )
            warnings = content.get('warnings') or []
        except Exception:  # noqa: BLE001
            pass
        return self._clm_action_open_pdf_preview(
            attachment,
            title='Final Contract',
            banner={
                'css': 'alert-info',
                'message': (
                    'Generated NextContract PDF — the same file as Download PDF.'
                ),
            },
            warnings=warnings,
        )

    def action_download_final_docx(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_url',
            'url': self._final_contract_url('docx'),
            'target': 'self',
        }

    def action_open_final_contract_wizard(self):
        """Open a single dialog to view or download the final contract."""
        self.ensure_one()
        if not self.final_contract_available:
            raise UserError('Final contract is not available yet.')
        return {
            'type': 'ir.actions.act_window',
            'name': 'Final Contract',
            'res_model': 'clm.contract.final.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_contract_id': self.id},
        }

    def action_download_signed_document(self):
        """The authoritative signed document, byte-for-byte as it was signed."""
        self.ensure_one()
        self.check_access('read')
        artifact = self._get_signed_artifact()
        if not artifact:
            raise UserError(
                'This contract has no signed document on file.\n\n'
                'Use Download PDF for the final contract document, which is '
                'generated from the approved contract content.'
            )
        return {
            'type': 'ir.actions.act_url',
            'url': self._attachment_url(artifact),
            'target': 'self',
        }


class ClmContractVersionFinal(models.Model):
    """Signing details captured at execution time, for the signature block."""
    _inherit = 'clm.contract.version'

    execution_signatory = fields.Char(
        string='Signatory', readonly=True,
        help='Authorised signer recorded when the contract was executed.'
    )
    execution_notes = fields.Text(
        string='Execution Notes', readonly=True,
        help='Notes recorded when the contract was executed.'
    )
