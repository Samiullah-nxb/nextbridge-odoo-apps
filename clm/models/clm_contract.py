# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models, fields, api
from odoo.exceptions import AccessError, UserError, ValidationError

# ─── State Machine Definition (aligned with client requirements §3.15) ───────
_CONTRACT_STATES = [
    ('draft', 'Draft Request'),
    ('in_review', 'Under Legal Review'),
    ('awaiting_business', 'Awaiting Business Input'),
    ('awaiting_finance', 'Awaiting Finance Input'),
    ('awaiting_procurement', 'Awaiting Procurement Input'),
    ('awaiting_compliance', 'Awaiting Compliance Review'),
    ('internal_review_done', 'Internal Review Completed'),
    ('sent_to_counterparty', 'Sent to Counterparty'),
    ('in_negotiation', 'Under Negotiation'),
    ('approved', 'Approved for Signature'),
    ('pending_signature', 'Pending Signature'),
    ('executed', 'Executed'),
    ('active', 'Active'),
    ('expiring_soon', 'Expiring Soon'),
    ('renewed', 'Renewed'),
    ('expired', 'Expired'),
    ('terminated', 'Terminated'),
    ('archived', 'Archived'),
    ('cancelled', 'Cancelled'),
]


class ClmContract(models.Model):
    _name = 'clm.contract'
    _description = 'Contract'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _rec_name = 'display_name'
    _check_company_auto = True

    # ─── Identity ────────────────────────────────────────────────────────────
    contract_ref = fields.Char(
        string='Contract ID', readonly=True, copy=False, index=True,
        default='New',
        help='Auto-generated unique identifier (CNT-YYYY-NNNNN).'
    )
    display_name = fields.Char(
        compute='_compute_display_name', store=True, recursive=False
    )
    name = fields.Char(string='Contract Title', required=True, tracking=True)

    # ─── Classification ──────────────────────────────────────────────────────
    contract_type_id = fields.Many2one(
        'clm.contract.type', string='Contract Type',
        required=True, tracking=True, index=True,
        check_company=True
    )

    # ─── Lifecycle ───────────────────────────────────────────────────────────
    state = fields.Selection(
        _CONTRACT_STATES, string='Status',
        default='draft', required=True, tracking=True, index=True, copy=False
    )

    # ─── Parties & Ownership ─────────────────────────────────────────────────
    partner_id = fields.Many2one(
        'res.partner', string='Counterparty', tracking=True,
        check_company=True
    )
    partner_signatory_id = fields.Many2one(
        'res.partner',
        string='Counterparty Signatory',
        tracking=True,
        domain="[('id', 'child_of', partner_id), ('is_company', '=', False)]",
        help='Person who signs for the counterparty. Name and Title on the '
             'signature block, and the Odoo Sign recipient, come from this '
             'contact. Leave Counterparty as the company.',
    )
    department_id = fields.Many2one('hr.department', string='Department', tracking=True)
    user_id = fields.Many2one(
        'res.users', string='Business Owner',
        default=lambda self: self.env.user, tracking=True
    )
    legal_owner_id = fields.Many2one('res.users', string='Legal Owner', tracking=True)
    procurement_owner_id = fields.Many2one('res.users', string='Procurement Owner', tracking=True)
    finance_owner_id = fields.Many2one('res.users', string='Finance Owner', tracking=True)

    # ─── Financial ───────────────────────────────────────────────────────────
    contract_value = fields.Monetary(
        string='Contract Value',
        groups='clm.group_clm_legal_admin,clm.group_clm_legal,clm.group_clm_finance,clm.group_clm_manager'
    )
    currency_id = fields.Many2one(
        'res.currency', string='Currency',
        default=lambda self: self.env.company.currency_id
    )

    # ─── Dates ───────────────────────────────────────────────────────────────
    effective_date = fields.Date(string='Effective Date', tracking=True)
    expiry_date = fields.Date(string='Expiry Date', tracking=True)
    signing_date = fields.Date(string='Signing Date', tracking=True)
    renewal_date = fields.Date(string='Renewal Date', tracking=True)
    notice_period_days = fields.Integer(string='Notice Period (Days)', tracking=True)
    termination_for_convenience = fields.Boolean(string='Termination for Convenience', tracking=True)
    auto_renewal = fields.Boolean(string='Auto-Renewal', tracking=True)
    remarks = fields.Text(string='Remarks')
    signed_copy_uploaded = fields.Boolean(string='Signed Copy Uploaded', default=False, tracking=True)
    alerts_configured = fields.Boolean(string='Alerts Configured', default=False, tracking=True)
    parent_contract_id = fields.Many2one(
        'clm.contract', string='Parent Contract', ondelete='set null', index=True,
    )
    child_contract_ids = fields.One2many('clm.contract', 'parent_contract_id', string='Related Contracts')
    tag_ids = fields.Many2many('documents.tag', string='Tags')

    # ─── Risk & Confidentiality ──────────────────────────────────────────────
    risk_rating = fields.Selection([
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
        ('critical', 'Critical'),
    ], string='Risk Rating', tracking=True,
        groups='clm.group_clm_legal_admin,clm.group_clm_legal'
    )
    risk_level = fields.Selection(
        related='risk_rating', string='Risk Level', store=False,
        help='Alias for risk_rating used in views/reporting.'
    )
    confidentiality_level = fields.Selection([
        ('public', 'Public Internal'),
        ('internal', 'Internal'),
        ('restricted', 'Restricted Legal'),
    ], string='Confidentiality Level', default='internal', tracking=True)

    # ─── Source / Origin ─────────────────────────────────────────────────────
    source = fields.Selection([
        ('manual', 'Manual Creation'),
        ('intake', 'Contract Request'),
    ], string='Source', default='manual', readonly=True, copy=False)

    # ─── Documents Integration ───────────────────────────────────────────────
    folder_id = fields.Many2one(
        'documents.document', string='Documents Folder',
        domain=[('type', '=', 'folder')],
        help='Odoo Documents folder for this contract.'
    )
    field_value_ids = fields.One2many(
        'clm.contract.field.value', 'contract_id', string='Field Values'
    )

    # ─── Multi-Company ───────────────────────────────────────────────────────
    company_id = fields.Many2one(
        'res.company', string='Company',
        required=True, default=lambda self: self.env.company,
        index=True
    )

    # ─── SQL Constraints ─────────────────────────────────────────────────────
    _unique_contract_ref = models.Constraint("unique(contract_ref)", 'Contract ID must be unique.')

    # ─── Computed ────────────────────────────────────────────────────────────
    @api.depends('contract_ref', 'name')
    def _compute_display_name(self):
        for rec in self:
            if rec.contract_ref and rec.contract_ref != 'New':
                rec.display_name = f"[{rec.contract_ref}] {rec.name}"
            else:
                rec.display_name = rec.name

    @api.onchange('partner_id')
    def _onchange_partner_id_clear_signatory(self):
        """Drop signatory when it no longer belongs under the counterparty."""
        if not self.partner_id:
            self.partner_signatory_id = False
            return
        signatory = self.partner_signatory_id
        if not signatory:
            return
        allowed = self.env['res.partner'].search([
            ('id', 'child_of', self.partner_id.id),
            ('is_company', '=', False),
        ])
        if signatory not in allowed:
            self.partner_signatory_id = False

    # ─── CRUD Overrides ──────────────────────────────────────────────────────
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('contract_ref', 'New') == 'New':
                vals['contract_ref'] = self.env['ir.sequence'].sudo().next_by_code(
                    'clm.contract'
                ) or 'New'
        records = super().create(vals_list)
        # Audit
        for rec in records:
            rec._log_audit_event(
                'state_change',
                f"Contract created in state '{rec.state}'",
                new_value=rec.state,
            )
            rec._ensure_field_values()
        return records

    def _ensure_field_values(self):
        """Create empty field value rows for applicable definitions.

        The rows are bookkeeping for a contract its creator may create, so
        they are created as superuser: requesters and contract users can
        create contracts but only read field values. Filling a value in is
        still governed by the field value access rules.
        """
        Definition = self.env['clm.contract.field.definition']
        Value = self.env['clm.contract.field.value'].sudo()
        for contract in self:
            definitions = Definition.search([('active', '=', True)])
            existing_codes = set(contract.field_value_ids.mapped('definition_id').ids)
            for definition in definitions:
                if definition.id in existing_codes:
                    continue
                if definition.contract_type_ids and contract.contract_type_id not in definition.contract_type_ids:
                    continue
                Value.create({
                    'contract_id': contract.id,
                    'definition_id': definition.id,
                })

    # ─── Data validation ─────────────────────────────────────────────────────
    @api.constrains('effective_date', 'expiry_date', 'contract_value', 'notice_period_days')
    def _check_contract_terms(self):
        """Reject impossible dates, amounts and notice periods.

        Legacy imports (``migration_mode``) are exempt: their data problems are
        reported by the migration tool instead of blocking the import.
        """
        if self.env.context.get('migration_mode'):
            return
        # sudo: contract_value is group-restricted but must always be validated.
        for contract in self.sudo():
            if (
                contract.effective_date and contract.expiry_date
                and contract.expiry_date < contract.effective_date
            ):
                raise ValidationError(
                    "The expiry date (%s) of '%s' cannot be before its effective date (%s)."
                    % (contract.expiry_date, contract.display_name, contract.effective_date)
                )
            if contract.contract_value and contract.contract_value < 0:
                raise ValidationError(
                    "The contract value of '%s' cannot be negative." % contract.display_name
                )
            if contract.notice_period_days and contract.notice_period_days < 0:
                raise ValidationError(
                    "The notice period of '%s' cannot be negative." % contract.display_name
                )

    def write(self, vals):
        if 'state' in vals:
            self._validate_state_transition(vals['state'])
        res = super().write(vals)
        # Audit state changes
        if 'state' in vals:
            for rec in self:
                rec._log_audit_event(
                    'state_change',
                    f"Contract transitioned to '{vals['state']}'",
                    new_value=vals['state'],
                )
        return res

    # ─── State Machine Enforcement ───────────────────────────────────────────
    def _get_allowed_transitions(self):
        return {
            'draft': {'in_review', 'cancelled'},
            'in_review': {
                'awaiting_business', 'awaiting_finance', 'awaiting_procurement',
                'awaiting_compliance', 'internal_review_done',
                'sent_to_counterparty', 'approved', 'draft', 'cancelled',
            },
            'awaiting_business': {'in_review', 'internal_review_done', 'cancelled'},
            'awaiting_finance': {'in_review', 'internal_review_done', 'cancelled'},
            'awaiting_procurement': {'in_review', 'internal_review_done', 'cancelled'},
            'awaiting_compliance': {'in_review', 'internal_review_done', 'cancelled'},
            'internal_review_done': {
                'sent_to_counterparty', 'approved', 'in_review', 'cancelled',
            },
            # in_review: lets a contract that skipped a mandatory review stage go
            # back and complete it (see clm_workflow approval gates).
            'sent_to_counterparty': {'in_negotiation', 'approved', 'in_review', 'cancelled'},
            'in_negotiation': {'approved', 'in_review', 'cancelled'},
            'approved': {'pending_signature', 'in_review', 'cancelled'},
            'pending_signature': {'executed', 'approved', 'cancelled'},
            'executed': {'active', 'archived'},
            'active': {'expiring_soon', 'expired', 'terminated', 'renewed', 'archived'},
            'expiring_soon': {'renewed', 'expired', 'terminated', 'active'},
            'renewed': {'active', 'expired'},
            'expired': {'archived'},
            'terminated': {'archived'},
            'archived': set(),
            'cancelled': {'draft'},
        }

    def _validate_state_transition(self, new_state):
        """
        Enforce allowed state transitions. Raises UserError on invalid jump.
        Superuser and migration_mode bypass (for legacy imports).
        """
        if self.env.context.get('migration_mode'):
            from odoo.exceptions import AccessError
            if not self.env.user.has_group('clm.group_clm_legal_admin'):
                raise AccessError("Only Legal Admins can bypass state validation via migration mode.")
            return
        for rec in self:
            allowed = rec._get_allowed_transitions().get(rec.state, set())
            if new_state not in allowed:
                raise UserError(
                    f"Cannot transition contract '{rec.display_name}' "
                    f"from '{rec.state}' to '{new_state}'.\n"
                    f"Allowed transitions: {', '.join(sorted(allowed)) or 'None (terminal state)'}."
                )

    def _clm_require_groups(self, *group_xmlids):
        """Server-side role check for a public action method.

        View ``groups=`` only hides a button; every ``action_*`` method can still
        be called through RPC by anyone who can write the contract. Internal
        code running as superuser is not affected.
        """
        if self.env.su:
            return
        if not any(self.env.user.has_group(xmlid) for xmlid in group_xmlids):
            raise AccessError(
                "You are not allowed to perform this action on a contract. "
                "It requires one of the roles: %s." % ', '.join(
                    self.env.ref(xmlid).name for xmlid in group_xmlids)
            )

    # ─── Action Methods (named transitions) ──────────────────────────────────
    def action_submit_for_review(self):
        self.write({'state': 'in_review'})
        self.message_post(body='Contract submitted for legal review.')

    def action_request_business_input(self):
        self.write({'state': 'awaiting_business'})
        self.message_post(body='Awaiting business input.')

    def action_request_finance_input(self):
        self.write({'state': 'awaiting_finance'})
        self.message_post(body='Awaiting finance input.')

    def action_request_procurement_input(self):
        self.write({'state': 'awaiting_procurement'})
        self.message_post(body='Awaiting procurement input.')

    def action_request_compliance_input(self):
        self.write({'state': 'awaiting_compliance'})
        self.message_post(body='Awaiting compliance / regulatory review.')

    def action_complete_internal_review(self):
        self.write({'state': 'internal_review_done'})
        self.message_post(body='Internal review completed.')

    def action_send_to_counterparty(self):
        self.write({'state': 'sent_to_counterparty'})
        self.message_post(body='Contract sent to counterparty for review.')

    def action_send_to_negotiation(self):
        """Move to Under Negotiation, always via Sent to Counterparty.

        Skipping ``sent_to_counterparty`` left that lifecycle stage as
        "Not used". Start Negotiation and the Send-to-Counterparty wizard
        both call this method, so the chart stays consistent.
        """
        for rec in self:
            if rec.state == 'in_negotiation':
                continue
            if rec.state != 'sent_to_counterparty':
                allowed = rec._get_allowed_transitions().get(rec.state, set())
                if 'sent_to_counterparty' not in allowed:
                    raise UserError(
                        "Cannot start negotiation for contract '%s' from "
                        "status '%s'."
                        % (rec.display_name, rec.state)
                    )
                rec.action_send_to_counterparty()
            rec.write({'state': 'in_negotiation'})
            rec.message_post(body='Contract moved to negotiation.')

    def action_approve(self):
        self.write({'state': 'approved'})
        self.message_post(body='Contract approved for signature.')

    def action_return_for_clarification(self):
        self.write({'state': 'draft'})
        self.message_post(body='Contract returned for clarification.')

    def action_cancel(self):
        self.write({'state': 'cancelled'})
        self.message_post(body='Contract cancelled.')

    def action_reopen(self):
        """Reopen cancelled contract back to draft."""
        self.write({'state': 'draft'})
        self.message_post(body='Contract reopened to draft.')

    # ─── Audit Helper ────────────────────────────────────────────────────────
    def _log_audit_event(self, event_type, summary, previous_value=None,
                         new_value=None, details=None):
        """Create an immutable audit event."""
        self.env['clm.audit.event'].sudo().create({
            'event_type': event_type,
            'summary': summary,
            'user_id': self.env.user.id,
            'res_model': self._name,
            'res_id': self.id,
            'contract_id': self.id if self._name == 'clm.contract' else (
                self.contract_id.id if hasattr(self, 'contract_id') and self.contract_id else False
            ),
            'previous_value': previous_value,
            'new_value': new_value,
            'details': details,
            'source': self._get_audit_source(),
            'company_id': self.company_id.id if hasattr(self, 'company_id') and self.company_id else self.env.company.id,
        })

    def _get_audit_source(self):
        if self.env.user.has_group('base.group_portal'):
            return 'portal'
        return 'ui'
