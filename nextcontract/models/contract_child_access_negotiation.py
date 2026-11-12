# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models


class NextcontractNegotiationRoundAccess(models.Model):
    """Visible only to users who can see the contract (see nextcontract.contract.child.mixin)."""
    _name = 'nextcontract.negotiation.round'
    _inherit = ['nextcontract.negotiation.round', 'nextcontract.contract.child.mixin']
