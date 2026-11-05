# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models, fields

class ClmWorkflowDelegation(models.Model):
    _name = 'clm.workflow.delegation'
    _description = 'Workflow Delegation History'

    task_id = fields.Many2one('clm.workflow.task', string='Task', required=True, ondelete='cascade')
    original_user_id = fields.Many2one('res.users', string='Original Assignee')
    delegated_to_id = fields.Many2one('res.users', string='Delegated To')
    delegated_by_id = fields.Many2one('res.users', string='Delegated By', default=lambda self: self.env.user)
    delegation_date = fields.Datetime(string='Delegation Date', default=fields.Datetime.now)
    reason = fields.Text(string='Reason')
