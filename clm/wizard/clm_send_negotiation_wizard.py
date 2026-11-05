# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models, fields
from odoo.exceptions import UserError


class ClmSendNegotiationWizard(models.TransientModel):
    _name = 'clm.send.negotiation.wizard'
    _description = 'Send for External Negotiation'

    version_id = fields.Many2one('clm.contract.version', string='Version', required=True)
    contract_id = fields.Many2one(
        'clm.contract', related='version_id.contract_id', readonly=True,
    )
    partner_id = fields.Many2one('res.partner', string='Counterparty', required=True)
    response_deadline = fields.Date(string='Response Deadline')
    notes = fields.Text(string='Notes')

    def action_send(self):
        self.ensure_one()
        if not self.partner_id:
            raise UserError('Please select a counterparty.')
        self.version_id.action_send_for_negotiation(
            self.partner_id,
            deadline=self.response_deadline,
            notes=self.notes or '',
        )
        if self.contract_id.state in ('internal_review_done', 'sent_to_counterparty', 'in_review'):
            self.contract_id.action_send_to_negotiation()
        return {'type': 'ir.actions.act_window_close'}
