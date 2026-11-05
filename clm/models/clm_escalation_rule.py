# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models, fields, api
from datetime import timedelta


class ClmEscalationRule(models.Model):
    _name = 'clm.escalation.rule'
    _description = 'NextContract Escalation Rule'
    _order = 'level, sequence'

    name = fields.Char(string='Rule Name', required=True)
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)

    applies_to = fields.Selection([
        ('workflow_task', 'Workflow Task'),
        ('obligation', 'Obligation'),
        ('contract_expiry', 'Contract Expiry'),
        ('negotiation_deadline', 'Negotiation Deadline'),
    ], string='Applies To', required=True)

    grace_period_days = fields.Integer(
        string='Grace Period (Days)', required=True, default=3,
        help='Number of days after due date before escalation triggers'
    )
    level = fields.Selection([
        ('1', 'Level 1'),
        ('2', 'Level 2'),
        ('3', 'Level 3'),
    ], string='Escalation Level', required=True, default='1')

    escalation_user_id = fields.Many2one('res.users', string='Escalate To User')
    escalation_group_id = fields.Many2one('res.groups', string='Escalate To Group')
    activity_type_id = fields.Many2one(
        'mail.activity.type', string='Activity Type',
        help='Activity type to create on escalation'
    )

    company_id = fields.Many2one(
        'res.company', string='Company',
        default=lambda self: self.env.company
    )

    @api.model
    def _cron_evaluate_escalations(self):
        """Evaluate all active escalation rules. Called by ir.cron every 4 hours."""
        rules = self.search([('active', '=', True)])
        today = fields.Date.today()
        for rule in rules:
            if rule.applies_to == 'workflow_task':
                rule._escalate_overdue_tasks(today)

    def _escalate_overdue_tasks(self, today):
        """Find overdue workflow tasks and create escalation activities."""
        self.ensure_one()
        cutoff = today - timedelta(days=self.grace_period_days)
        overdue_tasks = self.env['clm.workflow.task'].search([
            ('status', 'not in', ['completed', 'cancelled']),
            ('due_date', '<', cutoff),
            ('escalation_level', '<', self.level),
        ])
        for task in overdue_tasks:
            task.write({
                'escalation_level': self.level,
                'escalation_date': today,
            })
