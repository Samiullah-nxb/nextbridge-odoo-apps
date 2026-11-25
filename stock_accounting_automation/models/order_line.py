"""Purchase / sale order line helpers used by the accrued expense / revenue wizard."""
from odoo import _, models
from odoo.fields import Domain


class PurchaseOrderLine(models.Model):
    """Received-not-billed helpers for the accrued expense entry."""

    _inherit = 'purchase.order.line'

    def _stock_move_accounting_is_perpetual(self):
        """Receipts of this line are booked on Stock Interim (Received) by this module."""
        self.ensure_one()
        company = self.company_id
        product = self.product_id.with_company(company)
        return bool(
            company.stock_move_accounting
            and product.is_storable
            and product.valuation == 'real_time'
            and not any(m._is_dropshipped() for m in self.move_ids)
        )

    def _stock_move_accounting_accrual_lines_vals(self, date):
        """Accrual of the price difference of standard-cost goods received but not billed.

        The receipts already credited Stock Interim (Received) at the standard price;
        the liability towards the vendor is at the purchase price, the difference
        being booked on the price difference account when the bill arrives."""
        vals_list = []
        for line in self.with_context(accrual_entry_date=date):
            company = line.company_id
            currency = company.currency_id
            product = line.product_id.with_company(company)
            if product.cost_method != 'standard':
                continue
            qty_to_bill = line.qty_received_at_date - line.qty_invoiced_at_date
            if line.product_uom_id.compare(qty_to_bill, 0) <= 0:
                continue
            receipts = line.move_ids.filtered(
                lambda m: m.state == 'done' and m.is_in and m.account_valuation_tracked)
            received_qty = sum(receipts.mapped('account_valuation_qty'))
            if product.uom_id.is_zero(received_qty):
                continue
            unit_cost = sum(receipts.mapped('account_valuation_interim')) / received_qty
            price = line.currency_id._convert(line.price_unit_discounted, currency, company, date, round=False)
            price = line.product_uom_id._compute_price(price, product.uom_id)
            qty = line.product_uom_id._compute_quantity(qty_to_bill, product.uom_id)
            diff = currency.round(qty * (price - unit_cost))
            if currency.is_zero(diff):
                continue
            fpos = line.order_id.fiscal_position_id
            pdiff_account = fpos.map_account(product.categ_id.property_price_difference_account_id)
            interim = product.product_tmpl_id.get_product_accounts(fiscal_pos=fpos)['stock_input']
            if not pdiff_account or not interim:
                continue
            label = _('%(order)s - %(product)s; price difference on %(qty)s received not billed',
                      order=line.order_id.name, product=product.display_name, qty=qty_to_bill)
            vals_list += [
                {'name': label, 'account_id': pdiff_account.id, 'product_id': product.id,
                 'debit': max(diff, 0.0), 'credit': max(-diff, 0.0)},
                {'name': label, 'account_id': interim.id, 'product_id': product.id,
                 'debit': max(-diff, 0.0), 'credit': max(diff, 0.0)},
            ]
        return vals_list


class SaleOrderLine(models.Model):
    """Delivered-not-invoiced helpers for the accrued revenue entry."""

    _inherit = 'sale.order.line'

    def _stock_move_accounting_is_perpetual(self):
        """Deliveries of this line are booked on Stock Interim (Delivered) by this module."""
        self.ensure_one()
        company = self.company_id
        product = self.product_id.with_company(company)
        return bool(
            company.stock_move_accounting
            and not self.is_downpayment
            and product.is_storable
            and product.valuation == 'real_time'
            and not any(m._is_dropshipped() or m._is_dropshipped_returned() for m in self.move_ids)
        )

    def _stock_move_accounting_accrual_lines_vals(self, date):
        """Cost of goods delivered but not invoiced (or invoiced but not delivered) at
        ``date``: the balance of Stock Interim (Delivered) for the line, moved to the
        expense (COGS) account so that the period carries its cost of sales."""
        vals_list = []
        for line in self:
            company = line.company_id
            currency = company.currency_id
            product = line.product_id.with_company(company)
            accounts = product.product_tmpl_id.get_product_accounts(fiscal_pos=line.order_id.fiscal_position_id)
            interim, expense = accounts['stock_output'], accounts['expense']
            if not interim or not expense:
                continue
            moves = line.move_ids.filtered(lambda m: m.state == 'done' and m.account_valuation_tracked)
            legacy_entries = moves.account_valuation_move_ids | moves.account_move_id
            domain = Domain([
                ('company_id', '=', company.id),
                ('account_id', '=', interim.id),
                ('product_id', '=', product.id),
                ('parent_state', '=', 'posted'),
                ('date', '<=', date),
            ]) & (
                Domain('stock_valuation_move_id', 'in', moves.ids)
                | (Domain('stock_valuation_move_id', '=', False) & Domain('move_id', 'in', legacy_entries.ids)
                   & Domain('display_type', '!=', 'cogs'))
                | (Domain('display_type', '=', 'cogs') & Domain('cogs_origin_id.sale_line_ids', 'in', line.ids))
            )
            balance = currency.round(sum(self.env['account.move.line'].search(domain).mapped('balance')))
            if currency.is_zero(balance):
                continue
            if balance > 0:
                label = _('%(order)s - %(product)s; goods delivered not invoiced (perpetual valuation)',
                          order=line.order_id.name, product=product.display_name)
            else:
                label = _('%(order)s - %(product)s; goods invoiced not delivered (perpetual valuation)',
                          order=line.order_id.name, product=product.display_name)
            vals_list += [
                {'name': label, 'account_id': expense.id, 'product_id': product.id,
                 'debit': max(balance, 0.0), 'credit': max(-balance, 0.0)},
                {'name': label, 'account_id': interim.id, 'product_id': product.id,
                 'debit': max(-balance, 0.0), 'credit': max(balance, 0.0)},
            ]
        return vals_list
