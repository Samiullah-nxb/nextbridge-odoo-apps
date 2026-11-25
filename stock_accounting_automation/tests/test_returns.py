"""Customer and vendor returns, credit notes and vendor refunds."""
from odoo.tests import tagged

from .common import StockAccountingAutomationCommon


@tagged('post_install', '-at_install')
class TestReturns(StockAccountingAutomationCommon):
    """Returns are booked when the goods move; credit notes / refunds clear the interim."""

    # customer returns ---------------------------------------------------

    def test_customer_return_before_invoice(self):
        product = self._stocked()
        so = self.sale(product, 10)
        returned = self.return_picking(so.picking_ids, 3)
        self.assertBalances(product, stock=300, interim_out=700)
        self.assertEqual(returned.move_ids.account_move_id.line_ids.stock_valuation_move_id, returned.move_ids)
        self.invoice(so)  # 7 delivered net
        self.assertBalances(product, stock=300, interim_out=0, cogs=700)
        self.assertInterimCleared(product, self.out_acc)
        self.assertStockReconciled(product)

    def test_customer_return_after_invoice_with_credit_note(self):
        for cost_method in ('fifo', 'average', 'standard'):
            with self.subTest(cost_method=cost_method):
                product = self.new_product(cost_method, cost=100)
                self.bill(self.purchase(product, 10, 100))
                so = self.sale(product, 10)
                self.invoice(so)
                self.assertBalances(product, stock=0, interim_out=0, cogs=1000)
                self.return_picking(so.picking_ids, 3)
                self.assertBalances(product, stock=300, interim_out=-300, cogs=1000)
                refund = self.invoice(so, final=True)
                self.assertEqual(refund.move_type, 'out_refund')
                self.assertBalances(product, stock=300, interim_out=0, cogs=700)
                self.assertStockReconciled(product)

    def test_multiple_customer_returns(self):
        product = self._stocked()
        so = self.sale(product, 10)
        delivery = so.picking_ids
        self.return_picking(delivery, 2)
        self.return_picking(delivery, 3)
        self.assertBalances(product, stock=500, interim_out=500)
        self.invoice(so)
        self.assertBalances(product, interim_out=0, cogs=500)
        self.assertStockReconciled(product)

    def test_full_customer_return(self):
        product = self._stocked()
        so = self.sale(product, 5)
        self.return_picking(so.picking_ids, 5)
        self.assertBalances(product, stock=1000, interim_out=0, cogs=0)
        self.assertStockReconciled(product)

    def test_customer_return_fifo_layers(self):
        product = self.new_product('fifo')
        self.bill(self.purchase(product, 5, 100))
        self.bill(self.purchase(product, 5, 120))
        so = self.sale(product, 7)                     # 500 + 240 = 740
        self.return_picking(so.picking_ids, 2)         # at the delivery unit cost: 740 * 2 / 7
        self.assertBalances(product, interim_out=528.57)
        self.invoice(so)
        self.assertBalances(product, interim_out=0, cogs=528.57)
        self.assertStockReconciled(product)

    # vendor returns -----------------------------------------------------

    def test_vendor_return_before_bill(self):
        product = self.new_product('fifo')
        po = self.purchase(product, 10, 100)
        self.return_picking(po.picking_ids, 3)
        self.assertBalances(product, stock=700, interim_in=-700)
        self.bill(po)  # 7 received net
        self.assertBalances(product, stock=700, interim_in=0)
        self.assertInterimCleared(product, self.in_acc)
        self.assertStockReconciled(product)

    def test_vendor_return_after_bill_with_refund(self):
        for cost_method in ('fifo', 'average', 'standard'):
            with self.subTest(cost_method=cost_method):
                product = self.new_product(cost_method, cost=100)
                po = self.purchase(product, 10, 100)
                self.bill(po)
                self.return_picking(po.picking_ids, 3)
                self.assertBalances(product, stock=700, interim_in=300)
                refund = self.bill(po)
                self.assertEqual(refund.move_type, 'in_refund')
                self.assertBalances(product, stock=700, interim_in=0)
                self.assertStockReconciled(product)

    def test_vendor_return_after_bill_at_other_price(self):
        """Bill at 110 instead of 100, then return 3 and get them refunded at 110."""
        product = self.new_product('fifo')
        po = self.purchase(product, 10, 100)
        self.bill(po, price=110)
        self.assertBalances(product, stock=1100, interim_in=0)
        self.return_picking(po.picking_ids, 3)
        self.assertBalances(product, stock=770, interim_in=330)
        refund = self.bill(po, price=110)
        self.assertEqual(refund.move_type, 'in_refund')
        # the refund of the returned goods does not reprice the 7 kept
        self.assertBalances(product, stock=770, interim_in=0, pdiff=0)
        self.assertEqual(po.picking_ids.move_ids.filtered('is_in').value, 1100)
        self.assertInterimCleared(product, self.in_acc)
        self.assertStockReconciled(product)

    def test_multiple_vendor_returns(self):
        product = self.new_product('average')
        po = self.purchase(product, 10, 100)
        receipt = po.picking_ids
        self.return_picking(receipt, 2)
        self.return_picking(receipt, 1)
        self.assertBalances(product, stock=700, interim_in=-700)
        self.bill(po)
        self.assertBalances(product, stock=700, interim_in=0)
        self.assertStockReconciled(product)

    def test_return_not_posted_twice(self):
        product = self._stocked()
        so = self.sale(product, 5)
        returned = self.return_picking(so.picking_ids, 2)
        entries = self.valuation_entries(returned.move_ids)
        returned.move_ids._create_account_move()
        returned.move_ids._set_value()
        self.assertEqual(self.valuation_entries(returned.move_ids), entries)
        self.assertBalances(product, stock=700, interim_out=300)
