# Part of NextContract. See LICENSE file for full copyright and licensing details.

import logging

from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


class ClmContractVersion(models.Model):
    """
    Tracks every distinct document version for a contract.

    Each version holds a reference to an Odoo Documents record
    (`documents.document`) as the authoritative file-management layer.
    The underlying binary is stored via Odoo's native ir.attachment.

    Executed versions are immutable for non-superusers (PRD §37).
    """
    _name = 'clm.contract.version'
    _description = 'Contract Version'
    _order = 'contract_id, version_number desc'
    _rec_name = 'display_name'

    # A version has no `name` field, and Odoo's fallback display name for a
    # model without one is the literal "<model>,<id>". That string is what any
    # relation to a version renders as: "Parent Version" on the version form
    # and in the Create New Version wizard both read
    # "clm.contract.version,787", which tells the user nothing and cannot be
    # searched by typing. Name a version the way the rest of the UI already
    # speaks about it — the editor header says "v2", the dialog title says
    # "Version 2".
    _rec_names_search = ['contract_id', 'version_number']

    contract_id = fields.Many2one(
        'clm.contract', string='Contract', required=True, ondelete='cascade',
        index=True
    )
    display_name = fields.Char(compute='_compute_display_name', store=True)
    company_id = fields.Many2one(
        'res.company', string='Company',
        related='contract_id.company_id', store=True
    )
    parent_version_id = fields.Many2one(
        'clm.contract.version', string='Parent Version',
        help='The version this version was derived from (ancestry).'
    )
    # No default: a default of 1 was sent by the form on every new line, which
    # defeated the per-contract auto-numbering in create().
    version_number = fields.Integer(
        string='Version Number', readonly=True, copy=False,
        help='Assigned automatically per contract when the version is created.'
    )
    version_type = fields.Selection([
        ('internal_draft', 'Internal Draft'),
        ('legal_draft', 'Legal Draft'),
        ('business_draft', 'Business Draft'),
        ('finance_draft', 'Finance Draft'),
        ('procurement_draft', 'Procurement Draft'),
        ('counterparty_draft', 'Counterparty Draft'),
        ('negotiated_draft', 'Negotiated Draft'),
        ('final_legal_draft', 'Final Legal Draft'),
        ('signature_draft', 'Signature Draft'),
        ('executed', 'Executed'),
    ], string='Version Type', required=True, default='internal_draft')

    # Odoo Documents as the primary document-management layer (PRD §12)
    document_id = fields.Many2one(
        'documents.document', string='Document', ondelete='set null',
        help='Reference to the Odoo Documents record for this version.'
    )
    # Upload shortcut: the file still lands in Odoo Documents and is linked
    # through document_id, so there is one document layer, not two.
    document_file = fields.Binary(
        string='Upload File', compute='_compute_document_file',
        inverse='_inverse_document_file',
        help='Upload a file for this version. It is stored in Odoo Documents '
             'and linked as this version\'s document.'
    )
    document_filename = fields.Char(
        string='Upload File Name', compute='_compute_document_file',
        inverse='_inverse_document_file',
    )

    created_by_id = fields.Many2one(
        'res.users', string='Created By', default=lambda self: self.env.uid
    )
    source = fields.Char(string='Source')
    is_counterparty = fields.Boolean(string='Counterparty Version', default=False)
    status = fields.Selection([
        ('draft', 'Draft'),
        ('active', 'Active'),
        ('superseded', 'Superseded'),
    ], string='Status', default='draft')
    negotiation_round = fields.Integer(string='Negotiation Round', default=0)
    change_summary = fields.Text(string='Change Summary')
    change_classification = fields.Selection([
        ('none', 'No Material Change'),
        ('immaterial', 'Immaterial Change'),
        ('material', 'Material Change'),
        ('ok_to_proceed', 'OK to Proceed'),
    ], string='Change Classification', default='none')

    # ─── Text Layer ──────────────────────────────────────────────────────────
    extracted_text = fields.Text(
        string='Extracted Text', readonly=True,
        help='Plain text of this version, retained for search, audit, and '
             'reprocessing without re-reading the original.'
    )
    text_source = fields.Selection([
        ('native', 'Native Text Layer'),
        ('ocr', 'OCR'),
        ('vlm', 'Vision Model Transcription'),
        ('none', 'No Text Source'),
    ], string='Text Source', default='none', readonly=True,
        help='Where the extracted text came from. Distinct from whether it '
             'succeeded — see Text Layer Status.'
    )
    text_layer_status = fields.Selection([
        ('available', 'Available'),
        ('unavailable', 'Unavailable'),
        ('failed', 'Failed'),
    ], string='Text Layer Status', default='unavailable', readonly=True)
    page_count = fields.Integer(string='Page Count', readonly=True)

    @api.depends('version_number', 'version_type', 'contract_id.name')
    def _compute_display_name(self):
        """Name a version the way the UI already talks about it.

        Without this, Odoo's fallback for a model with no `name` field renders
        every relation to a version as "clm.contract.version,787".
        """
        type_labels = dict(self._fields['version_type']._description_selection(self.env))
        for version in self:
            parts = [f"v{version.version_number}" if version.version_number else _("New version")]
            label = type_labels.get(version.version_type)
            if label:
                parts.append(label)
            name = " · ".join(parts)
            # Outside its own contract's form a bare "v2" is ambiguous, so the
            # contract leads when we are not already scoped to one.
            if version.contract_id and not self.env.context.get('clm_hide_contract_in_name'):
                name = f"{version.contract_id.name} — {name}"
            version.display_name = name

    def _compute_document_file(self):
        for version in self:
            version.document_file = False
            version.document_filename = False

    def _inverse_document_file(self):
        for version in self:
            if not version.document_file:
                continue
            contract = version.contract_id
            document = self.env['documents.document'].create({
                'name': version.document_filename or 'Contract_v%s' % version.version_number,
                'type': 'binary',
                'datas': version.document_file,
                'folder_id': contract.folder_id.id or False,
                'res_model': 'clm.contract',
                'res_id': contract.id,
            })
            version.document_id = document

    @api.constrains('parent_version_id', 'contract_id')
    def _check_ancestry_within_contract(self):
        """A version can only descend from a version of the same contract."""
        for version in self.sudo():
            parent = version.parent_version_id
            if parent and parent.contract_id != version.contract_id:
                raise ValidationError(
                    "Version %s of '%s' cannot have a parent from another contract ('%s')."
                    % (version.version_number, version.contract_id.display_name,
                       parent.contract_id.display_name)
                )

    # -------------------------------------------------------------------------
    # Executed version immutability (PRD §37)
    # -------------------------------------------------------------------------

    def _is_executed(self):
        return any(v.version_type == 'executed' for v in self)

    def write(self, vals):
        if self._is_executed() and not self.env.su:
            raise UserError(
                "Executed contract versions are immutable and cannot be modified."
            )
        # A new file must be re-read; a prior failed extraction must not stick forever.
        if 'document_id' in vals:
            vals = dict(vals)
            vals.setdefault('extracted_text', False)
            vals.setdefault('text_source', 'none')
            vals.setdefault('text_layer_status', 'unavailable')
            vals.setdefault('page_count', 0)
        return super().write(vals)

    def unlink(self):
        if self._is_executed() and not self.env.su:
            raise UserError(
                "Executed contract versions cannot be deleted."
            )
        return super().unlink()

    def action_activate(self):
        for version in self:
            if version.status != 'draft':
                raise UserError('Only draft versions can be activated.')
            # Only one working version is current: activating a newer one
            # supersedes older active drafts. Executed versions are immutable
            # and stay as they are.
            older = version.contract_id.version_ids.filtered(
                lambda v: v.status == 'active'
                and v.version_number < version.version_number
                and v.version_type != 'executed'
            )
            version.status = 'active'
            older.write({'status': 'superseded'})
            version.contract_id.message_post(
                body='Contract version %s activated (%s).' % (
                    version.version_number,
                    version.document_id.name or 'no document',
                ),
            )
        return True

    def action_supersede(self):
        # A draft can be superseded too: that is how a version that should not
        # go forward (for example an unwanted revision) is set aside.
        for version in self:
            if version.status not in ('draft', 'active'):
                raise UserError('Only draft or active versions can be superseded.')
            version.status = 'superseded'
        return True

    def _ensure_extracted_text(self):
        """Return this version's plain text, reading the document once if needed.

        The text layer is normally filled in by an AI job. A contract that was
        never run through AI still has its wording inside its own file, and the
        final-contract export needs it. This reads it through DocumentReader —
        the module's single owner of content extraction — and caches it in the
        existing extracted_text field, so contract content is never stored twice.

        Scans and images return '': turning those into text needs OCR or the
        multimodal pipeline, which is the AI job's job, not an export's.
        """
        self.ensure_one()
        if self.extracted_text:
            return self.extracted_text
        # Allow retry after a new document is linked, or if libs were installed later.
        # Only skip when the current file was already probed and marked unavailable.
        if self.text_layer_status == 'failed' and not self.document_id:
            return ''

        from ..services.document_reader import NativeDocumentReader

        document = self.sudo().document_id
        attachment = document.attachment_id if document else False
        if not attachment or not attachment.datas:
            return ''

        reader = NativeDocumentReader(self.env)
        if not reader.can_read(attachment):
            return ''

        try:
            payload = reader.read(attachment)
        except Exception as error:  # noqa: BLE001 — a bad file must not block export
            _logger.warning(
                'NextContract: could not extract text from version %s (%s): %s',
                self.version_number, attachment.name, error,
            )
            # sudo(): executed versions are immutable, but their text layer is
            # extraction metadata, not contract content.
            self.sudo().write({'text_layer_status': 'failed'})
            return ''

        self.sudo().write({
            'extracted_text': payload.text or False,
            'text_source': payload.text_source,
            'text_layer_status': 'available' if payload.text else 'unavailable',
            'page_count': payload.page_count,
        })
        return payload.text or ''

    # -------------------------------------------------------------------------
    # Auto-increment version number
    # -------------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('contract_id') and not vals.get('version_number'):
                self.env.cr.execute(
                    "SELECT COALESCE(MAX(version_number), 0) + 1 FROM clm_contract_version WHERE contract_id = %s",
                    [vals['contract_id']]
                )
                vals['version_number'] = self.env.cr.fetchone()[0]
        return super().create(vals_list)
