# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models


class NextcontractContractClauseAccess(models.Model):
    """Visible only to users who can see the contract (see nextcontract.contract.child.mixin)."""
    _name = 'nextcontract.contract.clause'
    _inherit = ['nextcontract.contract.clause', 'nextcontract.contract.child.mixin']


class NextcontractAssemblyRequestAccess(models.Model):
    _name = 'nextcontract.assembly.request'
    _inherit = ['nextcontract.assembly.request', 'nextcontract.contract.child.mixin']
