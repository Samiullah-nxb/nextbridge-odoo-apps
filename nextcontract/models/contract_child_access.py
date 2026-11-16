# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models


class NextcontractContractVersionAccess(models.Model):
    """Visible only to users who can see the contract (see nextcontract.contract.child.mixin)."""
    _name = 'nextcontract.contract.version'
    _inherit = ['nextcontract.contract.version', 'nextcontract.contract.child.mixin']
