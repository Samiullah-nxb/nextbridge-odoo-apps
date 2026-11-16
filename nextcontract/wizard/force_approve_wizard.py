# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models, fields
from odoo.exceptions import AccessError


class NextcontractForceApproveWizard(models.TransientModel):
    _name = 'nextcontract.force.approve.wizard'
    _description = 'Force Approve Contract Wizard'

    contract_id = fields.Many2one('nextcontract.contract', string="Contract", required=True)
    reason = fields.Text(string="Reason", required=True)

    def action_force_approve(self):
        self.ensure_one()
        if not self.env.user.has_group('nextcontract.group_nextcontract_legal_admin'):
            raise AccessError("Only a Legal Admin can force-approve a contract.")
        contract = self.contract_id
        previous_state = contract.state
        open_items = '; '.join(contract._nextcontract_approval_blockers()) or 'none'
        # The one explicit, audited override of the review gates.
        contract.with_context(nextcontract_force_approve=True).write({'state': 'approved'})
        summary = "Force approved by %s. Reason: %s" % (self.env.user.name, self.reason)
        contract._log_audit_event(
            'state_change', summary,
            previous_value=previous_state, new_value='approved',
            details="Review gates overridden. Open items: %s" % open_items,
        )
        contract.message_post(body=summary)
        return {'type': 'ir.actions.act_window_close'}
