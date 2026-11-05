# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models, fields
from odoo.exceptions import UserError


class ClmAuditEvent(models.Model):
    _name = 'clm.audit.event'
    _description = 'NextContract Audit Event'
    _order = 'create_date desc'
    _rec_name = 'summary'

    # ─── Immutability ────────────────────────────────────────────────────────
    def write(self, vals):
        if not self.env.su:
            raise UserError("Audit events are immutable and cannot be modified.")
        return super().write(vals)

    def unlink(self):
        if not self.env.su:
            raise UserError("Audit events cannot be deleted.")
        return super().unlink()

    # ─── Fields ──────────────────────────────────────────────────────────────
    event_type = fields.Selection([
        ('state_change', 'State Change'),
        ('execution', 'Contract Execution'),
        ('ai_approval', 'AI Suggestion Approved'),
        ('ai_rejection', 'AI Suggestion Rejected'),
        ('clause_unlock', 'Clause Unlocked'),
        ('clause_lock', 'Clause Locked'),
        ('risk_override', 'Risk Override'),
        ('portal_access_granted', 'Portal Access Granted'),
        ('portal_access_revoked', 'Portal Access Revoked'),
        ('portal_upload', 'Counterparty Upload'),
        ('delegation', 'Task Delegation'),
        ('migration_import', 'Migration Import'),
        ('migration_force', 'Migration Force Import'),
        ('version_locked', 'Version Locked/Executed'),
    ], string='Event Type', required=True, index=True)

    summary = fields.Char(string='Summary', required=True)

    # Who
    user_id = fields.Many2one(
        'res.users', string='Actor', required=True,
        default=lambda self: self.env.user, index=True
    )

    # Which record (generic reference)
    res_model = fields.Char(string='Model', required=True, index=True)
    res_id = fields.Many2oneReference(
        string='Record ID', model_field='res_model'
    )

    # Convenience link to contract
    contract_id = fields.Many2one(
        'clm.contract', string='Contract',
        index=True, ondelete='set null'
    )

    # Change tracking
    previous_value = fields.Char(string='Previous Value')
    new_value = fields.Char(string='New Value')
    details = fields.Text(string='Details / Metadata')

    # Context
    source = fields.Selection([
        ('ui', 'User Interface'),
        ('portal', 'Portal'),
        ('api', 'API'),
        ('cron', 'Scheduled Action'),
        ('system', 'System'),
    ], string='Source', default='ui')

    company_id = fields.Many2one(
        'res.company', string='Company',
        default=lambda self: self.env.company, index=True
    )
