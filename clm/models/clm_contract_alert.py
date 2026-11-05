# Part of NextContract. See LICENSE file for full copyright and licensing details.

from datetime import timedelta

from odoo import api, fields, models


class ClmContractAlert(models.Model):
    _name = 'clm.contract.alert'
    _description = 'Contract Alert Configuration'
    _order = 'contract_id, alert_type'

    contract_id = fields.Many2one(
        'clm.contract', string='Contract', required=True, ondelete='cascade', index=True,
    )
    alert_type = fields.Selection([
        ('expiry', 'Expiry Date'),
        ('renewal', 'Renewal Date'),
        ('notice_period', 'Notice Period'),
        ('auto_renewal', 'Auto-Renewal'),
        ('payment_milestone', 'Payment Milestone'),
        ('bank_guarantee', 'Bank Guarantee Expiry'),
        ('insurance', 'Insurance Expiry'),
        ('sla_review', 'SLA Review'),
        ('obligation', 'Obligation Due'),
        ('contract_review', 'Contract Review'),
    ], string='Alert Type', required=True)
    lead_days = fields.Integer(string='Lead Time (Days)', default=30)
    active = fields.Boolean(default=True)
    last_notified = fields.Datetime(string='Last Notified')
    notes = fields.Char(string='Notes')
    target_date = fields.Date(
        string='Target Date',
        compute='_compute_alert_status',
    )
    days_to_target = fields.Integer(
        string='Days Remaining',
        compute='_compute_alert_status',
    )
    is_due = fields.Boolean(
        string='Due',
        compute='_compute_alert_status',
    )

    _unique_alert_per_contract = models.Constraint(
        'unique(contract_id, alert_type)',
        'Each alert type can only be configured once per contract.',
    )

    @api.depends(
        'alert_type', 'lead_days', 'active', 'last_notified',
        'contract_id.expiry_date', 'contract_id.renewal_date',
        'contract_id.notice_period_days', 'contract_id.state',
    )
    def _compute_alert_status(self):
        today = fields.Date.context_today(self)
        for alert in self:
            target = alert._get_target_date()
            alert.target_date = target
            if not target:
                alert.days_to_target = 0
                alert.is_due = False
                continue
            alert.days_to_target = (target - today).days
            alert.is_due = bool(
                alert.active
                and alert.contract_id.state in ('active', 'executed', 'expiring_soon')
                and today >= target - timedelta(days=alert.lead_days or 0)
            )

    def _get_target_date(self):
        """Resolve the date this alert watches on the parent contract."""
        self.ensure_one()
        contract = self.contract_id
        if self.alert_type == 'expiry':
            return contract.expiry_date
        if self.alert_type == 'renewal':
            return contract.renewal_date or contract.expiry_date
        if self.alert_type == 'notice_period':
            # Fire lead_days before the contractual notice deadline
            if not contract.expiry_date:
                return False
            notice_days = contract.notice_period_days or 0
            if notice_days:
                return contract.expiry_date - timedelta(days=notice_days)
            return contract.expiry_date
        if self.alert_type == 'auto_renewal':
            return contract.renewal_date or contract.expiry_date
        # Other types: use expiry as a sensible default when set
        return contract.expiry_date
