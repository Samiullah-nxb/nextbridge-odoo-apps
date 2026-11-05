# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models, fields

class PurchaseOrderClm(models.Model):
    _inherit = 'purchase.order'

    clm_contract_ids = fields.Many2many(
        'clm.contract',
        'clm_contract_purchase_order_rel',
        'purchase_order_id',
        'contract_id',
        string='Governing Contracts',
        help="NextContract Contracts governing this purchase order."
    )
