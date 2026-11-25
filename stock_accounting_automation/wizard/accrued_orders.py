"""Accrued expense ("Bill to Receive") and accrued revenue ("Invoices to Issue") entries
that take the Stock Interim balances into account.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class AccountAccruedOrdersWizard(models.TransientModel):
    """Accrued expense / revenue entries ("Bill to Receive", "Invoices to Issue").

    Odoo 19 computes the accruals of perpetual products assuming receipts and
    deliveries post nothing. With this module they do:

    * goods received not billed are already credited on Stock Interim (Received)
      by the receipt: accruing them again would double the liability and the
      expense. Those purchase lines are left out; only the price difference of
      standard-cost goods (receipt at standard, liability at purchase price) is
      accrued, against Stock Interim (Received).
    * the cost of goods delivered not invoiced sits on Stock Interim (Delivered):
      it is accrued from that balance (COGS vs Stock Interim (Delivered)) instead
      of Odoo's estimate against the stock variation account.
    """
    _inherit = 'account.accrued.orders.wizard'

    def _stock_move_accounting_order_lines(self):
        """(order lines of the wizard, the ones valued by this module)"""
        self.ensure_one()
        model = self.env.context.get('active_model')
        if (
            model not in ('purchase.order', 'purchase.order.line', 'sale.order', 'sale.order.line')
            or not self.company_id.stock_move_accounting
            or self.amount  # manual amount: the user decides
        ):
            return None, None
        records = self.env[model].with_company(self.company_id).browse(self.env.context.get('active_ids', []))
        lines = records.order_line if model in ('purchase.order', 'sale.order') else records
        lines = lines.filtered(lambda l: l.product_id and not l.display_type)
        return lines, lines.filtered(lambda l: l._stock_move_accounting_is_perpetual())

    def _stock_move_accounting_accrual_date(self):
        date = self.env.context.get('accrual_entry_date')
        return fields.Date.from_string(date) if date else self.date

    def _compute_move_vals(self):
        lines, perpetual = self._stock_move_accounting_order_lines()
        if not perpetual:
            return super()._compute_move_vals()
        wizard = self.with_context(stock_move_accounting_perpetual_line_ids=tuple(perpetual.ids))
        if lines._name == 'purchase.order.line':
            wizard = wizard.with_context(active_model='purchase.order.line', active_ids=(lines - perpetual).ids)
        move_vals, orders_with_entries = super(AccountAccruedOrdersWizard, wizard)._compute_move_vals()
        extra_vals = perpetual._stock_move_accounting_accrual_lines_vals(self._stock_move_accounting_accrual_date())
        company_currency = self.company_id.currency_id
        move_currency = self.env['res.currency'].browse(move_vals.get('currency_id')) or company_currency
        for vals in extra_vals:
            if move_currency != company_currency:
                # same convention as Odoo's perpetual accrual lines
                vals.update({'currency_id': move_currency.id, 'amount_currency': 0.0})
            move_vals['line_ids'].append(fields.Command.create(vals))
        return move_vals, orders_with_entries

    @api.model
    def _get_product_expense_and_stock_var_accounts(self, product):
        # Lines valued by this module get their cost accrual from
        # `_stock_move_accounting_accrual_lines_vals` instead.
        perpetual_ids = self.env.context.get('stock_move_accounting_perpetual_line_ids')
        if perpetual_ids and self.env.context.get('active_model') in ('sale.order', 'sale.order.line'):
            lines = self.env['sale.order.line'].browse(perpetual_ids)
            if product in lines.product_id:
                return (False, False)
        return super()._get_product_expense_and_stock_var_accounts(product)

    def create_entries(self):
        self.ensure_one()
        _lines, perpetual = self._stock_move_accounting_order_lines()
        if perpetual and not self._compute_move_vals()[0]['line_ids']:
            raise UserError(_(
                "There is nothing to accrue: the goods received or delivered and not yet "
                "invoiced are already booked on the Stock Interim accounts by their receipts "
                "and deliveries."))
        return super().create_entries()
