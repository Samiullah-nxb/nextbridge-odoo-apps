"""Receipts and vendor bills: posting, partial flows, bill corrections, currency and dating."""
from datetime import timedelta

from odoo.tests import tagged

from .common import StockAccountingAutomationCommon


@tagged('post_install', '-at_install')
class TestReceipts(StockAccountingAutomationCommon):
    """Receipt entry, vendor bill on Stock Interim (Received), automatic matching."""

    def test_receipt_then_bill_books_stock_once(self):
        product = self.new_product('fifo')
        po = self.purchase(product, 10, 100)
        move = po.picking_ids.move_ids
        self.assertBalances(product, stock=1000, interim_in=-1000)
        entry = move.account_move_id
        self.assertEqual(move.account_valuation_move_ids, entry)
        self.assertEqual(entry.line_ids.mapped('stock_valuation_role'), ['initial', 'initial'])
        self.assertEqual(entry.line_ids.stock_valuation_move_id, move)
        stock_line = entry.line_ids.filtered(lambda l: l.account_id == self.stock_acc)
        self.assertRecordValues(stock_line, [{'debit': 1000.0, 'credit': 0.0, 'quantity': 10.0}])

        bill = self.bill(po)
        self.assertEqual(bill.invoice_line_ids.account_id, self.in_acc)
        self.assertBalances(product, stock=1000, interim_in=0)
        self.assertEqual(self.balance(self.payable_acc), -1000)
        # no adjustment: the bill matches the receipt
        self.assertEqual(move.account_valuation_move_ids, entry)
        self.assertInterimCleared(product, self.in_acc)
        self.assertStockReconciled(product)
        self.assertEntriesTraced(move)

    def test_partial_receipts_one_bill(self):
        product = self.new_product('average')
        po = self.purchase(product, 10, 100, receive=False)
        backorder = self.process(po.picking_ids, 4)
        self.process(backorder, 6)
        moves = po.picking_ids.move_ids
        self.assertEqual(len(self.valuation_entries(moves)), 2)
        self.assertBalances(product, stock=1000, interim_in=-1000)
        self.bill(po)
        self.assertBalances(product, stock=1000, interim_in=0)
        self.assertEqual(len(self.valuation_entries(moves)), 2, "the bill must not add stock entries")
        self.assertInterimCleared(product, self.in_acc)
        self.assertStockReconciled(product)

    def test_partial_receipt_partial_bills(self):
        product = self.new_product('fifo')
        po = self.purchase(product, 10, 100, receive=False)
        backorder = self.process(po.picking_ids, 4)
        self.bill(po)  # bills the 4 received
        self.assertBalances(product, stock=400, interim_in=0)
        self.process(backorder)
        self.assertBalances(product, stock=1000, interim_in=-600)
        self.bill(po)
        self.assertBalances(product, stock=1000, interim_in=0)
        self.assertStockReconciled(product)

    def test_multiple_bills_one_receipt_different_prices(self):
        product = self.new_product('fifo')
        po = self.purchase(product, 10, 100)
        self.bill(po, qty=4, price=110)
        self.assertBalances(product, stock=1040, interim_in=-600)
        self.bill(po, qty=6, price=105)
        self.assertBalances(product, stock=1070, interim_in=0)
        self.assertInterimCleared(product, self.in_acc)
        self.assertStockReconciled(product)
        self.assertEntriesTraced(po.picking_ids.move_ids)

    def test_bill_before_receipt(self):
        product = self.new_product('fifo', purchase_method='purchase')
        po = self.purchase(product, 10, 100, receive=False)
        self.bill(po, price=110)
        self.assertBalances(product, stock=0, interim_in=1100)
        backorder = self.process(po.picking_ids, 4)
        self.assertBalances(product, stock=440, interim_in=660)
        self.process(backorder)
        self.assertBalances(product, stock=1100, interim_in=0)
        moves = po.picking_ids.move_ids
        self.assertEqual(len(self.valuation_entries(moves)), 2, "no adjustment: receipts valued at the bill price")
        self.assertInterimCleared(product, self.in_acc)
        self.assertStockReconciled(product)

    def test_bill_reset_to_draft_and_repost(self):
        product = self.new_product('fifo')
        po = self.purchase(product, 10, 100)
        bill = self.bill(po, price=110)
        self.assertBalances(product, stock=1100, interim_in=0)

        bill.button_draft()
        self.assertBalances(product, stock=1000, interim_in=-1000)
        self.assertStockReconciled(product)

        bill.invoice_line_ids.price_unit = 105
        bill.action_post()
        self.assertBalances(product, stock=1050, interim_in=0)
        self.assertInterimCleared(product, self.in_acc)
        self.assertStockReconciled(product)

        # posting the same bill again changes nothing
        entries = self.valuation_entries(po.picking_ids.move_ids)
        bill.line_ids._get_stock_moves()._set_value()
        self.assertEqual(self.valuation_entries(po.picking_ids.move_ids), entries)

    def test_bill_cancelled(self):
        product = self.new_product('average')
        po = self.purchase(product, 10, 100)
        bill = self.bill(po, price=120)
        self.assertBalances(product, stock=1200, interim_in=0)
        bill.button_draft()
        bill.button_cancel()
        self.assertBalances(product, stock=1000, interim_in=-1000)
        self.assertStockReconciled(product)

    def test_bill_reversed_by_credit_note(self):
        product = self.new_product('fifo')
        po = self.purchase(product, 10, 100)
        bill = self.bill(po, price=110)
        bill._reverse_moves([{'invoice_date': self.today}], cancel=True)
        self.assertBalances(product, stock=1000, interim_in=-1000)
        self.assertStockReconciled(product)
        # re-bill at the PO price
        self.bill(po)
        self.assertBalances(product, stock=1000, interim_in=0)
        self.assertStockReconciled(product)

    def test_foreign_currency_bill_at_another_rate(self):
        eur = self.setup_other_currency('EUR', rates=[
            (self.days(-400), 2.0), (self.days(-1), 2.5), (self.today, 2.0)])
        product = self.new_product('fifo')
        po = self.purchase(product, 10, 200, currency=eur)  # 2000 EUR at 2.0 = 1000
        self.assertBalances(product, stock=1000, interim_in=-1000)
        self.bill(po, date=self.days(-1))  # 2000 EUR at 2.5 = 800
        self.assertBalances(product, stock=800, interim_in=0)
        self.assertStockReconciled(product)

    def test_adjustment_dated_on_receipt_date(self):
        """Odoo values the receipt at the bill price from the receipt date on: the
        adjustment is booked on the receipt date, so a closing at any date in between
        finds nothing to (re)book."""
        product = self.new_product('fifo')
        po = self.purchase(product, 10, 100)
        move = po.picking_ids.move_ids
        self.bill(po, price=110, date=self.days(5))
        adjustment = move.account_valuation_move_ids - move.account_move_id
        self.assertEqual(adjustment.date, self.today)
        for at_date in (self.days(-1), self.today, self.days(5)):
            self.assertFalse(self.closing_vals(at_date=at_date), f"closing at {at_date}")
        self.assertStockReconciled(product)

    def test_adjustment_in_locked_period_goes_to_first_open_day(self):
        product = self.new_product('fifo')
        po = self.purchase(product, 10, 100)
        move = po.picking_ids.move_ids
        move.date = move.date - timedelta(days=10)
        self.company.fiscalyear_lock_date = self.days(-5)
        self.bill(po, price=110)
        adjustment = move.account_valuation_move_ids - move.account_move_id
        self.assertEqual(adjustment.date, self.days(-4))
        self.assertBalances(product, stock=1100, interim_in=0)

    def test_bill_dated_before_receipt_adjusts_at_receipt_date(self):
        product = self.new_product('fifo', purchase_method='purchase')
        po = self.purchase(product, 10, 100, receive=False)
        self.bill(po, price=110, date=self.days(-3))
        self.process(po.picking_ids)
        entries = self.valuation_entries(po.picking_ids.move_ids)
        self.assertEqual(entries.mapped('date'), [self.today])
        self.assertFalse(self.closing_vals(at_date=self.days(-1)))
        self.assertStockReconciled(product)
