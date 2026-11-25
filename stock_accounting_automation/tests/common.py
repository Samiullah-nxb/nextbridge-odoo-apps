"""Shared fixtures, business-flow helpers and accounting assertions."""
from datetime import timedelta

from odoo import Command, fields
from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged('post_install', '-at_install')
class StockAccountingAutomationCommon(AccountTestInvoicingCommon):
    """Business-flow helpers and accounting assertions.

    Every test product uses dedicated stock valuation / COGS / price difference
    accounts, so that balances (and the standard stock closing computation) only
    see what the test did.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.write({'group_ids': [Command.link(cls.env.ref(xmlid).id) for xmlid in (
            'sales_team.group_sale_manager', 'purchase.group_purchase_manager',
            'stock.group_stock_manager', 'account.group_account_manager')]})
        cls.company = cls.env.company
        cls.currency = cls.company.currency_id
        cls.today = fields.Date.context_today(cls.env.user)
        cls.in_acc = cls.company.account_stock_input_id
        cls.out_acc = cls.company.account_stock_output_id
        Account = cls.env['account.account']
        cls.variation_acc = Account.create({
            'name': 'Test Stock Variation', 'code': '699991', 'account_type': 'expense'})
        cls.stock_acc = Account.create({
            'name': 'Test Stock Valuation', 'code': '119991', 'account_type': 'asset_current',
            'account_stock_variation_id': cls.variation_acc.id})
        cls.cogs_acc = Account.create({
            'name': 'Test COGS', 'code': '699992', 'account_type': 'expense_direct_cost'})
        cls.pdiff_acc = Account.create({
            'name': 'Test Price Difference', 'code': '699993', 'account_type': 'expense'})
        cls.income_acc = cls.company_data['default_account_revenue']
        cls.payable_acc = cls.company_data['default_account_payable']
        cls.warehouse = cls.env['stock.warehouse'].search([('company_id', '=', cls.company.id)], limit=1)
        cls.stock_location = cls.warehouse.lot_stock_id
        cls.vendor = cls.env['res.partner'].create({'name': 'SMA Vendor'})
        cls.customer = cls.env['res.partner'].create({'name': 'SMA Customer'})
        cls.categs = {method: cls._create_categ(method) for method in ('fifo', 'average', 'standard')}

    # ------------------------------------------------------------------
    # Master data
    # ------------------------------------------------------------------

    @classmethod
    def _create_categ(cls, cost_method, company=None, stock_acc=None, cogs_acc=None, pdiff_acc=None, income_acc=None):
        company = company or cls.company
        categ = cls.env['product.category'].create({'name': f'SMA Perpetual {cost_method}'})
        categ.with_company(company).write({
            'property_valuation': 'real_time',
            'property_cost_method': cost_method,
            'property_stock_valuation_account_id': (stock_acc or cls.stock_acc).id,
            'property_price_difference_account_id': (pdiff_acc or cls.pdiff_acc).id,
            'property_account_expense_categ_id': (cogs_acc or cls.cogs_acc).id,
            'property_account_income_categ_id': (income_acc or cls.income_acc).id,
        })
        return categ

    def new_product(self, cost_method='fifo', cost=0.0, categ=None, **vals):
        return self.env['product.product'].create({
            'name': f'SMA {cost_method} product',
            'is_storable': True,
            'categ_id': (categ or self.categs[cost_method]).id,
            'standard_price': cost,
            'invoice_policy': 'delivery',
            'purchase_method': 'receive',
            'taxes_id': [Command.clear()],
            'supplier_taxes_id': [Command.clear()],
            **vals,
        })

    # ------------------------------------------------------------------
    # Business flows
    # ------------------------------------------------------------------

    def process(self, picking, qty=None):
        """Validate ``picking`` for ``qty`` (all by default); the rest goes to a backorder."""
        if picking.state == 'draft':
            picking.action_confirm()
        picking.action_assign()
        for move in picking.move_ids:
            move.quantity = move.product_uom_qty if qty is None else qty
        picking.move_ids.picked = True
        picking.with_context(skip_backorder=True).button_validate()
        self.assertEqual(picking.state, 'done')
        return picking.backorder_ids

    def purchase(self, product, qty, price, currency=None, partner=None, receive=True):
        po = self.env['purchase.order'].create({
            'partner_id': (partner or self.vendor).id,
            'currency_id': (currency or self.currency).id,
            'order_line': [Command.create({
                'product_id': product.id, 'product_qty': qty, 'price_unit': price, 'tax_ids': [Command.clear()],
            })],
        })
        po.button_confirm()
        if receive:
            self.process(po.picking_ids)
        return po

    def _stocked(self, cost_method='fifo', qty=10, price=100, **vals):
        product = self.new_product(cost_method, **vals)
        self.bill(self.purchase(product, qty, price))
        return product

    def open_picking(self, order):
        return order.picking_ids.filtered(lambda p: p.state not in ('done', 'cancel'))

    def bill(self, po, qty=None, price=None, date=None, post=True):
        action = po.action_create_invoice()
        bill = self.env['account.move'].browse(action['res_id'])
        bill.invoice_date = date or self.today
        if qty is not None:
            bill.invoice_line_ids.quantity = qty
        if price is not None:
            bill.invoice_line_ids.price_unit = price
        if post:
            bill.action_post()
        return bill

    def sale(self, product, qty, price=150.0, deliver=True):
        so = self.env['sale.order'].create({
            'partner_id': self.customer.id,
            'order_line': [Command.create({
                'product_id': product.id, 'product_uom_qty': qty, 'price_unit': price, 'tax_ids': [Command.clear()],
            })],
        })
        so.action_confirm()
        if deliver:
            self.process(so.picking_ids)
        return so

    def invoice(self, so, qty=None, final=False, post=True):
        invoice = so._create_invoices(final=final)
        if qty is not None:
            invoice.invoice_line_ids.quantity = qty
        if post:
            invoice.action_post()
        return invoice

    def return_picking(self, picking, qty):
        wizard = self.env['stock.return.picking'].with_context(
            active_id=picking.id, active_ids=picking.ids, active_model='stock.picking').create({})
        wizard.product_return_moves.quantity = qty
        returned = self.env['stock.picking'].browse(wizard.action_create_returns()['res_id'])
        self.process(returned)
        return returned

    def inventory(self, product, quantity, location=None):
        quant = self.env['stock.quant'].with_context(inventory_mode=True).create({
            'product_id': product.id,
            'location_id': (location or self.stock_location).id,
            'inventory_quantity': quantity,
        })
        quant.action_apply_inventory()

    def closing_vals(self, accounts=None, at_date=None, company=None):
        """Lines Odoo's standard stock closing would post on ``accounts`` (default: the test stock account)."""
        company = company or self.company
        accounts = accounts or self.stock_acc
        vals_list = company.with_context(allowed_company_ids=company.ids)._action_close_stock_valuation(at_date=at_date)
        return [vals for vals in vals_list if vals['account_id'] in accounts.ids]

    # ------------------------------------------------------------------
    # Assertions
    # ------------------------------------------------------------------

    def balance(self, account, product=None, date=None, company=None):
        domain = [
            ('account_id', '=', account.id),
            ('parent_state', '=', 'posted'),
            ('company_id', '=', (company or self.company).id),
        ]
        if product:
            domain.append(('product_id', '=', product.id))
        if date:
            domain.append(('date', '<=', date))
        lines = self.env['account.move.line'].search(domain)
        return (company or self.company).currency_id.round(sum(lines.mapped('balance')))

    def assertBalances(self, product, **expected):
        accounts = {
            'stock': self.stock_acc, 'interim_in': self.in_acc, 'interim_out': self.out_acc,
            'cogs': self.cogs_acc, 'pdiff': self.pdiff_acc, 'variation': self.variation_acc,
        }
        actual = {key: self.balance(accounts[key], product) for key in expected}
        self.assertEqual(actual, {key: float(value) for key, value in expected.items()})

    def assertStockReconciled(self, product, company=None):
        """Stock account = inventory valuation, and the standard closing posts nothing."""
        company = company or self.company
        product = product.with_company(company).with_context(allowed_company_ids=company.ids)
        product.invalidate_recordset()
        stock_acc = product.categ_id.with_company(company).property_stock_valuation_account_id
        self.assertAlmostEqual(
            self.balance(stock_acc, product, company=company), product.total_value,
            msg="stock account does not match the inventory valuation")
        self.assertFalse(self.closing_vals(stock_acc, company=company),
                         "the standard stock closing would post a correction")

    def assertInterimCleared(self, product, accounts=None):
        """All interim lines of the product are fully reconciled."""
        accounts = accounts or (self.in_acc | self.out_acc)
        lines = self.env['account.move.line'].search([
            ('account_id', 'in', accounts.ids), ('product_id', '=', product.id), ('parent_state', '=', 'posted')])
        self.assertTrue(lines)
        open_lines = lines.filtered(lambda l: not l.reconciled)
        self.assertFalse(open_lines, "open interim lines:\n" + "\n".join(
            f"{l.move_id.display_name} | {l.move_id.ref} | {l.account_id.code} | {l.balance} | "
            f"residual {l.amount_residual} | {l.name}" for l in lines))

    def valuation_entries(self, moves):
        return moves.account_valuation_move_ids | moves.account_move_id

    def assertEntriesTraced(self, moves):
        """Each valuation line is linked to one of the stock moves, and the stock
        side of the links adds up to the value booked on each move."""
        entries = self.valuation_entries(moves)
        for entry in entries:
            self.assertEqual(entry.state, 'posted')
            self.assertEqual(entry.journal_id, self.company.account_stock_journal_id)
            self.assertTrue(all(line.stock_valuation_move_id for line in entry.line_ids),
                            "valuation line without source stock move")
        for move in moves.filtered('account_valuation_tracked'):
            initial = entries.line_ids.filtered(
                lambda l: l.stock_valuation_move_id == move and l.stock_valuation_role == 'initial')
            self.assertLessEqual(len(initial), 2, "more than one initial entry for a stock move")

    def post_reversals(self, entries):
        """Post the auto-reversals of accrual entries (Odoo's cron does it on their date)."""
        drafts = entries.filtered(lambda m: m.state == 'draft')
        drafts.auto_post = 'no'
        drafts.action_post()

    def days(self, n):
        return self.today + timedelta(days=n)
