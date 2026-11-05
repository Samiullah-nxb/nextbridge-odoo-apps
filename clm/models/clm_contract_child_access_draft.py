# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models


class ClmContractClauseAccess(models.Model):
    """Visible only to users who can see the contract (see clm.contract.child.mixin)."""
    _name = 'clm.contract.clause'
    _inherit = ['clm.contract.clause', 'clm.contract.child.mixin']


class ClmAssemblyRequestAccess(models.Model):
    _name = 'clm.assembly.request'
    _inherit = ['clm.assembly.request', 'clm.contract.child.mixin']
