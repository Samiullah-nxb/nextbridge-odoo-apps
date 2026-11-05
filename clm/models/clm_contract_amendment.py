# Part of NextContract. See LICENSE file for full copyright and licensing details.
"""
Signed-contract protection and amendments.

A signed contract's content is locked (clm_draft), and so is the version that
was signed, together with the draft it was signed from: neither its content nor
its document can be changed.

To change a signed contract, Legal starts an amendment:

    Signed v2 (locked)
        │  Create Amendment
        ▼
    contract back to Draft, clause wording loaded from v2's signed content
        │  edit  →  Create New Version
        ▼
    v3 (parent: v2)  →  review  →  approval  →  signing  →  executed v4

The signed version, its documents and its signed artifact are never modified.
The amendment runs through the contract's existing lifecycle — the same review
tasks, approval, signature and execution — rather than a second workflow.
"""

from markupsafe import Markup

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.clm.services import FinalContractBuilder

_AMENDABLE_STATES = ('executed', 'active', 'expiring_soon')
_PDF_MIME = 'application/pdf'


class ClmAuditEventAmendment(models.Model):
    _inherit = 'clm.audit.event'

    event_type = fields.Selection(
        selection_add=[('amendment', 'Amendment Started')],
        ondelete={'amendment': 'cascade'},
    )


class ClmContractAmendment(models.Model):
    _inherit = 'clm.contract'

    def _clm_is_legal(self):
        return self.env.su or self.env.user.has_group('clm.group_clm_legal')

    def _validate_state_transition(self, new_state):
        # Re-opening a signed contract exists only as part of an amendment,
        # never as a free state change.
        if (
            new_state == 'draft'
            and self.env.context.get('clm_amendment')
            and self._clm_is_legal()
            and all(contract.state in _AMENDABLE_STATES for contract in self)
        ):
            return
        return super()._validate_state_transition(new_state)

    def action_create_amendment(self):
        self.ensure_one()
        if not self._clm_is_legal():
            raise AccessError('Only Legal Reviewers and Legal Admins can start an amendment.')
        if self.state not in _AMENDABLE_STATES:
            state = dict(self._fields['state']._description_selection(self.env)).get(self.state)
            raise UserError(
                'Only Executed, Active or Expiring Soon contracts can be amended. '
                'This contract is %s.' % state
            )
        signed_version, kind = self._get_final_contract_version()
        if kind != 'signed':
            raise UserError('This contract has no signed version to amend.')
        signed_version = signed_version.sudo()
        if not signed_version.content_snapshot:
            # A signed version from before content was frozen on versions renders
            # from the live contract. Freeze it as it renders today — once —
            # before the amendment is allowed to change the contract.
            signed_version.write({
                'content_snapshot': self._clm_capture_signed_content(signed_version),
            })

        previous_state = self.state
        self.with_context(clm_amendment=True).write({'state': 'draft'})
        self._clm_load_clauses_from_snapshot(signed_version.content_snapshot)
        self.message_post(body=Markup(
            'Amendment started from signed version <b>v%s</b>. The signed version stays '
            'locked; edit the contract, then use <b>Create New Version</b> and take the '
            'amendment through review, approval and signing.'
        ) % signed_version.version_number)
        self._log_audit_event(
            'amendment',
            'Amendment started from signed v%s' % signed_version.version_number,
            previous_value=previous_state,
            new_value='draft',
        )
        return self.action_open_contract_editor()

    def _clm_capture_signed_content(self, version):
        """The content of a signed version that predates snapshots, as it renders."""
        self.ensure_one()
        content = self._build_final_contract_content(version=version, kind='signed')
        snapshot = self._clm_capture_content()
        snapshot.update({
            'captured_from': 'signed_version',
            'template': False,
            'clauses': FinalContractBuilder.clauses_from_body(content['body']),
        })
        return snapshot


class ClmContractVersionSigning(models.Model):
    _inherit = 'clm.contract.version'

    @api.depends('version_type')
    def _compute_is_signed_version(self):
        super()._compute_is_signed_version()
        real_ids = self._origin.ids
        if not real_ids:
            return
        signed_sources = set(self.sudo().search([
            ('source_version_id', 'in', real_ids),
            ('version_type', '=', 'executed'),
        ]).mapped('source_version_id').ids)
        for version in self:
            if version._origin.id in signed_sources:
                version.is_signed_version = True

    def write(self, vals):
        if 'document_id' in vals and not self.env.su:
            for version in self:
                if version.version_type != 'executed' and version._clm_signing_started():
                    raise UserError(
                        'Version %s has been sent for signature or signed, so its document '
                        'cannot be replaced. Create a new version instead.'
                        % version.version_number
                    )
        return super().write(vals)

    def _clm_signing_started(self):
        self.ensure_one()
        if self.is_signed_version:
            return True
        return bool(self.env['sign.request'].sudo().search_count([
            ('clm_source_version_id', '=', self.id),
            ('state', 'in', ('sent', 'signed')),
        ]))

    def _clm_document_kind(self):
        self.ensure_one()
        if self.version_type == 'executed':
            return 'signed'
        version, kind = self.contract_id._get_final_contract_version()
        if kind == 'approved' and version.id == self.id:
            return 'approved'
        return 'draft'

    def _clm_signing_attachment(self):
        """The PDF Odoo Sign receives for this signature draft.

        When the version has a frozen content snapshot (Base DOCX / assembled
        draft), always regenerate the PDF so Title and Date print like Name.
        An uploaded PDF with no snapshot is signed as uploaded.

        Portal counterparty uploads are usually DOCX; Odoo Sign only accepts
        PDF, so we generate from the snapshot or raise a clear error.
        """
        self.ensure_one()
        # Snapshot wins over any attached/cached PDF so Jinja-filled Title and
        # Date (same as Name) are current when Sign opens.
        if self.content_snapshot:
            return self._clm_generate_document('pdf')
        attachment = self.document_id.attachment_id if self.document_id else self.env['ir.attachment']
        if attachment and attachment.mimetype == _PDF_MIME:
            return attachment
        if self.generated_pdf_id:
            return self.generated_pdf_id
        raise UserError(
            "Odoo Sign requires a PDF, but this Signature Draft has a Word (.docx) file.\n\n"
            "Do one of the following:\n"
            "1. Click Generate PDF on the contract, then try Send for Signature again, or\n"
            "2. Open Version 2, upload a PDF of the negotiated document, or\n"
            "3. Use Mark as Manually Signed and upload the signed PDF."
        )

    def action_create_amendment(self):
        self.ensure_one()
        return self.contract_id.action_create_amendment()
