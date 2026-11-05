# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models, fields


class ClmDelegateWizard(models.TransientModel):
    _name = 'clm.delegate.wizard'
    _description = 'Delegate Workflow Task'

    task_id = fields.Many2one('clm.workflow.task', string='Task', required=True)
    new_user_id = fields.Many2one('res.users', string='Delegate To', required=True)
    reason = fields.Text(string='Reason')

    def action_delegate(self):
        self.ensure_one()
        self.task_id.action_delegate(self.new_user_id.id, self.reason)
        return {'type': 'ir.actions.act_window_close'}
