# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models, fields, api
from odoo.exceptions import UserError

class ClmContractVersion(models.Model):
    _inherit = 'clm.contract.version'

    negotiation_round_id = fields.Many2one(
        'clm.negotiation.round', string='Negotiation Round',
        help='The negotiation round this version belongs to (if any).'
    )

    def action_send_for_negotiation(self, partner, deadline=False, notes=""):
        """
        Creates a new negotiation round with this version as the outbound version,
        and ensures the partner has portal access.
        """
        self.ensure_one()

        if isinstance(partner, int):
            partner = self.env['res.partner'].browse(partner)
        
        if self.version_type not in ('internal_draft', 'negotiated_draft'):
            raise UserError("Only draft or negotiated versions can be sent for negotiation.")

        # Ensure portal access
        access_domain = [
            ('contract_id', '=', self.contract_id.id),
            ('partner_id', '=', partner.id)
        ]
        access_exists = self.env['clm.contract.portal.access'].search_count(access_domain)
        if not access_exists:
            self.env['clm.contract.portal.access'].create({
                'contract_id': self.contract_id.id,
                'partner_id': partner.id,
            })

        # Calculate next round number
        existing_rounds = self.env['clm.negotiation.round'].search([('contract_id', '=', self.contract_id.id)])
        next_round_num = len(existing_rounds) + 1

        # Create round
        round_vals = {
            'contract_id': self.contract_id.id,
            'round_number': next_round_num,
            'outbound_version_id': self.id,
            'status': 'in_progress',
            'recipient_id': partner.id,
            'response_deadline': deadline,
            'notes': notes,
        }
        
        new_round = self.env['clm.negotiation.round'].create(round_vals)
        
        # Link this version to the round
        self.negotiation_round_id = new_round.id
        self.version_type = 'negotiated_draft'

        email_to = self.env['clm.mail.notify'].emails_for_partners(partner)
        if email_to:
            self.env['clm.mail.notify'].send(
                new_round,
                'clm.mail_template_clm_negotiation_sent',
                email_to=email_to,
            )

        return new_round

    def action_compare_with_parent(self):
        """Redline this version against the nearest earlier differing version.

        Parent alone is often identical wording (Signature Draft → Executed).
        Prefer a version that actually changed the Word file or frozen text.
        """
        self.ensure_one()
        base = self.contract_id._clm_previous_differing_version(self)
        if not base:
            raise UserError(
                "Version %s has no earlier version to compare with." % self.version_number
            )
        return self.env['clm.document.comparison']._clm_compare(base, self)._clm_open_action()

    def action_open_send_negotiation_wizard(self):
        self.ensure_one()
        return {
            'name': 'Send to Counterparty',
            'type': 'ir.actions.act_window',
            'res_model': 'clm.send.negotiation.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_version_id': self.id,
                'default_partner_id': self.contract_id.partner_id.id,
            },
        }
