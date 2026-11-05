# Part of NextContract. See LICENSE file for full copyright and licensing details.

import re

from odoo import models, fields, api
from odoo.exceptions import AccessError, UserError

from odoo.addons.clm.services import docx_tag_editor as editor
from odoo.addons.clm.services.placeholder_catalog import catalog_for_ui
from odoo.addons.clm.services.clause_set_context import (
    compact_insert_lines_for_lines,
)


class ClmTemplate(models.Model):
    """
    Contract template library (PRD §23, §24, §25).

    Lifecycle: draft → approved → published → superseded / retired
    Published templates are protected from modification by non-Legal-Admin users (PRD §24).
    """
    _name = 'clm.template'
    _description = 'Contract Template'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    # -------------------------------------------------------------------------
    # Core metadata (PRD §23, §25)
    # -------------------------------------------------------------------------
    name = fields.Char(string='Template Name', required=True, tracking=True)
    contract_type_id = fields.Many2one(
        'clm.contract.type', string='Contract Type',
        required=True, tracking=True
    )

    content = fields.Html(
        string='Template Body',
        help='Raw template content with variable placeholders (e.g. {{partner_name}}).\''
    )
    base_document_id = fields.Many2one(
        'documents.document', string='Document Template',
        help='Active Document Template File used by the editor and draft assembly. '
             'Upload or pick a Documents file. Each Save as New Version / upload '
             'creates a Template Version named from this template '
             '(e.g. "Mutual NDA — Standard — v2"); the uploaded filename is kept '
             'on the Documents record.',
    )
    base_docx_upload = fields.Binary(
        string='Upload Document Template',
        attachment=False,
        copy=False,
        help='Upload a .docx Document Template File. Stored in Documents and set as the '
             'active Template Version. If a Document Template already exists, the upload is '
             'added as a new version (v1 / original file is never replaced).',
    )
    base_docx_upload_filename = fields.Char(
        string='Upload Filename',
        copy=False,
    )
    base_document_original_id = fields.Many2one(
        'documents.document',
        string='Original Document Template File',
        copy=False,
        help='First Document Template File linked to this template (v1). Never overwritten by Saves.',
    )
    base_document_previous_id = fields.Many2one(
        'documents.document',
        string='Previous Active Template Version',
        copy=False,
        help='Document Template File that was active before the latest switch or Save.',
    )
    base_docx_version_ids = fields.One2many(
        'clm.template.base.docx.version',
        'template_id',
        string='Template Versions',
        copy=False,
    )
    base_docx_version_count = fields.Integer(
        compute='_compute_has_base_document',
    )
    has_base_document = fields.Boolean(
        string='Has Document Template',
        compute='_compute_has_base_document',
    )
    has_previous_base_document = fields.Boolean(
        string='Has Previous Template Version',
        compute='_compute_has_base_document',
    )
    # Dummy field for the OWL Document Template preview editor widget.
    docx_editor_anchor = fields.Char(
        string='Document Template Editor',
        compute='_compute_docx_editor_anchor',
    )

    @api.depends(
        'base_document_id',
        'base_document_previous_id',
        'base_docx_version_ids',
    )
    def _compute_has_base_document(self):
        for tmpl in self:
            tmpl.has_base_document = bool(tmpl.base_document_id)
            tmpl.has_previous_base_document = bool(tmpl.base_document_previous_id)
            tmpl.base_docx_version_count = len(tmpl.base_docx_version_ids)

    @api.depends('base_document_id', 'base_document_id.attachment_id')
    def _compute_docx_editor_anchor(self):
        for tmpl in self:
            tmpl.docx_editor_anchor = (
                'docx:%s' % tmpl.base_document_id.id if tmpl.base_document_id else False
            )

    @api.constrains('base_document_id')
    def _check_base_document(self):
        for tmpl in self:
            if tmpl.base_document_id:
                name = tmpl.base_document_id.name or ''
                mimetype = tmpl.base_document_id.mimetype or ''
                if not name.endswith('.docx') and 'wordprocessingml.document' not in mimetype:
                    # Provide a warning/error about basic validation
                    raise UserError(
                        "The selected Base Template does not appear to be a valid DOCX file. "
                        "Please select a .docx file."
                    )

    # Versioning (PRD §25)
    version_number = fields.Integer(string='Version', default=1, readonly=True)
    effective_date = fields.Date(string='Effective Date', tracking=True)
    owner_id = fields.Many2one('res.users', string='Owner', tracking=True)
    approved_by_id = fields.Many2one('res.users', string='Approved By', tracking=True)
    approval_date = fields.Date(string='Approval Date', tracking=True)
    superseded_template_id = fields.Many2one(
        'clm.template', string='Supersedes', ondelete='set null'
    )
    change_description = fields.Text(string='Change Description')

    # Lifecycle
    state = fields.Selection([
        ('draft', 'Draft'),
        ('approved', 'Approved'),
        ('published', 'Published'),
        ('superseded', 'Superseded'),
        ('retired', 'Retired'),
    ], string='Status', default='draft', tracking=True)

    # Ordered clause lines (junction to clm.clause)
    clause_line_ids = fields.One2many(
        'clm.template.clause.line', 'template_id', string='Clause Lines'
    )
    clause_count = fields.Integer(
        string='Clauses', compute='_compute_clause_count'
    )

    def _compute_clause_count(self):
        for tmpl in self:
            tmpl.clause_count = len(tmpl.clause_line_ids)

    # -------------------------------------------------------------------------
    # Published template write protection (PRD §24)
    # -------------------------------------------------------------------------
    def _check_template_edit_permission(self):
        is_legal_admin = self.env.user.has_group('clm.group_clm_legal_admin')
        for tmpl in self:
            if tmpl.state == 'published' and not is_legal_admin and not self.env.su:
                raise AccessError(
                    f"Template '{tmpl.name}' is published and can only be modified "
                    "by Legal Administrators."
                )

    def write(self, vals):
        self._check_template_edit_permission()
        vals = dict(vals)
        upload = vals.pop('base_docx_upload', None) if 'base_docx_upload' in vals else None
        upload_name = (
            vals.pop('base_docx_upload_filename', None)
            if 'base_docx_upload_filename' in vals else None
        )
        # Ignore empty clears from the binary widget when no new file was picked.
        if upload is False or upload is None or upload == '':
            upload = None
        if 'base_document_original_id' in vals:
            for tmpl in self:
                current = tmpl.base_document_original_id
                new_id = vals.get('base_document_original_id') or False
                if isinstance(new_id, models.BaseModel):
                    new_id = new_id.id
                if current and new_id and new_id != current.id:
                    raise UserError(
                        "The original Document Template File cannot be changed. "
                        "Add a new Template Version instead."
                    )
                if current and not new_id:
                    raise UserError("The original Document Template File cannot be cleared.")
        activating = self.env.context.get('clm_activating_base_docx_version')
        skipping = self.env.context.get('clm_skip_base_docx_history')
        history_by_id = {}
        if 'base_document_id' in vals and not skipping:
            new_id = vals.get('base_document_id') or False
            if isinstance(new_id, models.BaseModel):
                new_id = new_id.id
            for tmpl in self:
                old_id = tmpl.base_document_id.id if tmpl.base_document_id else False
                patch = {}
                if new_id and old_id and old_id != new_id:
                    patch['base_document_previous_id'] = old_id
                if new_id and not tmpl.base_document_original_id:
                    patch['base_document_original_id'] = new_id
                if patch:
                    history_by_id[tmpl.id] = patch
        res = super().write(vals)
        if 'base_document_id' in vals and not skipping:
            new_id = vals.get('base_document_id') or False
            if isinstance(new_id, models.BaseModel):
                new_id = new_id.id
            for tmpl in self:
                if history_by_id.get(tmpl.id):
                    super(ClmTemplate, tmpl).write(history_by_id[tmpl.id])
                if new_id:
                    tmpl._register_base_docx_document(
                        tmpl.base_document_id,
                        source='restore' if activating else 'upload',
                        activate=False,  # already written on template
                    )
        if upload:
            for tmpl in self:
                tmpl._apply_base_docx_upload(upload, upload_name)
        return res

    @api.model_create_multi
    def create(self, vals_list):
        pending_uploads = []
        cleaned = []
        for vals in vals_list:
            vals = dict(vals)
            pending_uploads.append((
                vals.pop('base_docx_upload', None),
                vals.pop('base_docx_upload_filename', None),
            ))
            cleaned.append(vals)
        records = super().create(cleaned)
        for rec, (data, name) in zip(records, pending_uploads):
            if data:
                rec._apply_base_docx_upload(data, name)
            elif rec.base_document_id:
                if not rec.base_document_original_id:
                    super(ClmTemplate, rec).write({
                        'base_document_original_id': rec.base_document_id.id,
                    })
                rec._register_base_docx_document(
                    rec.base_document_id,
                    source='upload',
                    activate=False,
                    force_original=True,
                )
        return records

    def unlink(self):
        self._check_template_edit_permission()
        return super().unlink()

    # -------------------------------------------------------------------------
    # Lifecycle actions
    # -------------------------------------------------------------------------
    def action_approve(self):
        is_legal_admin = self.env.user.has_group('clm.group_clm_legal_admin')
        for tmpl in self:
            if tmpl.state != 'draft':
                raise UserError("Only draft templates can be approved.")
            if not is_legal_admin and not self.env.su:
                raise AccessError("Only Legal Administrators can approve templates.")
            tmpl.write({
                'state': 'approved',
                'approved_by_id': self.env.uid,
                'approval_date': fields.Date.today(),
            })

    def action_publish(self):
        is_legal_admin = self.env.user.has_group('clm.group_clm_legal_admin')
        for tmpl in self:
            if tmpl.state != 'approved':
                raise UserError("Only approved templates can be published.")
            if not is_legal_admin and not self.env.su:
                raise AccessError("Only Legal Administrators can publish templates.")
            tmpl.state = 'published'

    def action_supersede(self, new_template=None):
        is_legal_admin = self.env.user.has_group('clm.group_clm_legal_admin')
        for tmpl in self:
            if tmpl.state != 'published':
                raise UserError("Only published templates can be superseded.")
            if not is_legal_admin and not self.env.su:
                raise AccessError("Only Legal Administrators can supersede templates.")
            tmpl.state = 'superseded'

    def action_retire(self):
        is_legal_admin = self.env.user.has_group('clm.group_clm_legal_admin')
        for tmpl in self:
            if tmpl.state not in ('published', 'superseded'):
                raise UserError("Only published or superseded templates can be retired.")
            if not is_legal_admin and not self.env.su:
                raise AccessError("Only Legal Administrators can retire templates.")
            tmpl.state = 'retired'

    # -------------------------------------------------------------------------
    # Document Template preview editor (in-form tag placement)
    # -------------------------------------------------------------------------
    def _clm_placeholder_catalog(self):
        self.ensure_one()
        lines = self.clause_line_ids.filtered('enabled')
        return catalog_for_ui(clause_lines=lines)

    def _clm_clause_keys(self):
        self.ensure_one()
        keys = set()
        for line in self.clause_line_ids.filtered('enabled'):
            clause = line.clause_id
            code = ''
            if clause and 'code' in clause._fields:
                code = (clause.code or '').strip()
            if not code and clause:
                code = (clause.category or '').strip()
            if code:
                keys.add(code)
        return keys

    def _clm_validate_docx_texts(self, texts):
        self.ensure_one()
        require_loop = bool(self.clause_line_ids.filtered('enabled'))
        return editor.validate_paragraphs(
            texts,
            require_clause_loop=require_loop,
            clause_keys=self._clm_clause_keys(),
        )

    def _clm_compact_clause_insert_lines(self):
        """Title + content tags for each enabled clause line."""
        self.ensure_one()
        return compact_insert_lines_for_lines(self.clause_line_ids.filtered('enabled'))

    def _get_base_docx_bytes(self):
        self.ensure_one()
        document = self.base_document_id
        if not document:
            raise UserError("Set a Document Template first.")
        attachment = document.attachment_id.with_context(bin_size=False)
        if not attachment:
            raise UserError("The Document Template File has no file attachment.")
        raw = editor.bytes_from_b64(attachment.datas)
        if not raw or raw[:2] != b'PK':
            raise UserError(
                "The Document Template File is empty or is not a valid .docx file."
            )
        return raw, attachment

    def _next_base_docx_version_number(self):
        self.ensure_one()
        Version = self.env['clm.template.base.docx.version'].with_context(active_test=False)
        numbers = Version.search([('template_id', '=', self.id)]).mapped('version_number')
        return (max(numbers) if numbers else 0) + 1

    def _document_template_display_name(self):
        """Document Template identity used in version labels (not Contract Type).

        Contract Type stays a classification (e.g. NDA). Version rows are named
        from this template's own name (e.g. ``Mutual NDA — Standard — v2``).
        """
        self.ensure_one()
        return (self.name or '').strip() or 'Document Template'

    def _base_docx_clean_stem(self):
        """Legacy stem helper — prefer ``_document_template_display_name``."""
        return self._document_template_display_name()

    def _base_docx_revision_number(self, version_number, *, is_original=False):
        """Public revision index after the original: 1, 2, 3…

        The original upload has no revision number. Internal ``version_number``
        still uses 1 for the original and 2+ for later snapshots, so the
        public revision is ``version_number - 1``.
        """
        version_number = int(version_number)
        if is_original or version_number <= 1:
            return None
        return version_number - 1

    def _base_docx_version_filename(self, version_number, *, is_original=False):
        """Documents filename: original has no suffix; later ``{Name} — v{N}.docx``."""
        name = self._document_template_display_name()
        rev = self._base_docx_revision_number(
            version_number, is_original=is_original,
        )
        if rev is None:
            return '%s.docx' % name
        return '%s — v%s.docx' % (name, rev)

    def _base_docx_version_label(self, version_number, *, is_original=False, stamp=None):
        """Template Version label: original is bare name; later ``{Name} — v{N}``."""
        name = self._document_template_display_name()
        rev = self._base_docx_revision_number(
            version_number, is_original=is_original,
        )
        if rev is None:
            return name
        return '%s — v%s' % (name, rev)

    def _register_base_docx_document(
        self,
        document,
        *,
        source='upload',
        activate=True,
        force_original=False,
        note=None,
        name=None,
    ):
        """Ensure ``document`` appears in the version list (idempotent)."""
        self.ensure_one()
        if not document:
            return self.env['clm.template.base.docx.version']
        Version = self.env['clm.template.base.docx.version']
        existing = Version.search([
            ('template_id', '=', self.id),
            ('document_id', '=', document.id),
        ], limit=1)
        if existing:
            if activate and self.base_document_id != document:
                self.with_context(
                    clm_skip_base_docx_history=True,
                    clm_activating_base_docx_version=True,
                ).write({'base_document_id': document.id})
            return existing

        has_original = bool(self.base_docx_version_ids.filtered('is_original'))
        is_original = bool(force_original or not has_original)
        if is_original:
            version_number = 1
        else:
            version_number = self._next_base_docx_version_number()

        label = name or self._base_docx_version_label(
            version_number,
            is_original=is_original,
        )
        version = Version.create({
            'template_id': self.id,
            'version_number': version_number,
            'name': label,
            'document_id': document.id,
            'is_original': is_original,
            'source': source,
            'note': note,
        })
        if is_original and not self.base_document_original_id:
            super(ClmTemplate, self).write({
                'base_document_original_id': document.id,
            })
        if activate and self.base_document_id != document:
            self.with_context(clm_skip_base_docx_history=True).write({
                'base_document_id': document.id,
            })
        return version

    def _create_base_docx_document(self, datas_b64, filename, folder_id=False):
        """Create a new Documents file owned by this template (never mutates others)."""
        self.ensure_one()
        if not str(filename).lower().endswith('.docx'):
            filename = '%s.docx' % filename
        mimetype = (
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        )
        vals = {
            'name': filename,
            'datas': datas_b64,
            'mimetype': mimetype,
            'res_model': 'clm.template',
            'res_id': self.id,
        }
        if folder_id:
            vals['folder_id'] = folder_id
        return self.env['documents.document'].create(vals)

    def _apply_base_docx_upload(self, datas_b64, filename=None):
        """Turn a direct .docx upload into a Documents file + Template Version."""
        self.ensure_one()
        self._check_template_edit_permission()
        filename = (filename or 'Document Template.docx').strip() or 'Document Template.docx'
        if not filename.lower().endswith('.docx'):
            filename = '%s.docx' % filename
        raw = editor.bytes_from_b64(datas_b64)
        if not raw or raw[:2] != b'PK':
            raise UserError("Please upload a valid .docx file.")
        datas = editor.b64_from_bytes(raw)
        folder_id = (
            self.base_document_id.folder_id.id
            if self.base_document_id and self.base_document_id.folder_id
            else False
        )

        # Later uploads become a new version — never overwrite the original.
        # Store the Documents file under the same ``{Name} — vN.docx`` label as
        # the Template Version row so Preview / Download match the list.
        if self.base_document_original_id or self.base_docx_version_ids:
            if self.base_document_id and not self.base_docx_version_ids:
                self._register_base_docx_document(
                    self.base_document_id,
                    source='upload',
                    activate=False,
                    force_original=True,
                )
            version_number = self._next_base_docx_version_number()
            stamp = fields.Datetime.context_timestamp(
                self, fields.Datetime.now()
            ).strftime('%Y-%m-%d %H:%M')
            version_filename = self._base_docx_version_filename(version_number)
            new_doc = self._create_base_docx_document(
                datas, version_filename, folder_id=folder_id,
            )
            self.env['clm.template.base.docx.version'].create({
                'template_id': self.id,
                'version_number': version_number,
                'name': self._base_docx_version_label(version_number, stamp=stamp),
                'document_id': new_doc.id,
                'is_original': False,
                'source': 'upload',
                'note': 'Uploaded as new Template Version (%s)' % filename,
            })
            previous_id = self.base_document_id.id if self.base_document_id else False
            self.with_context(clm_skip_base_docx_history=True).write({
                'base_document_id': new_doc.id,
                'base_document_previous_id': previous_id or False,
                'base_docx_upload': False,
                'base_docx_upload_filename': False,
            })
            return new_doc

        # First upload: keep the user's filename when provided; otherwise
        # use the bare Document Template name (no ``— v1`` suffix).
        first_name = filename if filename else self._base_docx_version_filename(
            1, is_original=True,
        )
        if first_name.lower().endswith('.docx'):
            pass
        else:
            first_name = '%s.docx' % first_name
        # Prefer Document Template name over a generic upload label.
        if not filename or filename in (
            'Document Template.docx', 'Base DOCX.docx',
        ):
            first_name = self._base_docx_version_filename(1, is_original=True)
        new_doc = self._create_base_docx_document(
            datas,
            first_name,
            folder_id=folder_id,
        )
        self.write({
            'base_document_id': new_doc.id,
            'base_docx_upload': False,
            'base_docx_upload_filename': False,
        })
        return new_doc

    def _active_base_docx_version(self):
        """Return the version row for the currently active Template Version, if any."""
        self.ensure_one()
        return self.base_docx_version_ids.filtered(
            lambda v: v.document_id == self.base_document_id
        )[:1]

    def _overwrite_active_base_docx(self, updated_bytes, *, note=None):
        """
        Persist editor changes onto the currently active Template Version file.

        Refuses to overwrite the original or any version already used by a
        contract — use ``_commit_base_docx_revision`` instead.
        """
        self.ensure_one()
        self._check_template_edit_permission()
        current = self.base_document_id
        if not current:
            raise UserError("Set a Document Template first.")
        active_version = self._active_base_docx_version()
        if active_version:
            active_version._check_docx_mutable()
        attachment = current.attachment_id
        if not attachment:
            raise UserError("The Document Template File has no file attachment.")
        attachment.with_context(bin_size=False).write({
            'datas': editor.b64_from_bytes(updated_bytes),
        })
        stamp = fields.Datetime.context_timestamp(
            self, fields.Datetime.now()
        ).strftime('%Y-%m-%d %H:%M')
        if active_version:
            active_version.write({
                'note': note or 'Updated from Document Template editor — %s' % stamp,
                'source': 'editor',
            })
        return self.get_docx_editor_data()

    def _commit_base_docx_revision(self, updated_bytes, *, note=None):
        """
        Save editor changes as a NEW Template Version.

        Never overwrites the original (or any prior) Documents attachment.
        """
        self.ensure_one()
        self._check_template_edit_permission()
        current = self.base_document_id
        if not current:
            raise UserError("Set a Document Template first.")

        version_number = self._next_base_docx_version_number()
        # If the list was empty, register current as original first.
        if not self.base_docx_version_ids:
            self._register_base_docx_document(
                current,
                source='upload',
                activate=False,
                force_original=True,
            )
            version_number = self._next_base_docx_version_number()

        stamp = fields.Datetime.context_timestamp(
            self, fields.Datetime.now()
        ).strftime('%Y-%m-%d %H:%M')
        filename = self._base_docx_version_filename(version_number)
        new_doc = self._create_base_docx_document(
            editor.b64_from_bytes(updated_bytes),
            filename,
            folder_id=current.folder_id.id if current.folder_id else False,
        )
        Version = self.env['clm.template.base.docx.version']
        Version.create({
            'template_id': self.id,
            'version_number': version_number,
            'name': self._base_docx_version_label(version_number, stamp=stamp),
            'document_id': new_doc.id,
            'is_original': False,
            'source': 'editor',
            'note': note or 'Saved as New Version from Document Template editor',
        })
        previous_id = current.id
        self.with_context(clm_skip_base_docx_history=True).write({
            'base_document_id': new_doc.id,
            'base_document_previous_id': previous_id,
        })
        return self.get_docx_editor_data()

    def _persist_base_docx_bytes(self, updated_bytes, *, mode='new', note=None):
        """Dispatch editor save: ``inplace`` overwrite or ``new`` version.

        In-place save on a used / original active version is upgraded to a new
        Template Version so existing contracts keep their pinned file.
        """
        mode = (mode or 'new').strip().lower()
        if mode == 'inplace':
            active_version = self._active_base_docx_version()
            if active_version and (active_version.is_original or active_version.is_used):
                return self._commit_base_docx_revision(
                    updated_bytes,
                    note=note or (
                        'Saved as New Version because the active Template Version '
                        'is immutable (original or used by contracts)'
                    ),
                )
            return self._overwrite_active_base_docx(updated_bytes, note=note)
        if mode in ('new', 'new_version'):
            return self._commit_base_docx_revision(updated_bytes, note=note)
        raise UserError("Unknown Document Template save mode: %s" % mode)

    def _action_preview_base_docx(self, document, kind, *, preview_name=None):
        """Open the read-only preview modal for a stored Template Version.

        Converts the DOCX to PDF (LibreOffice) and shows the same native PDF.js
        viewer as contract Preview — Fullscreen, 100% zoom, open in new tab.

        ``preview_name`` is the dialog title file label (normally the Template
        Version name + ``.docx``). Prefer that over ``document.name`` — uploads
        and older saves can leave the Documents file with a stale filename
        while the version row already shows the correct ``— vN`` label.
        """
        self.ensure_one()
        if not document:
            raise UserError("No Document Template File is available for this action.")
        file_name = (preview_name or document.name or 'document.docx').strip()
        if file_name and not file_name.lower().endswith('.docx'):
            file_name = '%s.docx' % file_name
        wizard = self.env['clm.base.docx.preview.wizard'].create({
            'template_id': self.id,
            'document_id': document.id,
            'kind': kind,
            'file_name': file_name,
        })
        wizard.prepare_pdf_preview()
        dialog_title = 'Preview(%s)' % file_name
        return {
            'type': 'ir.actions.act_window',
            'name': dialog_title,
            'display_name': dialog_title,
            'res_model': 'clm.base.docx.preview.wizard',
            'res_id': wizard.id,
            'view_mode': 'form',
            'views': [(
                self.env.ref('clm.view_clm_base_docx_preview_wizard_form').id,
                'form',
            )],
            'target': 'new',
            'context': {
                'dialog_size': 'extra-large',
            },
        }

    def action_preview_original_base_docx(self):
        """Preview + download Template Version v1."""
        self.ensure_one()
        document = self.base_document_original_id or self.base_document_id
        return self._action_preview_base_docx(document, 'original')

    def action_preview_previous_changed_base_docx(self):
        """Preview + download the previous Template Version."""
        self.ensure_one()
        return self._action_preview_base_docx(
            self.base_document_previous_id, 'previous_changed'
        )

    def get_docx_editor_data(self):
        """RPC payload for the Document Template preview editor widget."""
        self.ensure_one()
        raw, _attachment = self._get_base_docx_bytes()
        preview = editor.extract_document_preview(raw)
        paragraphs = preview['paragraphs']
        errors, warnings, issue_spans = self._clm_validate_docx_texts(
            [row['text'] for row in paragraphs],
        )
        active_version = self._active_base_docx_version()
        is_original_active = bool(active_version.is_original) if active_version else False
        is_used_active = bool(active_version.is_used) if active_version else False
        # Original and used versions are immutable — Save as New Version only.
        readonly = (
            self.state == 'published'
            and not self.env.user.has_group('clm.group_clm_legal_admin')
        )
        return {
            'template_id': self.id,
            'document_name': self.base_document_id.name or self._document_template_display_name(),
            'base_document_id': self.base_document_id.id,
            'version_number': active_version.version_number if active_version else False,
            'version_name': active_version.name if active_version else False,
            'is_original_active': is_original_active,
            'is_used_active': is_used_active,
            'can_save_inplace': bool(
                not is_original_active and not is_used_active and not readonly
            ),
            'version_count': len(self.base_docx_version_ids),
            'headers': preview.get('headers') or [],
            'paragraphs': paragraphs,
            'document_html': preview.get('document_html') or editor.build_editor_html(paragraphs),
            'tags': self._clm_placeholder_catalog(),
            'clause_set_compact_lines': self._clm_compact_clause_insert_lines(),
            'errors': errors,
            'warnings': warnings,
            'issue_spans': issue_spans,
            'readonly': readonly,
        }

    def save_docx_paragraphs(self, paragraphs, mode='new'):
        """
        Persist edited content.

        ``mode``:
        - ``new`` / ``new_version``: create a new Template Version (default)
        - ``inplace``: overwrite the currently active non-original file

        Accepts either:
        - list of paragraph dicts {index, text, html, style_css}, or
        - a single HTML document string from the Wysiwyg editor (via
          kwargs / wrapped dict {'document_html': '...'}).
        """
        self.ensure_one()
        self._check_template_edit_permission()
        raw, _attachment = self._get_base_docx_bytes()

        # Wysiwyg full-document save
        if isinstance(paragraphs, dict) and paragraphs.get('document_html') is not None:
            payloads = editor.payloads_from_editor_html(paragraphs.get('document_html'))
            ordered_texts = [p.get('text') or '' for p in payloads]
            errors, _warnings, _issue_spans = self._clm_validate_docx_texts(ordered_texts)
            if errors:
                raise UserError('\n'.join(errors))
            updated = editor.replace_body_from_payloads(raw, payloads)
            return self._persist_base_docx_bytes(updated, mode=mode)

        payloads_by_index = {}
        ordered_texts = []
        for item in paragraphs or []:
            if isinstance(item, dict):
                index = int(item.get('index', len(payloads_by_index)))
                text = item.get('text') or ''
                payloads_by_index[index] = {
                    'text': text,
                    'html': item.get('html') or '',
                    'style_css': item.get('style_css') or item.get('styleCss') or '',
                }
            else:
                index = len(payloads_by_index)
                text = item or ''
                payloads_by_index[index] = {'text': text}
            ordered_texts.append(text)

        errors, _warnings, _issue_spans = self._clm_validate_docx_texts(ordered_texts)
        if errors:
            raise UserError('\n'.join(errors))

        updated = editor.apply_paragraph_payloads(raw, payloads_by_index)
        return self._persist_base_docx_bytes(updated, mode=mode)

    def save_docx_document_html(self, document_html, mode='new'):
        """Persist full Wysiwyg HTML (``inplace`` or ``new`` version)."""
        self.ensure_one()
        self._check_template_edit_permission()
        raw, _attachment = self._get_base_docx_bytes()
        payloads = editor.payloads_from_editor_html(document_html)
        ordered_texts = [p.get('text') or '' for p in payloads]
        errors, _warnings, _issue_spans = self._clm_validate_docx_texts(ordered_texts)
        if errors:
            raise UserError('\n'.join(errors))
        updated = editor.replace_body_from_payloads(raw, payloads)
        return self._persist_base_docx_bytes(updated, mode=mode)

    def validate_docx_paragraphs(self, paragraphs):
        """Validate without saving. Returns {errors, warnings}."""
        self.ensure_one()
        texts = []
        for item in paragraphs or []:
            if isinstance(item, dict):
                texts.append(item.get('text') or '')
            else:
                texts.append(item or '')
        errors, warnings, issue_spans = self._clm_validate_docx_texts(texts)
        return {
            'errors': errors,
            'warnings': warnings,
            'issue_spans': issue_spans,
        }
