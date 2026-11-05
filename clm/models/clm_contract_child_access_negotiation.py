# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models


class ClmNegotiationRoundAccess(models.Model):
    """Visible only to users who can see the contract (see clm.contract.child.mixin)."""
    _name = 'clm.negotiation.round'
    _inherit = ['clm.negotiation.round', 'clm.contract.child.mixin']
