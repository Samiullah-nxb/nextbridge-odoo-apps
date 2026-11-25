"""Create the interim accounts when a chart of accounts is loaded after installation."""
from odoo import models


class AccountChartTemplate(models.AbstractModel):
    """Set up the interim accounts of a company right after its chart of accounts is loaded."""

    _inherit = 'account.chart.template'

    def _post_load_data(self, template_code, company, template_data):
        super()._post_load_data(template_code, company, template_data)
        company = company or self.env.company
        company._stock_move_accounting_setup_interim_accounts()
