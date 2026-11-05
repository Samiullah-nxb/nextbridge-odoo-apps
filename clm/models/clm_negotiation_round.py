# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models, fields, api
from odoo.exceptions import ValidationError

class ClmNegotiationRound(models.Model):
    _name = 'clm.negotiation.round'
    _description = 'Negotiation Round'
    _order = 'contract_id, round_number desc'

    contract_id = fields.Many2one(
        'clm.contract', string='Contract', required=True, ondelete='cascade',
        index=True
    )
    round_number = fields.Integer(string='Round', default=1, required=True)
    @api.constrains('contract_id', 'outbound_version_id', 'inbound_version_id')
    def _check_versions_belong_to_contract(self):
        for negotiation_round in self.sudo():
            versions = negotiation_round.outbound_version_id | negotiation_round.inbound_version_id
            if versions.filtered(lambda v: v.contract_id != negotiation_round.contract_id):
                raise ValidationError(
                    "The versions of a negotiation round must belong to the round's contract."
                )

    outbound_version_id = fields.Many2one(
        'clm.contract.version', string='Outbound Version',
        required=True, ondelete='restrict',
        help='The version sent to the counterparty.'
    )
    inbound_version_id = fields.Many2one(
        'clm.contract.version', string='Inbound Version',
        ondelete='restrict',
        help='The revised version received from the counterparty.'
    )
    
    status = fields.Selection([
        ('in_progress', 'Awaiting Counterparty'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled')
    ], string='Status', default='in_progress', required=True)
    
    sender_id = fields.Many2one('res.users', string='Sent By', default=lambda self: self.env.user)
    recipient_id = fields.Many2one('res.partner', string='Sent To')
    sent_at = fields.Datetime(string='Sent At', default=fields.Datetime.now)
    received_at = fields.Datetime(string='Received At')
    
    response_deadline = fields.Date(string='Response Deadline')
    notes = fields.Text(string='Notes')

    _unique_round = models.Constraint('unique(contract_id, round_number)', 'Round numbers must be unique per contract.')
