# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models


class ClmContractAlertAccess(models.Model):
    """Visible only to users who can see the contract (see clm.contract.child.mixin)."""
    _name = 'clm.contract.alert'
    _inherit = ['clm.contract.alert', 'clm.contract.child.mixin']
