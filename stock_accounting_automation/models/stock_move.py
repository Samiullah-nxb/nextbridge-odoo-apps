"""Core engine: initial valuation entry per stock move, value-change adjustments,
duplicate prevention (row lock + tracking flags) and interim reconciliation.
"""
from collections import defaultdict
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import SQL


class StockMove(models.Model):
    """Post and keep in sync the journal entries of valued stock moves."""

    _inherit = 'stock.move'

    account_valuation_move_ids = fields.Many2many(
        'account.move', 'stock_move_account_valuation_rel', 'stock_move_id', 'account_move_id',
        string='Valuation Entries', copy=False, readonly=True,
        help="Journal entries posted to value this stock move (initial entry and adjustments).")
    account_valuation_tracked = fields.Boolean(
        'Valuation Posted', copy=False, readonly=True,
        help="Technical: the value of this move is kept in sync with the accounting.")
    account_valuation_posted = fields.Monetary(
        'Posted Value', currency_field='company_currency_id', copy=False, readonly=True,
        help="Amount currently booked in the stock valuation account for this move.")
    account_valuation_qty = fields.Float(
        'Posted Quantity', digits='Product Unit', copy=False, readonly=True)
    account_valuation_interim = fields.Monetary(
        'Value on Interim', currency_field='company_currency_id', copy=False, readonly=True,
        help="Part of the posted value booked against the interim (or expense) counterpart, i.e. "
             "what the bill / invoice of the move clears. Manual valuation adjustments are "
             "booked against the stock variation account and are not included.")
    account_valuation_entry_count = fields.Integer(compute='_compute_account_valuation_entry_count')

    @api.depends('account_valuation_move_ids')
    def _compute_account_valuation_entry_count(self):
        for move in self:
            move.account_valuation_entry_count = len(move.account_valuation_move_ids)

    # -------------------------------------------------------------------------
    # Which moves are posted
    # -------------------------------------------------------------------------

    def _is_location_valued_move(self):
        """Moves handled natively by Odoo 19 (location with its own valuation account)."""
        self.ensure_one()
        return bool(self.location_id.valuation_account_id or self.location_dest_id.valuation_account_id)

    def _use_stock_move_accounting(self):
        """True when this module must post an entry Odoo 19 no longer creates."""
        self.ensure_one()
        return (
            self.company_id.stock_move_accounting
            and self.product_id.is_storable
            and self.product_id.with_company(self.company_id).valuation == 'real_time'
            and self.is_valued
            and not self._is_location_valued_move()
            and not self.product_uom.is_zero(self.quantity)
        )

    def _should_create_account_move(self):
        if self.account_valuation_tracked:
            # The initial entry of this move is already posted: value changes are
            # booked by `_sync_account_valuation`, never by a second initial entry.
            return False
        return super()._should_create_account_move() or self._use_stock_move_accounting()

    # -------------------------------------------------------------------------
    # Amounts and accounts
    # -------------------------------------------------------------------------

    def _get_aml_value(self):
        self.ensure_one()
        if self.company_id.stock_move_accounting:
            return self._get_account_valuation_target(initial=True)
        return super()._get_aml_value()

    def _get_cogs_price_unit(self, quantity=0):
        """Invoice COGS at the value actually credited from stock by the deliveries,
        so that the Stock Interim (Delivered) account is cleared (all cost methods)."""
        product = self.product_id
        if len(product) == 1 and len(self.company_id) == 1 and self.company_id.stock_move_accounting:
            product = product.with_company(self.company_id)
            if product.valuation == 'real_time' and all(m.account_valuation_tracked for m in self):
                total_qty = sum(m._get_valued_qty() * (-1 if m.is_in else 1) for m in self)
                if not product.uom_id.is_zero(total_qty):
                    total_value = sum(m.account_valuation_interim * (-1 if m.is_in else 1) for m in self)
                    return total_value / total_qty
        return super()._get_cogs_price_unit(quantity=quantity)

    def _get_account_valuation_target(self, initial=False):
        """Amount that should currently be booked in stock for this move.

        * Incoming, standard cost: valued at the standard price of the receipt
          (the bill difference goes to the price difference account) and then
          frozen; only a quantity correction changes it.
        * Incoming, FIFO / AVCO: the move value without landed costs, because
          validated landed costs already post their own stock entry.
        * Outgoing: the move value when it is done, then frozen; only a quantity
          correction changes it. Odoo never uses the stored value of a done
          outgoing move to value the inventory (FIFO stack, AVCO replay and
          standard price ignore it), and it re-runs FIFO / the current cost when
          `_set_value` is called again on a done delivery (manual adjustment,
          purchase price edit): booking that would desynchronise the stock
          account from the inventory valuation.
        """
        self.ensure_one()
        move = self.with_company(self.company_id)
        currency = self.company_currency_id
        valued_qty = move._get_valued_qty()
        frozen = not initial and self.account_valuation_qty
        if self.is_in:
            if move.product_id.cost_method == 'standard':
                if frozen:
                    return currency.round(self.account_valuation_posted * valued_qty / self.account_valuation_qty)
                return currency.round(move._get_value_from_std_price(valued_qty)['value'])
            return currency.round(move.sudo()._get_value_data(add_extra_value=False)['value'])
        if frozen:
            return currency.round(self.account_valuation_posted * valued_qty / self.account_valuation_qty)
        return currency.round(self.value)

    def _get_interim_counterpart_account(self):
        """Counterpart account of a move crossing the company boundary."""
        self.ensure_one()
        product = self.product_id.with_company(self.company_id)
        accounts = product.product_tmpl_id._get_product_accounts()
        if self.is_in:
            usage = self.location_id.usage
            if usage == 'customer':
                key = 'stock_output'      # customer return
            elif usage in ('inventory', 'production'):
                key = 'expense'
            else:
                key = 'stock_input'       # supplier, inter-company transit
        else:
            usage = self.location_dest_id.usage
            if usage == 'supplier':
                key = 'stock_input'       # return to vendor
            elif usage in ('inventory', 'production'):
                key = 'expense'
            else:
                key = 'stock_output'      # customer, inter-company transit
        account = accounts.get(key)
        if not account:
            labels = {
                'stock_input': _('Stock Interim (Received)'),
                'stock_output': _('Stock Interim (Delivered)'),
                'expense': _('Expense'),
            }
            raise UserError(_(
                "No %(account)s account found for product '%(product)s'.\n"
                "Set it on the product category '%(category)s' or in "
                "Inventory > Configuration > Settings > Valuation.",
                account=labels[key], product=product.display_name, category=product.categ_id.display_name))
        return account

    def _get_valuation_accounts(self):
        """(debit, credit) accounts of the entry for a positive value."""
        self.ensure_one()
        product = self.product_id.with_company(self.company_id)
        stock_account = product._get_product_accounts()['stock_valuation']
        if not stock_account:
            raise UserError(_(
                "No stock valuation account found for product '%(product)s' (category '%(category)s').",
                product=product.display_name, category=product.categ_id.display_name))
        if self.location_id.valuation_account_id:
            return stock_account, self.location_id.valuation_account_id
        if self.location_dest_id.valuation_account_id:
            return self.location_dest_id.valuation_account_id, stock_account
        counterpart = self._get_interim_counterpart_account()
        if counterpart == stock_account:
            raise UserError(_(
                "The stock valuation account of product '%(product)s' is also its interim / expense "
                "counterpart (%(account)s). Use distinct accounts on the category '%(category)s'.",
                product=product.display_name, account=stock_account.display_name,
                category=product.categ_id.display_name))
        if self.is_in:
            return stock_account, counterpart
        return counterpart, stock_account

    def _get_manual_adjustment_account(self):
        """Counterpart of a manual valuation adjustment (Adjust Valuation): a
        revaluation of the inventory, not a supplier / customer document."""
        self.ensure_one()
        product = self.product_id.with_company(self.company_id)
        accounts = product.product_tmpl_id._get_product_accounts()
        account = accounts['stock_variation'] or accounts['expense']
        if not account:
            raise UserError(_(
                "Set a stock variation account on the stock valuation account of the category "
                "'%(category)s' (or an expense account) to adjust the valuation of '%(product)s'.",
                category=product.categ_id.display_name, product=product.display_name))
        return account

    def _prepare_valuation_aml_vals(self, amount, label=None, role='adjustment', counterpart=None):
        self.ensure_one()
        if self.company_currency_id.is_zero(amount):
            return []
        debit_acc, credit_acc = self._get_valuation_accounts()
        if counterpart:
            stock_acc = debit_acc if self.is_in else credit_acc
            debit_acc, credit_acc = (stock_acc, counterpart) if self.is_in else (counterpart, stock_acc)
        if amount < 0:
            debit_acc, credit_acc, amount = credit_acc, debit_acc, -amount
        name = '%s - %s' % (self.reference or self.picking_id.name or '', self.product_id.name)
        if label:
            name = '%s (%s)' % (name, label)
        common = {
            'name': name,
            'product_id': self.product_id.id,
            'quantity': self._get_valued_qty(),
            'product_uom_id': self.product_id.uom_id.id,
            'stock_valuation_move_id': self.id,
            'stock_valuation_role': role,
        }
        return [
            {**common, 'account_id': credit_acc.id, 'debit': 0, 'credit': amount},
            {**common, 'account_id': debit_acc.id, 'debit': amount, 'credit': 0},
        ]

    def _prepare_consumed_price_diff_aml_vals(self, amount):
        """Repricing of a receipt for the quantity already consumed:
        interim counterpart vs price difference / expense account."""
        self.ensure_one()
        product = self.product_id.with_company(self.company_id)
        accounts = product.product_tmpl_id._get_product_accounts()
        expense = product.categ_id.property_price_difference_account_id or accounts['expense']
        _stock, counterpart = self._get_valuation_accounts()
        if not expense:
            raise UserError(_("No expense account found for product '%s'.", product.display_name))
        if amount < 0:
            expense, counterpart, amount = counterpart, expense, -amount
        name = '%s - %s (%s)' % (self.reference or '', self.product_id.name, _('price difference on consumed quantity'))
        common = {
            'name': name,
            'product_id': self.product_id.id,
            'stock_valuation_move_id': self.id,
            'stock_valuation_role': 'adjustment',
        }
        return [
            {**common, 'account_id': counterpart.id, 'debit': 0, 'credit': amount},
            {**common, 'account_id': expense.id, 'debit': amount, 'credit': 0},
        ]

    def _get_account_move_line_vals(self):
        if not self._use_stock_move_accounting():
            vals_list = super()._get_account_move_line_vals()
            if self.company_id.stock_move_accounting:
                # Odoo's own entry (location with a valuation account): same traceability
                for vals in vals_list:
                    vals.update(stock_valuation_move_id=self.id, stock_valuation_role='initial')
            return vals_list
        return self._prepare_valuation_aml_vals(self._get_aml_value(), role='initial')

    # -------------------------------------------------------------------------
    # Posting
    # -------------------------------------------------------------------------

    def _stock_move_accounting_lock(self):
        """Row-lock the moves before reading what is already booked for them.

        Odoo runs in REPEATABLE READ: if a concurrent transaction has booked (or is
        booking) the same moves, the lock raises a serialization failure and the
        request is retried from scratch instead of posting a second entry."""
        if self.ids:
            self.env.cr.execute(SQL(
                "SELECT id FROM stock_move WHERE id IN %s ORDER BY id FOR UPDATE", tuple(self.ids)))

    def _create_account_move(self):
        candidates = self.filtered(lambda m: m.company_id.stock_move_accounting and not m.account_valuation_tracked)
        candidates._stock_move_accounting_lock()
        to_track = candidates.filtered(lambda m: m._should_create_account_move())
        own = to_track.filtered(lambda m: m._use_stock_move_accounting())
        # Odoo keeps posting its own entries (locations with a valuation account)
        account_move = super(StockMove, self - own)._create_account_move()
        account_move |= own._stock_move_accounting_post_initial_entries()
        for move in to_track:
            value = move._get_aml_value()
            vals = {
                'account_valuation_tracked': True,
                'account_valuation_posted': value,
                'account_valuation_interim': value,
                'account_valuation_qty': move._get_valued_qty(),
            }
            if move.account_move_id:
                vals['account_valuation_move_ids'] = [fields.Command.link(move.account_move_id.id)]
            # technical bookkeeping: also done for stock users without accounting rights
            move.sudo().write(vals)
        if account_move:
            to_track.sudo()._stock_move_accounting_reconcile_invoices()
        return account_move

    def _stock_move_accounting_post_initial_entries(self):
        """Initial valuation entry of the moves, one per company (Odoo's
        `_create_account_move` assumes a single company and a reference on every move)."""
        entries = self.env['account.move']
        for company, moves in self.grouped('company_id').items():
            aml_vals_list = []
            valued = self.env['stock.move']
            for move in moves:
                vals_list = move._get_account_move_line_vals()
                if vals_list:
                    aml_vals_list += vals_list
                    valued |= move
            if not aml_vals_list:
                continue
            refs = ', '.join(sorted({ref for ref in valued.mapped('reference') if ref}))
            if len(refs) > 43:
                refs = refs[:40] + '...'
            entry = self.env['account.move'].sudo().create({
                'ref': refs or _('Stock valuation'),
                'partner_id': valued._get_partner_id_for_valuation_lines(),
                'journal_id': company.account_stock_journal_id.id,
                'company_id': company.id,
                'move_type': 'entry',
                'line_ids': [fields.Command.create(vals) for vals in aml_vals_list],
                'date': self.env.context.get('force_period_date') or fields.Date.context_today(self),
            })
            valued.sudo().account_move_id = entry
            entry._post()
            entries |= entry
        return entries

    def _set_value(self, correction_quantity=None):
        tracked = self.filtered(lambda m: m.state == 'done' and m.account_valuation_tracked)
        if not tracked:
            return super()._set_value(correction_quantity=correction_quantity)
        # Inventory value of the repriced products before / after, to know which
        # part of a receipt repricing is still in stock and which part is consumed.
        repriced = tracked._get_repriced_products_by_company(exclude=(self - tracked).product_id)
        before = self._get_products_total_value(repriced)
        res = super()._set_value(correction_quantity=correction_quantity)
        after = self._get_products_total_value(repriced, invalidate=True)
        stock_delta = {key: after[key] - before[key] for key in before}
        tracked._sync_account_valuation(stock_delta)
        return res

    def _get_repriced_products_by_company(self, exclude=None):
        """FIFO / AVCO products of incoming moves, minus ``exclude`` (products with a
        not-yet-posted move in the batch: its value change would blur the measure)."""
        res = {}
        for company, moves in self.grouped('company_id').items():
            products = moves.filtered(
                lambda m: m.is_in and m.product_id.with_company(company).cost_method != 'standard').product_id
            if exclude:
                products -= exclude
            if products:
                res[company] = products
        return res

    @api.model
    def _get_products_total_value(self, products_by_company, invalidate=False):
        values = {}
        for company, products in products_by_company.items():
            products = products.with_company(company).with_context(allowed_company_ids=company.ids)
            if invalidate:
                products.invalidate_recordset(['total_value', 'avg_cost'])
            for product in products:
                values[company, product] = product.total_value
        return values

    def _sync_account_valuation(self, stock_delta_by_product=None):
        """Post the difference between the current value of the moves and what
        is booked (bill price, refund, manual adjustment, quantity change).

        For FIFO / AVCO receipts, only the part of the difference that changed the
        inventory value goes to stock; the part of goods already consumed goes to
        the price difference (or expense) account, as in Odoo 18.

        Manual adjustments (context ``stock_move_accounting_manual``) are booked
        against the stock variation account, for the part still in stock only."""
        stock_delta_by_product = stock_delta_by_product or {}
        manual = self.env.context.get('stock_move_accounting_manual')
        self._stock_move_accounting_lock()
        for company, moves in self.grouped('company_id').items():
            moves = moves.with_company(company)
            currency = company.currency_id
            deltas = {}
            for move in moves:
                delta = currency.round(move._get_account_valuation_target() - move.account_valuation_posted)
                if not currency.is_zero(delta):
                    deltas[move] = delta
            if not deltas:
                continue

            # Ratio of the receipt repricing that is still in stock, per product.
            stock_ratio = {}
            for product in moves.product_id:
                key = (company, product)
                if key not in stock_delta_by_product:
                    continue
                total = sum(d for m, d in deltas.items() if m.product_id == product and m.is_in)
                if currency.is_zero(total):
                    continue
                stock_ratio[product] = min(max(stock_delta_by_product[key] / total, 0.0), 1.0)

            aml_vals_by_date = defaultdict(list)
            moves_by_date = defaultdict(lambda: self.env['stock.move'])
            for move, delta in deltas.items():
                date = move._stock_move_accounting_adjustment_date()
                ratio = stock_ratio.get(move.product_id, 1.0) if move.is_in else 1.0
                stock_part = currency.round(delta * ratio)
                if manual:
                    aml_vals_by_date[date] += move._prepare_valuation_aml_vals(
                        stock_part, label=_('manual valuation adjustment'),
                        counterpart=move._get_manual_adjustment_account())
                else:
                    aml_vals_by_date[date] += move._prepare_valuation_aml_vals(stock_part, label=_('valuation adjustment'))
                    consumed = currency.round(delta - stock_part)
                    if not currency.is_zero(consumed):
                        aml_vals_by_date[date] += move._prepare_consumed_price_diff_aml_vals(consumed)
                moves_by_date[date] |= move
            for date, adjusted in moves_by_date.items():
                account_move = self.env['account.move']
                if aml_vals_by_date[date]:
                    account_move = self.env['account.move'].sudo().create({
                        'ref': _('Valuation adjustment: %s', ', '.join(sorted({ref for ref in adjusted.mapped('reference') if ref})))[:64],
                        'partner_id': adjusted._get_partner_id_for_valuation_lines(),
                        'journal_id': company.account_stock_journal_id.id,
                        'move_type': 'entry',
                        'company_id': company.id,
                        'line_ids': [fields.Command.create(vals) for vals in aml_vals_by_date[date]],
                        'date': date,
                    })
                    account_move._post()
                for move in adjusted:
                    vals = {
                        'account_valuation_posted': move.account_valuation_posted + deltas[move],
                        'account_valuation_qty': move._get_valued_qty(),
                    }
                    if not manual:
                        vals['account_valuation_interim'] = move.account_valuation_interim + deltas[move]
                    if account_move:
                        vals['account_valuation_move_ids'] = [fields.Command.link(account_move.id)]
                    move.sudo().write(vals)
                adjusted.sudo()._stock_move_accounting_reconcile_invoices()

    def _stock_move_accounting_adjustment_date(self):
        """Accounting date of a value change of the move: the date of the move.

        Odoo 19 values the stock at any date with the *current* value of the moves
        done up to that date (a bill posted later reprices its receipt from the
        receipt date on). Booking the change at the move date keeps the stock
        account equal to that valuation at every date, so the standard stock
        closing, whatever its date, never books the difference a second time.
        Locked periods are respected: the change then goes to the first unlocked day.
        """
        self.ensure_one()
        if self.env.context.get('force_period_date'):
            return fields.Date.to_date(self.env.context['force_period_date'])
        date = fields.Date.context_today(self, self.date)
        company = self.company_id
        lock_date = company._get_user_fiscal_lock_date(company.account_stock_journal_id)
        if lock_date and date <= lock_date:
            date = lock_date + timedelta(days=1)
        return date

    # -------------------------------------------------------------------------
    # Vendor refunds of returned goods
    # -------------------------------------------------------------------------

    def _stock_move_accounting_refund_ratio(self, aml):
        """Share of a vendor refund line that is a price reduction, as opposed to
        the refund of goods returned to the vendor.

        Odoo nets refunds against bills to value the receipts, so refunding returned
        goods reprices the *remaining* goods of the receipt at the purchase order
        price. The returned goods already left at their own value (return move),
        their refund must not reprice the receipt."""
        self.ensure_one()
        if aml.move_type != 'in_refund' or not self.company_id.stock_move_accounting:
            return 1.0
        po_line = aml.purchase_line_id
        uom = self.product_id.uom_id
        returned = sum(
            m._get_valued_qty() for m in po_line.move_ids
            if m.state == 'done' and m.is_out and m._is_purchase_return())
        if uom.is_zero(returned):
            return 1.0
        refunds = po_line.invoice_lines.filtered(
            lambda l: l.move_type == 'in_refund' and l.parent_state == 'posted').sorted(lambda l: (l.date, l.id))
        for line in refunds:
            qty = line.product_uom_id._compute_quantity(line.quantity, uom)
            covered = min(returned, qty)
            returned -= covered
            if line == aml:
                return (qty - covered) / qty if qty else 1.0
        return 1.0

    def _get_quantity_from_bill(self, aml, quantity):
        return super()._get_quantity_from_bill(aml, quantity) * self._stock_move_accounting_refund_ratio(aml)

    def _get_value_from_bill(self, aml):
        return super()._get_value_from_bill(aml) * self._stock_move_accounting_refund_ratio(aml)

    def _get_partner_id_for_valuation_lines(self):
        partners = self.picking_id.partner_id
        if len(partners) > 1:
            return False
        return super()._get_partner_id_for_valuation_lines()

    def _stock_move_accounting_reconcile_invoices(self):
        """Reconcile the interim lines of the moves with the related bills / invoices."""
        invoices = self.env['account.move']
        for move in self:
            if move.purchase_line_id:
                invoices |= move.purchase_line_id.invoice_lines.move_id
            if move.sale_line_id:
                invoices |= move.sale_line_id.invoice_lines.move_id
        invoices = invoices.filtered(lambda inv: inv.state == 'posted' and inv.is_invoice(include_receipts=True))
        if invoices:
            invoices._stock_move_accounting_reconcile(products=self.product_id)

    # -------------------------------------------------------------------------
    # UI
    # -------------------------------------------------------------------------

    def action_view_account_valuation_entries(self):
        self.ensure_one()
        entries = self.account_valuation_move_ids | self.account_move_id
        action = {
            'type': 'ir.actions.act_window',
            'name': _('Valuation Entries'),
            'res_model': 'account.move',
            'domain': [('id', 'in', entries.ids)],
            'view_mode': 'list,form',
            'views': [(self.env.ref('account.view_move_tree').id, 'list'),
                      (self.env.ref('account.view_move_form').id, 'form')],
            'context': {'create': False},
        }
        if len(entries) == 1:
            action.update({'view_mode': 'form', 'views': [(self.env.ref('account.view_move_form').id, 'form')],
                           'res_id': entries.id})
        return action
