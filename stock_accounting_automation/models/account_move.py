"""Vendor bill / customer invoice integration: interim accounts on bill and COGS lines,
receipt resynchronisation on draft / cancel / reverse, automatic reconciliation and
the database guarantee of a single initial entry per stock move.
"""
from odoo import _, fields, models
from odoo.exceptions import UserError


class AccountMove(models.Model):
    """Bill / invoice side of the perpetual flow (interim accounts, resync, reconciliation)."""

    _inherit = 'account.move'

    def _stock_account_prepare_realtime_out_lines_vals(self):
        """COGS lines of customer invoices credit the Stock Interim (Delivered)
        account instead of the stock account: the delivery already credited stock."""
        lines_vals_list = super()._stock_account_prepare_realtime_out_lines_vals()
        for vals in lines_vals_list:
            invoice = self.browse(vals['move_id'])
            company = invoice.company_id
            if not company.stock_move_accounting or not vals.get('product_id'):
                continue
            product = self.env['product.product'].browse(vals['product_id']).with_company(company)
            accounts = product.product_tmpl_id.get_product_accounts(fiscal_pos=invoice.fiscal_position_id)
            if accounts['stock_output'] and vals['account_id'] == accounts['stock_valuation'].id:
                vals['account_id'] = accounts['stock_output'].id
        return lines_vals_list

    def _post(self, soft=True):
        posted = super()._post(soft=soft)
        if self.env.context.get('move_reverse_cancel'):
            # Odoo does not revalue receipts when a bill is cancelled through a
            # reversal: do it, so that the bill's price adjustment is reversed too.
            posted._stock_move_accounting_resync_receipts()
        else:
            posted.filtered(lambda m: m.is_invoice(include_receipts=True))._stock_move_accounting_reconcile()
        return posted

    def button_draft(self):
        self._stock_move_accounting_check_valuation_entries()
        res = super().button_draft()
        self._stock_move_accounting_resync_receipts()
        return res

    def button_cancel(self):
        res = super().button_cancel()
        self._stock_move_accounting_resync_receipts()
        return res

    def _stock_move_accounting_check_valuation_entries(self):
        """Valuation entries posted by this module mirror the value booked on their
        stock moves; resetting them would silently desynchronise stock and accounting."""
        if self.env.context.get('stock_move_accounting_force_draft'):
            return
        entries = self.filtered(lambda m: m.state == 'posted' and m.line_ids.stock_valuation_move_id)
        if entries:
            raise UserError(_(
                "%(entries)s are stock valuation entries and cannot be reset to draft.\n"
                "Use 'Adjust Valuation' on the stock move to change its value: the "
                "difference is then posted as a new adjustment entry.",
                entries=', '.join(entries.mapped('display_name'))))

    def _stock_move_accounting_resync_receipts(self):
        """Bring the value (and the valuation entries) of the receipts back in line
        with the vendor bills that are still posted."""
        bills = self.filtered(
            lambda m: m.company_id.stock_move_accounting and m.is_purchase_document(include_receipts=True))
        if not bills:
            return
        moves = bills.line_ids._get_stock_moves().filtered(
            lambda m: m.state == 'done' and (m.is_in or m.is_dropship))
        if moves:
            moves._set_value()

    def _stock_move_accounting_reconcile(self, products=None):
        """Reconcile interim lines of the invoices with the entries of their stock moves."""
        for invoice in self:
            company = invoice.company_id
            if invoice.state != 'posted' or not company.stock_move_accounting:
                continue
            invoice = invoice.with_company(company)
            # the last-step moves (receipts / deliveries) and their returns
            stock_moves = (
                invoice._stock_account_get_last_step_stock_moves()
                | invoice.invoice_line_ids._get_stock_moves().filtered(lambda m: m.state == 'done')
            )
            if not stock_moves:
                continue
            key = 'stock_output' if invoice.is_sale_document(include_receipts=True) else 'stock_input'
            invoice_products = invoice.invoice_line_ids.product_id
            if products:
                invoice_products &= products
            for product in invoice_products:
                product = product.with_company(company)
                if product.valuation != 'real_time':
                    continue
                account = product.product_tmpl_id.get_product_accounts(fiscal_pos=invoice.fiscal_position_id)[key]
                if not account or not account.reconcile:
                    continue
                product_moves = stock_moves.filtered(lambda m: m.product_id == product)
                entries = product_moves.account_valuation_move_ids | product_moves.account_move_id
                lines = (invoice.line_ids | entries.line_ids).filtered(
                    lambda l: l.product_id == product
                    and l.account_id == account
                    and l.parent_state == 'posted'
                    and not l.reconciled)
                # also matches adjustment pairs left by bills reset to draft and reposted
                if lines.filtered(lambda l: l.balance > 0) and lines.filtered(lambda l: l.balance < 0):
                    lines.reconcile()


class AccountMoveLine(models.Model):
    """Source-move traceability, unique initial entry and interim account on bill lines."""

    _inherit = 'account.move.line'

    stock_valuation_move_id = fields.Many2one(
        'stock.move', string='Valued Stock Move', copy=False, readonly=True, index='btree_not_null',
        help="Stock move whose valuation this journal item books (Journal Entries on Stock Moves).")
    stock_valuation_role = fields.Selection(
        [('initial', 'Initial valuation'), ('adjustment', 'Valuation adjustment')],
        string='Valuation Role', copy=False, readonly=True)

    # Database guarantee: whatever the code path, a stock move gets one initial
    # valuation entry only (one debit and one credit line on distinct accounts).
    _stock_valuation_initial_unique = models.UniqueIndex(
        "(stock_valuation_move_id, account_id) WHERE stock_valuation_role = 'initial'",
        "This stock move already has its initial valuation entry.",
    )

    def _compute_account_id(self):
        """Vendor bill lines of perpetual products go to Stock Interim (Received):
        the receipt already debited the stock account."""
        super()._compute_account_id()
        for line in self:
            if line.display_type != 'product' or not line.product_id or not line.move_id.is_purchase_document():
                continue
            company = line.company_id
            if not company.stock_move_accounting or not line._eligible_for_stock_account():
                continue
            product = line.product_id.with_company(company)
            if product.valuation != 'real_time':
                continue
            accounts = product.product_tmpl_id.get_product_accounts(fiscal_pos=line.move_id.fiscal_position_id)
            if accounts['stock_input']:
                line.account_id = accounts['stock_input']

    def _get_price_unit_val_dif_and_relevant_qty(self):
        """Standard cost: measure the bill price difference against the value the
        receipts actually booked (Odoo uses the current standard price, which leaves
        a residual on Stock Interim (Received) when the standard price changed
        between the receipt and the bill)."""
        price_unit_val_dif, relevant_qty = super()._get_price_unit_val_dif_and_relevant_qty()
        company = self.company_id
        product = self.product_id.with_company(company)
        if not company.stock_move_accounting or product.cost_method != 'standard' or product.valuation != 'real_time':
            return price_unit_val_dif, relevant_qty
        receipts = self._get_stock_moves().filtered(
            lambda m: m.state == 'done' and m.is_in and m.account_valuation_tracked and m.product_id == product)
        received_qty = sum(receipts.mapped('account_valuation_qty'))
        if product.uom_id.is_zero(received_qty):
            return price_unit_val_dif, relevant_qty
        unit_cost = sum(receipts.mapped('account_valuation_interim')) / received_qty
        valuation_price_unit = product.uom_id._compute_price(unit_cost, self.product_uom_id)
        if self.move_id.move_type == 'in_refund':
            valuation_price_unit = -valuation_price_unit
        valuation_price_unit = self.company_currency_id._convert(
            valuation_price_unit, self.currency_id, company, self.date, round=False)
        return self._get_gross_unit_price() - valuation_price_unit, relevant_qty

    def _stock_move_accounting_posted_cogs_lines(self):
        """COGS lines already posted on the interim account for the same sale lines
        (sale_stock only looks at the stock valuation account)."""
        self.ensure_one()
        company = self.company_id
        if not company.stock_move_accounting or not self.sale_line_ids:
            return self.browse()
        accounts = self.product_id.with_company(company).product_tmpl_id.get_product_accounts(
            fiscal_pos=self.move_id.fiscal_position_id)
        output_account = accounts['stock_output']
        if not output_account or output_account == accounts['stock_valuation']:
            return self.browse()
        sale_lines = self.sale_line_ids
        return sale_lines.order_id.invoice_ids.filtered(lambda m: m.move_type == 'out_invoice').line_ids.filtered(
            lambda line: line.display_type == 'cogs'
            and line.account_id == output_account
            and line.cogs_origin_id.sale_line_ids & sale_lines)

    def _get_cogs_qty(self):
        qty = super()._get_cogs_qty()
        posted = self._stock_move_accounting_posted_cogs_lines()
        return qty + sum(posted.mapped(
            lambda line: line.product_uom_id._compute_quantity(line.quantity, line.product_id.uom_id)
            * (-1 if line.move_id.move_type == 'out_refund' else 1)))

    def _get_posted_cogs_value(self):
        value = super()._get_posted_cogs_value()
        return value - sum(self._stock_move_accounting_posted_cogs_lines().mapped('balance'))
