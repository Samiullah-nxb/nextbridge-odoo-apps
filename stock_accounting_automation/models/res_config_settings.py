"""Settings fields (Inventory > Configuration > Settings > Valuation)."""
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    """Expose the company switch and default interim accounts in the Inventory settings."""

    _inherit = 'res.config.settings'

    stock_move_accounting = fields.Boolean(
        related='company_id.stock_move_accounting', readonly=False)
    account_stock_input_id = fields.Many2one(
        related='company_id.account_stock_input_id', readonly=False)
    account_stock_output_id = fields.Many2one(
        related='company_id.account_stock_output_id', readonly=False)
