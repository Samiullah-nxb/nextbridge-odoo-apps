# Part of NextContract. See LICENSE file for full copyright and licensing details.

from urllib.parse import quote

from odoo import api, fields, models
from odoo.exceptions import UserError


class ClmTemplateBaseDocxVersion(models.Model):
    """Template Version snapshot of a Document Template File.

    Rules (enforced at ORM level):
    - Only one version is Active per template (``template.base_document_id``).
    - Once a version has been used by any contract, its DOCX file is immutable;
      further edits must create a new version.
    - Unused versions may be soft-deleted (``active=False``); used / Active /
      original versions cannot be deleted.
    - Soft-deleted rows stay in the database so historical contract pins remain
      valid, but they are hidden from the Template Versions list.
    """

    _name = 'clm.template.base.docx.version'
    _description = 'Template Version'
    _order = 'version_number desc, id desc'

    template_id = fields.Many2one(
        'clm.template',
        required=True,
        ondelete='cascade',
        index=True,
    )
    version_number = fields.Integer(required=True, default=1, index=True)
    revision_label = fields.Char(
        string='Version #',
        compute='_compute_revision_label',
        help='Public revision after the original (v1, v2, …). Empty for the original.',
    )
    name = fields.Char(string='Document Template', required=True)
    document_id = fields.Many2one(
        'documents.document',
        string='Document Template File',
        required=True,
        ondelete='restrict',
    )
    is_original = fields.Boolean(
        string='Original',
        default=False,
        help='First Document Template File linked to this template. Never overwritten or edited.',
    )
    is_active = fields.Boolean(
        string='Active',
        compute='_compute_is_active',
        inverse='_inverse_is_active',
        help='Only one Template Version can be active. Click the checkbox to switch.',
    )
    active = fields.Boolean(
        string='Active Record',
        default=True,
        help='Soft-delete flag. False hides the version from the list while '
             'keeping the Document Template File for historical contracts.',
    )
    is_used = fields.Boolean(
        string='Used by Contracts',
        compute='_compute_is_used',
        help='True when at least one contract generation pinned this Template Version. '
             'Used versions are immutable and cannot be deleted.',
    )
    status_label = fields.Char(
        string='Status',
        compute='_compute_status_label',
    )

    source = fields.Selection(
        [
            ('upload', 'Linked from Documents'),
            ('editor', 'Saved from editor'),
            ('restore', 'Restored from version'),
        ],
        default='upload',
        required=True,
    )
    note = fields.Char(string='Note')

    _uniq_template_version = models.Constraint(
        'unique(template_id, version_number)',
        'Version number must be unique per template.',
    )
    _uniq_template_document = models.Constraint(
        'unique(template_id, document_id)',
        'Each Documents file can appear only once in a template version list.',
    )

    @api.depends('version_number', 'is_original', 'template_id')
    def _compute_revision_label(self):
        for row in self:
            if not row.template_id:
                row.revision_label = ''
                continue
            rev = row.template_id._base_docx_revision_number(
                row.version_number, is_original=row.is_original,
            )
            row.revision_label = str(rev) if rev is not None else ''

    @api.depends('document_id', 'template_id', 'template_id.base_document_id')
    def _compute_is_active(self):
        for row in self:
            row.is_active = bool(
                row.document_id
                and row.template_id.base_document_id
                and row.document_id == row.template_id.base_document_id
            )

    @api.depends('is_active', 'is_used')
    def _compute_status_label(self):
        for row in self:
            if row.is_active:
                row.status_label = 'Active'
            elif row.is_used:
                row.status_label = 'Used'
            else:
                row.status_label = 'Available'

    def _compute_is_used(self):
        used_ids = self._clm_used_version_ids()
        for row in self:
            row.is_used = row.id in used_ids

    def _clm_used_version_ids(self):
        """Return ids of versions pinned by any contract generation / version."""
        if not self:
            return set()
        used = set()
        if (
            'clm.assembly.request' in self.env
            and 'base_docx_version_id' in self.env['clm.assembly.request']._fields
        ):
            rows = self.env['clm.assembly.request'].sudo().search_read(
                [('base_docx_version_id', 'in', self.ids)],
                ['base_docx_version_id'],
            )
            used.update(
                r['base_docx_version_id'][0]
                for r in rows if r.get('base_docx_version_id')
            )
        if (
            'clm.contract.version' in self.env
            and 'base_docx_version_id' in self.env['clm.contract.version']._fields
        ):
            rows = self.env['clm.contract.version'].sudo().search_read(
                [('base_docx_version_id', 'in', self.ids)],
                ['base_docx_version_id'],
            )
            used.update(
                r['base_docx_version_id'][0]
                for r in rows if r.get('base_docx_version_id')
            )
        return used

    def _inverse_is_active(self):
        """List / form checkbox writes: activate the ticked row."""
        for row in self:
            if row.is_active:
                row._activate_as_base()
            elif row.document_id == row.template_id.base_document_id:
                raise UserError(
                    "Tick another version’s Active checkbox to switch. "
                    "One Template Version must stay active."
                )

    def write(self, vals):
        locked = {'document_id', 'is_original', 'version_number'}
        if locked & set(vals):
            for row in self:
                if row.is_original or row.is_used:
                    if 'document_id' in vals:
                        new_id = vals['document_id']
                        if isinstance(new_id, models.BaseModel):
                            new_id = new_id.id
                        if new_id and new_id != row.document_id.id:
                            raise UserError(row._clm_immutable_message())
                    if row.is_original and 'is_original' in vals and not vals['is_original']:
                        raise UserError("The original flag cannot be removed.")
                    if (
                        row.is_original
                        and 'version_number' in vals
                        and vals['version_number'] != row.version_number
                    ):
                        raise UserError(
                            "The original Template Version number cannot be changed."
                        )
                    if (
                        row.is_used
                        and not row.is_original
                        and 'version_number' in vals
                        and vals['version_number'] != row.version_number
                    ):
                        raise UserError(row._clm_immutable_message())
        # Soft-delete (active → False) only when unused and not Active / original.
        if 'active' in vals and not vals['active']:
            for row in self:
                if row.is_used:
                    raise UserError(
                        "Template Version '%s' has been used by one or more contracts "
                        "and cannot be deleted."
                        % (row.display_name or row.name)
                    )
                if row.is_active:
                    raise UserError(
                        "Cannot delete the active Template Version. "
                        "Activate another version first."
                    )
                if row.is_original:
                    raise UserError(
                        "The original Document Template cannot be deleted."
                    )
        return super().write(vals)

    def _clm_immutable_message(self):
        self.ensure_one()
        if self.is_original:
            return (
                "The original Document Template File cannot be changed.\n\n"
                "Upload a new file or use Save as New Version."
            )
        return (
            "Template Version '%s' has been used by one or more contracts and "
            "cannot be edited or replaced.\n\n"
            "Save as New Version (or upload a new Document Template File) to "
            "create the next Template Version for new contracts."
        ) % (self.display_name or self.name)

    def _check_docx_mutable(self):
        """Raise if this version's DOCX file must not be overwritten."""
        self.ensure_one()
        if self.is_original or self.is_used:
            raise UserError(self._clm_immutable_message())

    def _activate_as_base(self):
        """Make this the only active Template Version."""
        self.ensure_one()
        if not self.active:
            raise UserError(
                "Cannot activate a deleted Template Version. "
                "Upload or save a new version instead."
            )
        self.template_id._check_template_edit_permission()
        if self.template_id.base_document_id != self.document_id:
            self.template_id.with_context(clm_activating_base_docx_version=True).write({
                'base_document_id': self.document_id.id,
            })
            if not self.is_original and self.source == 'upload':
                super(ClmTemplateBaseDocxVersion, self).write({'source': 'restore'})
        self.template_id.base_docx_version_ids.invalidate_recordset(
            ['is_active', 'status_label', 'is_used']
        )
        return True

    def action_set_active(self):
        """Make this Template Version active (no UI modal)."""
        self.ensure_one()
        if not self.is_active:
            self._activate_as_base()
        return True

    def action_edit(self):
        """Activate this Template Version and open it in the editor.

        The original Document Template File is never overwritten: the editor
        only offers Save as New Version when it is Active. Used versions are
        likewise immutable in place.
        """
        self.ensure_one()
        self.template_id._check_template_edit_permission()
        self._activate_as_base()
        tmpl = self.template_id
        return {
            'type': 'ir.actions.act_window',
            'name': tmpl.display_name,
            'res_model': 'clm.template',
            'res_id': tmpl.id,
            'view_mode': 'form',
            'views': [(False, 'form')],
            'target': 'current',
            'context': {
                'form_view_initial_mode': 'edit',
                'clm_open_base_docx_editor': True,
            },
        }

    def _preview_file_name(self):
        """Dialog / download label for this Template Version.

        Uses the version row name (``… — vN``) so Preview matches the list even
        when the linked Documents file still has an older upload filename.
        """
        self.ensure_one()
        label = (self.name or '').strip()
        if label:
            if not label.lower().endswith('.docx'):
                label = '%s.docx' % label
            return label
        return self.document_id.name or 'document.docx'

    def action_preview(self):
        self.ensure_one()
        kind = 'original' if self.is_original else 'previous_changed'
        return self.template_id._action_preview_base_docx(
            self.document_id,
            kind,
            preview_name=self._preview_file_name(),
        )

    def action_download(self):
        self.ensure_one()
        document = self.document_id
        attachment = document.attachment_id
        if not attachment:
            raise UserError("This Template Version has no file attachment.")
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

    def action_delete(self):
        """Soft-delete an unused, non-active Template Version.

        Sets ``active=False`` so the row disappears from the list while the
        Document Template File remains for any historical references.
        Used, Active, and original versions cannot be deleted.
        """
        self.ensure_one()
        if self.is_active:
            raise UserError(
                "Cannot delete the active Template Version. "
                "Activate another version first."
            )
        if self.is_original:
            raise UserError("The original Document Template cannot be deleted.")
        if self.is_used:
            raise UserError(
                "Template Version '%s' has been used by one or more contracts "
                "and cannot be deleted."
                % (self.display_name or self.name)
            )
        self.write({'active': False})
        return True

    def unlink(self):
        """Hard delete is blocked for protected rows; prefer ``action_delete``."""
        for row in self:
            if row.is_original:
                raise UserError("The original Document Template cannot be deleted.")
            if row.is_active:
                raise UserError(
                    "Cannot delete the active Template Version. "
                    "Activate another version first."
                )
            if row.is_used:
                raise UserError(
                    "Template Version '%s' has been used by one or more contracts "
                    "and cannot be deleted."
                    % (row.display_name or row.name)
                )
        return super().unlink()
