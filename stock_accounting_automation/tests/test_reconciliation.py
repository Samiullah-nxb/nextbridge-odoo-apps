"""Stock account vs inventory valuation: standard closing, lock dates, rounding."""
from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import StockAccountingAutomationCommon


@tagged('post_install', '-at_install')
class TestClosingAndReconciliation(StockAccountingAutomationCommon):
    """The stock account equals the inventory valuation; Odoo's closing has nothing to post."""

    def _busy_product(self):
        """Receipts, bill with a price difference, deliveries, returns, adjustment, scrap."""
        product = self.new_product('fifo')
        po = self.purchase(product, 10, 100)
        self.bill(po, price=105)
        po2 = self.purchase(product, 5, 120)
        so = self.sale(product, 8)
        self.invoice(so, qty=5)
        self.return_picking(so.picking_ids, 1)
        self.return_picking(po2.picking_ids, 2)
        self.inventory(product, 5)
        self.env['stock.scrap'].create({
            'product_id': product.id, 'scrap_qty': 1, 'location_id': self.stock_location.id}).action_validate()
        return product, po, po2, so

    def test_standard_closing_posts_nothing(self):
        product, _po, _po2, _so = self._busy_product()
        self.assertStockReconciled(product)
        for at_date in (None, self.today, self.days(30)):
            self.assertFalse(self.closing_vals(at_date=at_date), f"closing at {at_date}")
        with self.assertRaisesRegex(UserError, 'correctly closed'):
            self.company.action_close_stock_valuation(auto_post=True)

    def test_closing_corrects_external_difference_once(self):
        product = self.new_product('fifo')
        self.bill(self.purchase(product, 10, 100))
        # an entry posted by hand on the stock account, outside of any stock move
        manual = self.env['account.move'].create({
            'journal_id': self.company.account_stock_journal_id.id,
            'line_ids': [
                Command.create({'account_id': self.stock_acc.id, 'debit': 50, 'product_id': product.id}),
                Command.create({'account_id': self.variation_acc.id, 'credit': 50}),
            ],
        })
        manual.action_post()
        vals = self.closing_vals()
        self.assertEqual(sum(v['debit'] - v['credit'] for v in vals), -50)
        self.company.action_close_stock_valuation(auto_post=True)
        self.assertEqual(self.balance(self.stock_acc), 1000)
        self.assertFalse(self.closing_vals(), "a second closing must not post again")
        # later stock moves are still posted once, and the closing stays empty
        self.sale(product, 3)
        self.assertEqual(self.balance(self.stock_acc), 700)
        self.assertFalse(self.closing_vals())

    def test_lock_date_respected(self):
        product = self.new_product('fifo')
        po = self.purchase(product, 10, 100)
        self.company.fiscalyear_lock_date = self.days(-1)
        bill = self.bill(po, price=110, date=self.days(-3))
        self.assertGreater(bill.date, self.company.fiscalyear_lock_date)
        adjustments = po.picking_ids.move_ids.account_valuation_move_ids - po.picking_ids.move_ids.account_move_id
        self.assertTrue(adjustments)
        self.assertTrue(all(d > self.company.fiscalyear_lock_date for d in adjustments.mapped('date')))
        self.assertBalances(product, stock=1100, interim_in=0)

    def test_rounding(self):
        product = self.new_product('average')
        po = self.purchase(product, 3, 33.33)
        self.bill(po, price=33.34)
        self.invoice(self.sale(product, 1))
        self.invoice(self.sale(product, 2))
        self.assertBalances(product, stock=0, interim_in=0, interim_out=0)
        self.assertStockReconciled(product)
