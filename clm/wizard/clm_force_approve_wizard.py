# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models, fields
from odoo.exceptions import AccessError


class ClmForceApproveWizard(models.TransientModel):
    _name = 'clm.force.approve.wizard'
    _description = 'Force Approve Contract Wizard'

    contract_id = fields.Many2one('clm.contract', string="Contract", required=True)
    reason = fields.Text(string="Reason", required=True)

    def action_force_approve(self):
        self.ensure_one()
        if not self.env.user.has_group('clm.group_clm_legal_admin'):
            raise AccessError("Only a Legal Admin can force-approve a contract.")
        contract = self.contract_id
        previous_state = contract.state
        open_items = '; '.join(contract._clm_approval_blockers()) or 'none'
        # The one explicit, audited override of the review gates.
        contract.with_context(clm_force_approve=True).write({'state': 'approved'})
        summary = "Force approved by %s. Reason: %s" % (self.env.user.name, self.reason)
        contract._log_audit_event(
            'state_change', summary,
            previous_value=previous_state, new_value='approved',
            details="Review gates overridden. Open items: %s" % open_items,
        )
        contract.message_post(body=summary)
        return {'type': 'ir.actions.act_window_close'}
