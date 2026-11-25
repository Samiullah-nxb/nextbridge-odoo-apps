"""Company-level switch and default interim accounts of Stock Accounting Automation."""
from odoo import fields, models

INTERIM_ACCOUNTS = {
    'account_stock_input_id': 'Stock Interim (Received)',
    'account_stock_output_id': 'Stock Interim (Delivered)',
}


class ResCompany(models.Model):
    """Feature switch (``stock_move_accounting``) and company default interim accounts."""

    _inherit = 'res.company'

    stock_move_accounting = fields.Boolean(
        string='Journal Entries on Stock Moves', default=True,
        help="Post a journal entry for every valued stock move of products using perpetual "
             "valuation (receipts, deliveries, returns, adjustments, scrap, production).")
    account_stock_input_id = fields.Many2one(
        'account.account', string='Stock Interim (Received) Account', check_company=True,
        help="Counterpart of receipts; cleared by vendor bills.")
    account_stock_output_id = fields.Many2one(
        'account.account', string='Stock Interim (Delivered) Account', check_company=True,
        help="Counterpart of deliveries; cleared by the COGS lines of customer invoices.")

    def _stock_move_accounting_setup_interim_accounts(self):
        """Create (if needed) and assign the default interim accounts of the companies."""
        Account = self.env['account.account'].sudo()
        for company in self:
            if not company.chart_template:
                continue
            valuation = company.account_stock_valuation_id
            for fname, name in INTERIM_ACCOUNTS.items():
                if company[fname]:
                    continue
                account = Account.with_company(company).search([
                    *Account._check_company_domain(company),
                    ('name', '=', name),
                ], limit=1)
                if not account:
                    account = Account.with_company(company).create({
                        'name': name,
                        'code': company._stock_move_accounting_free_code(valuation.code or '1100'),
                        'account_type': 'asset_current',
                        'reconcile': True,
                        'company_ids': [fields.Command.link(company.id)],
                    })
                company[fname] = account

    def _stock_move_accounting_free_code(self, base_code):
        """Return the first unused account code following ``base_code``."""
        self.ensure_one()
        Account = self.env['account.account'].sudo().with_company(self)

        def is_free(code):
            return not Account.search_count([*Account._check_company_domain(self), ('code', '=', code)], limit=1)

        if base_code.isdigit():
            for step in range(1, 200):
                code = str(int(base_code) + step).zfill(len(base_code))
                if is_free(code):
                    return code
        for step in range(1, 1000):
            code = f'{base_code}{step}'
            if is_free(code):
                return code
        return f'{base_code}X'
