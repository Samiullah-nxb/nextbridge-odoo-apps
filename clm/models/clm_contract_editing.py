# Part of NextContract. See LICENSE file for full copyright and licensing details.
"""
Contract editing — one working copy, many frozen versions.

The contract record is the working copy: its structured terms (dates, notice
period, value, parties, custom field values) and its clause wording
(clm.contract.clause). Editing happens there, in the Contract Editor.

A contract version is a frozen checkpoint of that working copy
(clm.contract.version.content_snapshot). "Create New Version" captures the
working copy as it stands; the new version's parent is the version it was
derived from. Existing versions are never changed, so:

    Version 1: Notice Period = 20 days
    edit the contract to 30 days, Create New Version
    Version 2: Notice Period = 30 days      Version 1 still says 20 days

Once a contract is out for signature or signed, the working copy is locked.
Changing a signed contract takes an amendment, which re-opens the working copy
and runs the normal review → approval → signing lifecycle again, while the
signed version stays locked.
"""

import base64
import logging

from markupsafe import Markup

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from ..services import DraftAssemblyEngine, FinalContractBuilder
from ..services.final_contract_builder import comparable_snapshot
from ..services.final_contract_docx import FinalContractDocxWriter
from ..services.docx_to_pdf import docx_bytes_to_pdf, find_soffice
from .clm_contract_document import TERM_GROUPS

_logger = logging.getLogger(__name__)

# Out for signature, signed, or closed: the content can no longer be edited.
CONTENT_LOCKED_STATES = (
    'pending_signature', 'executed', 'active', 'expiring_soon',
    'renewed', 'expired', 'terminated', 'archived',
)
_CLOSED_STATES = ('renewed', 'expired', 'terminated', 'archived')

# Everything the working-copy snapshot is built from.
_WORKING_CONTENT_DEPENDS = (
    'name', 'contract_ref', 'contract_type_id', 'partner_id', 'company_id',
    'user_id', 'department_id', 'effective_date', 'expiry_date', 'signing_date',
    'renewal_date', 'notice_period_days', 'auto_renewal',
    'termination_for_convenience', 'contract_value', 'currency_id',
    'confidentiality_level', 'template_id', 'field_value_ids.display_value',
    'clause_ids.title', 'clause_ids.content', 'clause_ids.section',
    'clause_ids.sequence', 'version_ids.content_snapshot', 'version_ids.status',
)


class ClmAuditEvent(models.Model):
    _inherit = 'clm.audit.event'

    event_type = fields.Selection(
        selection_add=[('version_created', 'Contract Version Created')],
        ondelete={'version_created': 'cascade'},
    )


class ClmContractEditing(models.Model):
    _inherit = 'clm.contract'

    clause_ids = fields.One2many(
        'clm.contract.clause', 'contract_id', string='Contract Clauses'
    )
    current_version_id = fields.Many2one(
        'clm.contract.version', string='Current Version',
        compute='_compute_current_version',
        help='The latest version that has not been superseded.'
    )
    current_version_label = fields.Char(
        string='Current Version No.', compute='_compute_current_version'
    )
    content_locked = fields.Boolean(
        string='Content Locked', compute='_compute_content_locked',
        help='The contract is out for signature or signed; its content can only '
             'change through an amendment.'
    )
    content_lock_reason = fields.Char(
        string='Why Content Is Locked', compute='_compute_content_locked'
    )
    has_unversioned_changes = fields.Boolean(
        string='Unversioned Changes', compute='_compute_has_unversioned_changes',
        help='The contract has edits that are not captured in any version yet.'
    )
    contract_preview_html = fields.Html(
        string='Contract Preview', compute='_compute_contract_preview', sanitize=False
    )
    placeholder_help_html = fields.Html(
        string='Placeholders', compute='_compute_contract_preview', sanitize=False
    )

    # -------------------------------------------------------------------------
    # Computes
    # -------------------------------------------------------------------------

    @api.depends('version_ids.status', 'version_ids.version_number')
    def _compute_current_version(self):
        for contract in self:
            current = contract._clm_current_version()
            contract.current_version_id = current
            contract.current_version_label = 'v%s' % current.version_number if current else ''

    @api.depends('state')
    def _compute_content_locked(self):
        for contract in self:
            contract.content_locked = contract.state in CONTENT_LOCKED_STATES
            contract.content_lock_reason = contract._clm_lock_reason()

    @api.depends(*_WORKING_CONTENT_DEPENDS)
    def _compute_has_unversioned_changes(self):
        for contract in self:
            try:
                contract.has_unversioned_changes = contract._clm_differs_from_versions(
                    contract._clm_capture_content()
                )
            except Exception:  # noqa: BLE001 — an indicator must never break the form
                _logger.debug('NextContract: could not compare working copy', exc_info=True)
                contract.has_unversioned_changes = False

    @api.depends(*_WORKING_CONTENT_DEPENDS)
    def _compute_contract_preview(self):
        for contract in self:
            try:
                snapshot = contract._clm_capture_content()
                baseline = contract._clm_latest_content_version()
                changed = contract._clm_differs_from_versions(snapshot)
                banner = dict(contract._clm_working_banner(baseline, changed) or {})
                banner['message'] = (
                    (banner.get('message') or '')
                    + ' Preview shows the final PDF document.'
                ).strip()
                # One path for all formats: final PDF attachment → native viewer.
                attachment = contract._clm_ensure_working_preview_pdf_attachment(
                    snapshot, baseline, changed,
                )
                content = contract._clm_working_preview_content(
                    snapshot, baseline, changed,
                )
                contract.contract_preview_html = contract._clm_wrap_pdf_preview_html(
                    attachment, banner, warnings=content.get('warnings') or [],
                )
                contract.placeholder_help_html = self.env['ir.qweb']._render(
                    'clm.contract_placeholder_help',
                    {'variables': sorted((snapshot.get('variables') or {}).items())},
                )
            except Exception as error:  # noqa: BLE001 — a preview must never break the form
                _logger.warning(
                    'NextContract: preview of contract %s failed: %s', contract.display_name, error
                )
                contract.contract_preview_html = Markup(
                    '<div class="alert alert-warning" role="alert">%s</div>'
                ) % ('The preview could not be built: %s' % error)
                contract.placeholder_help_html = False

    def _clm_ensure_working_preview_pdf_attachment(self, snapshot, baseline, changed):
        """Return the PDF attachment the native Preview iframe will load.

        When the working copy matches a version, that is the same attachment
        Download PDF uses. Otherwise a dedicated working-preview attachment is
        stored so the browser can load a real PDF URL (not images).
        """
        self.ensure_one()
        self.check_access('read')
        return self.sudo()._clm_ensure_working_preview_pdf_attachment_sudo(
            snapshot, baseline, changed,
        )

    def _clm_ensure_working_preview_pdf_attachment_sudo(self, snapshot, baseline, changed):
        self.ensure_one()
        current = self.current_version_id
        upload = False
        if current and current._clm_is_counterparty_upload() and current._clm_docx_attachment():
            upload = current
        elif (
            baseline
            and not changed
            and baseline._clm_is_counterparty_upload()
            and baseline._clm_docx_attachment()
        ):
            upload = baseline
        if upload and (not changed or upload == current):
            return upload._clm_generate_document('pdf')
        if baseline and not changed:
            return baseline._clm_generate_document('pdf')

        data = self._clm_working_final_pdf_bytes_sudo(snapshot, baseline, changed)
        if not data or not bytes(data).startswith(b'%PDF'):
            raise UserError(
                'Could not generate a PDF for the working-copy preview.'
            )
        data = bytes(data)
        filename = '%s_WORKING_PREVIEW.pdf' % (
            (self.contract_ref or 'CONTRACT-%s' % self.id).replace('/', '-'),
        )
        Attachment = self.env['ir.attachment'].sudo()
        existing = Attachment.search([
            ('res_model', '=', 'clm.contract'),
            ('res_id', '=', self.id),
            ('name', '=', filename),
        ], limit=1)
        # Prefer ``raw`` — writing ``datas`` during a compute can leave an
        # empty filestore row (file_size=0) that breaks the PDF iframe.
        vals = {
            'name': filename,
            'raw': data,
            'mimetype': 'application/pdf',
            'res_model': 'clm.contract',
            'res_id': self.id,
        }
        if existing:
            existing_raw = existing.with_context(bin_size=False).raw or b''
            if (
                existing.file_size
                and existing_raw.startswith(b'%PDF')
                and existing.checksum
                and len(existing_raw) == len(data)
            ):
                # Still rewrite so unversioned edits stay current.
                pass
            existing.write(vals)
            return existing
        return Attachment.create(vals)

    def _clm_working_final_pdf_bytes(self, snapshot, baseline, changed):
        """Final PDF for the working copy — same format routing as Download PDF.

        When the working copy matches a version, that version's PDF path is used
        so Preview matches Download. Otherwise the live working content is
        rendered through the contract's generation format.
        """
        self.ensure_one()
        self.check_access('read')
        return self.sudo()._clm_working_final_pdf_bytes_sudo(
            snapshot, baseline, changed,
        )

    def _clm_working_final_pdf_bytes_sudo(self, snapshot, baseline, changed):
        self.ensure_one()
        # Counterparty upload in force → same PDF as that version's Download.
        current = self.current_version_id
        upload = False
        if current and current._clm_is_counterparty_upload() and current._clm_docx_attachment():
            upload = current
        elif (
            baseline
            and not changed
            and baseline._clm_is_counterparty_upload()
            and baseline._clm_docx_attachment()
        ):
            upload = baseline
        if upload and (not changed or upload == current):
            return upload._clm_final_pdf_bytes()

        if baseline and not changed:
            return baseline._clm_final_pdf_bytes()

        gen = self._clm_get_document_generation_context()
        if gen['uses_base_docx']:
            return self._render_contract_pdf_from_base_docx(kind='draft')
        if gen.get('uses_default_docx_layout'):
            content = self._build_final_contract_content(
                kind='draft', snapshot=snapshot,
            )
            # Mirror working-copy version label used by the old QWeb preview.
            if baseline:
                content['version_label'] = (
                    'v%s + unversioned edits' % baseline.version_number
                )
            else:
                content['version_label'] = 'Not versioned yet'
            docx = FinalContractDocxWriter(layout='professional').render(content)
            if find_soffice():
                return docx_bytes_to_pdf(docx)
            return self._render_contract_pdf(content)

        content = self._clm_working_preview_content(snapshot, baseline, changed)
        return self._render_contract_pdf(content)

    # -------------------------------------------------------------------------
    # Versions and locking
    # -------------------------------------------------------------------------

    def _clm_current_version(self):
        """The latest non-superseded version, read through sudo like version_count."""
        self.ensure_one()
        versions = self.sudo().version_ids.filtered(lambda v: v.status != 'superseded')
        if not versions:
            return self.env['clm.contract.version']
        return max(versions, key=lambda v: (v.version_number, v._origin.id or 0))._origin

    def _clm_latest_content_version(self):
        """The latest version that carries frozen content."""
        self.ensure_one()
        versions = self.sudo().version_ids.filtered('content_snapshot')
        if not versions:
            return self.env['clm.contract.version']
        return max(versions, key=lambda v: (v.version_number, v._origin.id or 0))

    def _clm_lock_reason(self):
        self.ensure_one()
        if self.state not in CONTENT_LOCKED_STATES:
            return ''
        state = dict(self._fields['state']._description_selection(self.env)).get(self.state, self.state)
        if self.state == 'pending_signature':
            return (
                'This contract is out for signature, so its content is locked. '
                'Cancel the signature request to make changes.'
            )
        if self.state in _CLOSED_STATES:
            return (
                'This contract is %s. Its signed content is locked and can no longer '
                'be changed.' % state
            )
        return (
            'This contract is %s. Its signed version is locked — use Create Amendment '
            'to change it.' % state
        )

    def _clm_check_content_editable(self):
        for contract in self:
            if contract.state in CONTENT_LOCKED_STATES:
                raise UserError(contract._clm_lock_reason())

    def _clm_capture_content(self):
        """The working copy as a content snapshot."""
        self.ensure_one()
        return FinalContractBuilder(self, kind='draft').capture()

    def _clm_differs_from_versions(self, snapshot):
        self.ensure_one()
        baseline = self._clm_latest_content_version()
        if not baseline:
            return bool(self.sudo().clause_ids)
        return comparable_snapshot(snapshot) != comparable_snapshot(baseline.content_snapshot)

    def _clm_create_content_version(self, version_type='internal_draft', parent_version=None,
                                    change_summary=False, change_classification='none',
                                    source=False, snapshot=None, base_docx_version=None):
        """Freeze the working copy into a new version and return it."""
        self.ensure_one()
        self._clm_check_content_editable()
        snapshot = snapshot or self._clm_capture_content()
        if parent_version is None:
            parent_version = self._clm_current_version()
        base_docx_version = self._clm_resolve_base_docx_version(
            base_docx_version=base_docx_version,
            parent_version=parent_version,
            snapshot=snapshot,
        )
        # Keep snapshot template.base_docx_* aligned with the pinned version.
        if base_docx_version and isinstance(snapshot, dict):
            template_meta = dict(snapshot.get('template') or {})
            template_meta.update({
                'base_docx_version_id': base_docx_version.id,
                'base_docx_version_number': base_docx_version.version_number,
                'base_document_id': base_docx_version.document_id.id,
            })
            snapshot = dict(snapshot, template=template_meta)
        Version = self.env['clm.contract.version']
        version = Version.create({
            'contract_id': self.id,
            'version_type': version_type,
            'parent_version_id': parent_version.id if parent_version else False,
            'content_snapshot': snapshot,
            'base_docx_version_id': base_docx_version.id if base_docx_version else False,
            'change_summary': change_summary or False,
            'change_classification': change_classification or 'none',
            'source': source or 'Contract editor',
        })
        if base_docx_version:
            base_docx_version.invalidate_recordset(['is_used', 'status_label'])
        type_label = dict(
            Version._fields['version_type']._description_selection(self.env)
        ).get(version_type, version_type)
        derived = Markup(', derived from v%s') % parent_version.version_number if parent_version else ''
        summary = Markup(': %s') % change_summary if change_summary else ''
        self.message_post(body=Markup('Version <b>v%s</b> created (%s%s)%s') % (
            version.version_number, type_label, derived, summary,
        ))
        self._log_audit_event(
            'version_created',
            'Version v%s created (%s)' % (version.version_number, type_label),
            previous_value='v%s' % parent_version.version_number if parent_version else None,
            new_value='v%s' % version.version_number,
            details=change_summary or None,
        )
        return version

    def _clm_resolve_base_docx_version(self, base_docx_version=None, parent_version=None,
                                       snapshot=None):
        """Pick the Base DOCX version this contract (version) must keep using.

        Priority:
        1. Explicit ``base_docx_version`` argument.
        2. Contract-level lock (``base_docx_version_id``), captured at create.
        3. Already-pinned version on this contract (oldest with a pin).
        4. Parent version's pin.
        5. Snapshot ``template.base_docx_version_id``.
        6. Template's current Active version (legacy / first capture only).
        """
        self.ensure_one()
        Version = self.env['clm.template.base.docx.version']
        if base_docx_version:
            return base_docx_version

        if self.base_docx_version_id:
            return self.base_docx_version_id

        pinned = self.env['clm.contract.version'].sudo().search([
            ('contract_id', '=', self.id),
            ('base_docx_version_id', '!=', False),
        ], order='version_number asc, id asc', limit=1)
        if pinned:
            # Backfill the contract lock from the oldest version pin.
            if not self.base_docx_version_id:
                self.with_context(clm_capturing_base_docx=True).write({
                    'base_docx_version_id': pinned.base_docx_version_id.id,
                })
            return pinned.base_docx_version_id

        if parent_version and parent_version.base_docx_version_id:
            if not self.base_docx_version_id:
                self.with_context(clm_capturing_base_docx=True).write({
                    'base_docx_version_id': parent_version.base_docx_version_id.id,
                })
            return parent_version.base_docx_version_id

        snap_id = False
        if isinstance(snapshot, dict):
            snap_id = (snapshot.get('template') or {}).get('base_docx_version_id')
        if snap_id:
            found = Version.browse(snap_id).exists()
            if found:
                if not self.base_docx_version_id:
                    self.with_context(clm_capturing_base_docx=True).write({
                        'base_docx_version_id': found.id,
                    })
                return found

        template = self.template_id
        if template and hasattr(template, '_active_base_docx_version'):
            active = template._active_base_docx_version()
            if active and not self.base_docx_version_id:
                self.with_context(clm_capturing_base_docx=True).write({
                    'base_docx_version_id': active.id,
                })
            return active
        return Version

    def _clm_base_docx_version_for_render(self, version=None):
        """Base DOCX version to use when rendering a contract document."""
        self.ensure_one()
        if version and version.base_docx_version_id:
            return version.base_docx_version_id
        return self._clm_resolve_base_docx_version(
            parent_version=version,
            snapshot=(
                version.sudo().content_snapshot
                if version and 'content_snapshot' in version._fields else None
            ),
        )

    def _clm_load_template_clauses(self):
        """Replace the contract's clauses with the template's library clauses."""
        self.ensure_one()
        self._clm_check_content_editable()
        template = self.template_id
        if not template:
            raise UserError('Select a contract template first.')
        if template.state != 'published':
            raise UserError(
                "Template '%s' is not published. Only published templates can be "
                "used." % template.name
            )
        context = DraftAssemblyEngine(template, self, use_contract_clauses=False).assemble(
            substitute=False
        )
        rows = []
        for section in context['sections']:
            for clause in section['clauses']:
                rows.append({
                    'contract_id': self.id,
                    'sequence': (len(rows) + 1) * 10,
                    'section': section['title'] or False,
                    'title': clause['title'] or template.name,
                    'content': clause['content'] or '',
                    'clause_id': clause['clause_id'] or False,
                    'clause_version': clause['clause_version'] or 0,
                    'required': bool(clause['required']),
                    'source': 'template',
                })
        self.clause_ids.unlink()
        clauses = self.env['clm.contract.clause'].create(rows)
        self.message_post(
            body='%s clause(s) loaded from template %s.' % (len(clauses), template.name)
        )
        return clauses

    def _clm_load_clauses_from_snapshot(self, snapshot):
        """Replace the contract's clauses with the wording frozen in `snapshot`."""
        self.ensure_one()
        self._clm_check_content_editable()
        Clause = self.env['clm.clause'].sudo()
        valid_sources = dict(self.env['clm.contract.clause']._fields['source'].selection)
        rows = []
        for index, clause in enumerate((snapshot or {}).get('clauses') or [], start=1):
            library = Clause.browse(clause['clause_id']).exists() if clause.get('clause_id') else Clause
            rows.append({
                'contract_id': self.id,
                'sequence': index * 10,
                'section': clause.get('section') or False,
                'title': clause.get('title') or 'Clause %s' % index,
                'content': clause.get('content') or '',
                'clause_id': library.id or False,
                'clause_version': clause.get('clause_version') or 0,
                'required': clause.get('required', True),
                'source': clause.get('source') if clause.get('source') in valid_sources else 'custom',
            })
        self.clause_ids.unlink()
        return self.env['clm.contract.clause'].create(rows)

    # -------------------------------------------------------------------------
    # Preview rendering
    # -------------------------------------------------------------------------

    def _clm_render_preview(self, content, banner=None):
        return self.env['ir.qweb']._render('clm.contract_document_preview', {
            'content': content,
            'term_groups': TERM_GROUPS,
            'banner': banner or {},
        })

    def _clm_working_preview_content(self, snapshot, baseline, changed):
        """The working copy's preview content, labelled with the version the banner names.

        A working copy identical to its latest version is rendered as that
        version, so it shows the same Version as the version's own preview.
        Otherwise the label says the edits are not in a version yet.
        """
        self.ensure_one()
        if baseline and not changed:
            return self._build_final_contract_content(
                version=baseline._origin, kind='draft', snapshot=snapshot
            )
        content = self._build_final_contract_content(kind='draft', snapshot=snapshot)
        if baseline:
            content['version_label'] = 'v%s + unversioned edits' % baseline.version_number
        else:
            content['version_label'] = 'Not versioned yet'
        return content

    def _clm_working_banner(self, baseline, changed):
        self.ensure_one()
        if self.state in CONTENT_LOCKED_STATES:
            return {'css': 'alert-warning', 'message': self._clm_lock_reason()}
        if not baseline:
            return {
                'css': 'alert-info',
                'message': 'Working draft — not captured in a version yet. Use Generate '
                           'Draft or Create New Version to capture it.',
            }
        if changed:
            return {
                'css': 'alert-info',
                'message': 'Working draft — includes edits that are not in any version '
                           'yet (latest captured version: v%s). Use Create New Version '
                           'to capture them.' % baseline.version_number,
            }
        return {
            'css': 'alert-success',
            'message': 'Working draft — identical to version v%s.' % baseline.version_number,
        }

    # -------------------------------------------------------------------------
    # UI actions
    # -------------------------------------------------------------------------

    def action_open_contract_editor(self):
        self.ensure_one()
        view = self.env.ref('clm.view_clm_contract_editor_form')
        return {
            'type': 'ir.actions.act_window',
            'name': 'Edit Contract',
            'res_model': 'clm.contract',
            'res_id': self.id,
            'view_mode': 'form',
            'views': [(view.id, 'form')],
            'target': 'current',
        }

    def action_open_contract_form(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.display_name,
            'res_model': 'clm.contract',
            'res_id': self.id,
            'view_mode': 'form',
            'views': [(self.env.ref('clm.view_clm_contract_form').id, 'form')],
            'target': 'current',
        }

    def action_load_template_clauses(self):
        self.ensure_one()
        self._clm_load_template_clauses()
        return True

    def action_open_new_version_wizard(self):
        self.ensure_one()
        self._clm_check_content_editable()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Create New Version',
            'res_model': 'clm.contract.version.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_contract_id': self.id},
        }

    def action_preview_contract(self):
        """Preview what the contract says.

        Uses the same current version as Generate DOCX / Generate PDF whenever
        that version is the document in force (content locked, or no unversioned
        edits). Working-copy preview is only for unlocked drafts with edits not
        yet captured in a version.
        """
        self.ensure_one()
        version = self.current_version_id
        if version and self._clm_can_read_versions():
            use_version = (
                self.content_locked
                or not self.has_unversioned_changes
                or version._clm_is_counterparty_upload()
            )
            if use_version:
                return version.action_preview_version()
        # Working copy with unversioned edits: materialise a real PDF first,
        # then open the native in-app viewer (same as Version / Final Preview).
        snapshot = self._clm_capture_content()
        baseline = self._clm_latest_content_version()
        changed = self._clm_differs_from_versions(snapshot)
        attachment = self._clm_ensure_working_preview_pdf_attachment(
            snapshot, baseline, changed,
        )
        self.env.flush_all()
        banner = dict(self._clm_working_banner(baseline, changed) or {})
        banner['message'] = (
            (banner.get('message') or '')
            + ' Preview shows the final PDF document.'
        ).strip()
        content = self._clm_working_preview_content(snapshot, baseline, changed)
        return self.with_context(
            clm_pdf_preview_show_create_version=True,
        )._clm_action_open_pdf_preview(
            attachment,
            title='Contract Preview',
            banner=banner,
            warnings=content.get('warnings') or [],
        )

    def _clm_can_read_versions(self):
        try:
            self.env['clm.contract.version'].check_access('read')
        except AccessError:
            return False
        return True

    def _clm_document_version(self):
        """The version whose documents the contract form generates."""
        self.ensure_one()
        version = self.current_version_id
        if not version:
            raise UserError(
                'This contract has no version yet. Use Generate Draft or Create New '
                'Version first, then generate its documents.'
            )
        if not self.content_locked and self.has_unversioned_changes:
            raise UserError(
                'The contract has edits that are not captured in a version yet, so a '
                'document generated now would not contain them.\n\n'
                'Use Create New Version first. To generate the documents of version v%s '
                'as it is, open it from the Document Versions tab.' % version.version_number
            )
        return version

    def action_generate_docx(self):
        self.ensure_one()
        return self._clm_document_version().action_generate_docx()

    def action_generate_pdf(self):
        self.ensure_one()
        return self._clm_document_version().action_generate_pdf()
