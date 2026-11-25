"""Deliveries and customer invoices: COGS on Stock Interim (Delivered)."""
from odoo.tests import tagged

from .common import StockAccountingAutomationCommon


@tagged('post_install', '-at_install')
class TestDeliveries(StockAccountingAutomationCommon):
    """Delivery entry, COGS of the customer invoice, invoice corrections."""

    def cogs_lines(self, invoices):
        return invoices.line_ids.filtered(lambda l: l.display_type == 'cogs')

    def test_delivery_then_invoice(self):
        product = self._stocked()
        so = self.sale(product, 5, price=150)
        move = so.picking_ids.move_ids
        self.assertBalances(product, stock=500, interim_out=500, cogs=0)
        self.assertEqual(move.account_move_id.line_ids.stock_valuation_move_id, move)
        invoice = self.invoice(so)
        self.assertBalances(product, stock=500, interim_out=0, cogs=500)
        self.assertEqual(self.balance(self.income_acc, product), -750)
        cogs = self.cogs_lines(invoice)
        self.assertEqual(sorted(cogs.mapped('account_id').ids), sorted((self.out_acc | self.cogs_acc).ids))
        self.assertEqual(len(self.valuation_entries(move)), 1, "the invoice must not touch the stock entries")
        self.assertInterimCleared(product, self.out_acc)
        self.assertStockReconciled(product)

    def test_partial_deliveries_one_invoice(self):
        product = self._stocked()
        so = self.sale(product, 5, deliver=False)
        backorder = self.process(so.picking_ids, 2)
        self.process(backorder)
        self.assertBalances(product, stock=500, interim_out=500)
        self.invoice(so)
        self.assertBalances(product, stock=500, interim_out=0, cogs=500)
        self.assertInterimCleared(product, self.out_acc)
        self.assertStockReconciled(product)

    def test_one_delivery_multiple_invoices(self):
        product = self._stocked()
        so = self.sale(product, 5)
        self.invoice(so, qty=2)
        self.assertBalances(product, interim_out=300, cogs=200)
        self.invoice(so)
        self.assertBalances(product, interim_out=0, cogs=500)
        self.assertStockReconciled(product)

    def test_partial_delivery_partial_invoice(self):
        product = self._stocked()
        so = self.sale(product, 5, deliver=False)
        backorder = self.process(so.picking_ids, 2)
        self.invoice(so)
        self.assertBalances(product, stock=800, interim_out=0, cogs=200)
        self.process(backorder)
        self.assertBalances(product, stock=500, interim_out=300, cogs=200)
        self.invoice(so)
        self.assertBalances(product, stock=500, interim_out=0, cogs=500)
        self.assertStockReconciled(product)

    def test_fifo_cogs_spans_layers(self):
        product = self.new_product('fifo')
        self.bill(self.purchase(product, 5, 100))
        self.bill(self.purchase(product, 5, 120))
        so = self.sale(product, 7)
        self.assertBalances(product, stock=360, interim_out=740)
        self.invoice(so)
        self.assertBalances(product, interim_out=0, cogs=740)
        self.assertStockReconciled(product)

    def test_avco_cogs_uses_delivered_value(self):
        """Odoo would invoice AVCO COGS at the current average cost; the deliveries
        credited stock at the average of their own date."""
        product = self._stocked('average')
        so = self.sale(product, 10, deliver=False)
        backorder = self.process(so.picking_ids, 5)            # 5 x 100
        self.bill(self.purchase(product, 10, 130))           # avg (500 + 1300) / 15 = 120
        self.process(backorder)                              # 5 x 120
        self.assertBalances(product, interim_out=1100)
        self.invoice(so)
        self.assertBalances(product, interim_out=0, cogs=1100)
        self.assertStockReconciled(product)

    def test_invoice_before_delivery(self):
        product = self._stocked('average', invoice_policy='order')
        so = self.sale(product, 5, deliver=False)
        self.invoice(so)
        self.assertBalances(product, stock=1000, interim_out=-500, cogs=500)
        backorder = self.process(so.picking_ids, 2)
        self.assertBalances(product, stock=800, interim_out=-300, cogs=500)
        self.process(backorder)
        self.assertBalances(product, stock=500, interim_out=0, cogs=500)
        self.assertInterimCleared(product, self.out_acc)
        self.assertStockReconciled(product)

    def test_invoice_reset_and_repost(self):
        product = self._stocked()
        so = self.sale(product, 5)
        invoice = self.invoice(so)
        invoice.button_draft()
        self.assertBalances(product, interim_out=500, cogs=0)
        self.assertFalse(self.cogs_lines(invoice))
        invoice.action_post()
        self.assertBalances(product, interim_out=0, cogs=500)
        self.assertEqual(len(self.cogs_lines(invoice)), 2)
        self.assertInterimCleared(product, self.out_acc)
        self.assertStockReconciled(product)

    def test_invoice_cancelled(self):
        product = self._stocked()
        so = self.sale(product, 5)
        invoice = self.invoice(so)
        invoice.button_draft()
        invoice.button_cancel()
        self.assertBalances(product, stock=500, interim_out=500, cogs=0)
        self.invoice(so)
        self.assertBalances(product, stock=500, interim_out=0, cogs=500)
        self.assertStockReconciled(product)
