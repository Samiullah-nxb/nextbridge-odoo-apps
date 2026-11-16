# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models, fields

class NextcontractContractPurchase(models.Model):
    _inherit = 'nextcontract.contract'

    purchase_order_ids = fields.Many2many(
        'purchase.order', 
        'nextcontract_contract_purchase_order_rel', 
        'contract_id', 
        'purchase_order_id', 
        string='Purchase Orders',
        help="Purchase orders governed by this contract."
    )
