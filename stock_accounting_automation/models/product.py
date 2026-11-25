"""Product category interim accounts, account resolution and cost revaluation entries."""
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class ProductCategory(models.Model):
    """Per-category overrides of the interim accounts (company dependent)."""

    _inherit = 'product.category'

    property_stock_account_input_categ_id = fields.Many2one(
        'account.account', 'Stock Interim (Received)', company_dependent=True, ondelete='restrict',
        check_company=True,
        help="Counterpart of receipts with perpetual valuation, cleared by the vendor bill. "
             "Falls back on the company default when empty.")
    property_stock_account_output_categ_id = fields.Many2one(
        'account.account', 'Stock Interim (Delivered)', company_dependent=True, ondelete='restrict',
        check_company=True,
        help="Counterpart of deliveries with perpetual valuation, cleared by the customer invoice. "
             "Falls back on the company default when empty.")


class ProductTemplate(models.Model):
    """Add ``stock_input`` / ``stock_output`` (interim accounts) to the product accounts."""

    _inherit = 'product.template'

    def _get_product_accounts(self):
        accounts = super()._get_product_accounts()
        company = self.env.company
        accounts['stock_input'] = self.categ_id.property_stock_account_input_categ_id or company.account_stock_input_id or company.root_id.account_stock_input_id
        accounts['stock_output'] = self.categ_id.property_stock_account_output_categ_id or company.account_stock_output_id or company.root_id.account_stock_output_id
        return accounts


class ProductProduct(models.Model):
    """Revaluation entry when the cost of a standard / average cost product is changed."""

    _inherit = 'product.product'

    def _change_standard_price(self, old_price):
        """Post a revaluation entry for the on-hand quantity when the cost of a
        perpetual (standard / average) product is changed manually."""
        res = super()._change_standard_price(old_price)
        company = self.env.company
        if not company.stock_move_accounting or self.env.context.get('valuation_date'):
            return res
        currency = company.currency_id
        aml_vals_list = []
        for product in self:
            if (
                not product.is_storable
                or product.valuation != 'real_time'
                or product.cost_method == 'fifo'
                or product.standard_price == old_price.get(product)
            ):
                continue
            quantity = product._with_valuation_context().qty_available
            delta = currency.round((product.standard_price - (old_price.get(product) or 0.0)) * quantity)
            if currency.is_zero(delta):
                continue
            accounts = product.product_tmpl_id._get_product_accounts()
            valuation_account = accounts['stock_valuation']
            counterpart = accounts['stock_variation'] or accounts['expense']
            if not valuation_account or not counterpart:
                raise UserError(_(
                    "Configure the stock valuation account and a stock variation or expense account "
                    "on the category '%(category)s' to revalue '%(product)s'.",
                    category=product.categ_id.display_name, product=product.display_name))
            name = _('%(product)s - Cost update from %(old)s to %(new)s',
                     product=product.display_name,
                     old=currency.format(old_price.get(product) or 0.0),
                     new=currency.format(product.standard_price))
            debit, credit = (valuation_account, counterpart) if delta > 0 else (counterpart, valuation_account)
            for account, balance in ((debit, abs(delta)), (credit, -abs(delta))):
                aml_vals_list.append({
                    'name': name,
                    'account_id': account.id,
                    'balance': balance,
                    'product_id': product.id,
                    'quantity': quantity,
                    'product_uom_id': product.uom_id.id,
                })
        if aml_vals_list:
            account_move = self.env['account.move'].sudo().create({
                'journal_id': company.account_stock_journal_id.id,
                'ref': _('Product cost revaluation'),
                'date': fields.Date.context_today(self),
                'move_type': 'entry',
                'company_id': company.id,
                'line_ids': [fields.Command.create(vals) for vals in aml_vals_list],
            })
            account_move._post()
        return res


class ProductValue(models.Model):
    """Manual valuation adjustments (Adjust Valuation) are booked against stock variation."""

    _inherit = 'product.value'

    @api.model_create_multi
    def create(self, vals_list):
        # A product.value on a move is a manual valuation adjustment: its accounting
        # counterpart is the stock variation account, not the interim accounts.
        return super(ProductValue, self.with_context(stock_move_accounting_manual=True)).create(vals_list)
