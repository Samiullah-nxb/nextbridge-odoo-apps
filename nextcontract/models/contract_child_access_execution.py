# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models


class NextcontractContractAlertAccess(models.Model):
    """Visible only to users who can see the contract (see nextcontract.contract.child.mixin)."""
    _name = 'nextcontract.contract.alert'
    _inherit = ['nextcontract.contract.alert', 'nextcontract.contract.child.mixin']
