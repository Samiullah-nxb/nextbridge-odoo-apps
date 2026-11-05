# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models, fields, api

class ClmObligation(models.Model):
    _name = 'clm.obligation'
    _description = 'Contract Obligation'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    contract_id = fields.Many2one('clm.contract', required=True, ondelete='cascade')
    name = fields.Char('Description', required=True, tracking=True)
    
    obligation_type = fields.Selection([
        ('payment', 'Payment'),
        ('deliverable', 'Deliverable'),
        ('sla', 'SLA / Performance'),
        ('notice', 'Notice / Reporting'),
        ('renewal', 'Renewal / Expiry Alert'),
        ('other', 'Other')
    ], required=True, default='deliverable', tracking=True)
    
    owner_id = fields.Many2one('res.users', string='Owner', required=True, default=lambda self: self.env.user, tracking=True)
    due_date = fields.Date('Due Date', required=True, tracking=True)
    
    status = fields.Selection([
        ('pending', 'Pending'),
        ('in_progress', 'In Progress'),
        ('completed', 'Completed'),
        ('overdue', 'Overdue'),
        ('cancelled', 'Cancelled')
    ], default='pending', tracking=True)
    
    priority = fields.Selection([
        ('0', 'Normal'),
        ('1', 'High')
    ], default='0', tracking=True)
    
    evidence_required = fields.Boolean('Evidence Required', default=False)
    completed_date = fields.Date('Completed Date', readonly=True)
    
    def action_mark_completed(self):
        for record in self:
            record.status = 'completed'
            record.completed_date = fields.Date.today()

    @api.model
    def _cron_check_overdue_obligations(self):
        overdue_obligations = self.search([
            ('status', 'in', ['pending', 'in_progress']),
            ('due_date', '<', fields.Date.today())
        ])
        for obligation in overdue_obligations:
            obligation.status = 'overdue'
            obligation.message_post(body="Obligation is now overdue.")
            
            # Create a mail.activity for the owner
            obligation.activity_schedule(
                'mail.mail_activity_data_todo',
                summary=f'Overdue Obligation: {obligation.name}',
                note='This obligation has passed its due date and requires immediate action.',
                user_id=obligation.owner_id.id,
                date_deadline=fields.Date.today()
            )
            if obligation.owner_id and obligation.owner_id.email:
                obligation.env['clm.mail.notify'].send(
                    obligation,
                    'clm.mail_template_clm_obligation_overdue',
                    email_to=obligation.owner_id.email,
                )
