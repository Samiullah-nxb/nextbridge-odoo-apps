# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models


class ClmContractVersionAccess(models.Model):
    """Visible only to users who can see the contract (see clm.contract.child.mixin)."""
    _name = 'clm.contract.version'
    _inherit = ['clm.contract.version', 'clm.contract.child.mixin']
