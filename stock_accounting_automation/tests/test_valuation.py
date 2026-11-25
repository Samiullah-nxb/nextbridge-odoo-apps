"""Value changes: bill price differences per costing method, manual adjustments,
quantity corrections, cost updates and landed costs.
"""
from odoo import Command
from odoo.tests import tagged

from .common import StockAccountingAutomationCommon


@tagged('post_install', '-at_install')
class TestPriceDifferences(StockAccountingAutomationCommon):
    """Bill price different from the receipt value (FIFO, AVCO, Standard)."""

    def test_price_difference_fifo_in_stock(self):
        product = self.new_product('fifo')
        po = self.purchase(product, 10, 100)
        self.bill(po, price=110)
        self.assertBalances(product, stock=1100, interim_in=0, pdiff=0)
        self.assertStockReconciled(product)

    def test_price_difference_fifo_partly_consumed(self):
        product = self.new_product('fifo')
        po = self.purchase(product, 10, 100)
        so = self.sale(product, 4)
        self.invoice(so)
        self.bill(po, price=110)
        # 6 in stock: +60 on stock; 4 sold: +40 on price difference
        self.assertBalances(product, stock=660, interim_in=0, pdiff=40, cogs=400, interim_out=0)
        self.assertStockReconciled(product)

    def test_price_difference_avco_partly_consumed(self):
        product = self.new_product('average')
        po = self.purchase(product, 10, 100)
        so = self.sale(product, 4)
        self.invoice(so)
        self.bill(po, price=110)
        self.assertBalances(product, stock=660, interim_in=0, pdiff=40, cogs=400, interim_out=0)
        self.assertStockReconciled(product)

    def test_price_difference_avco_fully_consumed(self):
        product = self.new_product('average')
        po = self.purchase(product, 10, 100)
        self.invoice(self.sale(product, 10))
        self.bill(po, price=110)
        self.assertBalances(product, stock=0, interim_in=0, pdiff=100)
        self.assertStockReconciled(product)

    def test_price_difference_standard(self):
        product = self.new_product('standard', cost=100)
        po = self.purchase(product, 10, 100)
        self.bill(po, price=110)
        self.assertBalances(product, stock=1000, interim_in=0, pdiff=100)
        self.assertStockReconciled(product)

    def test_price_difference_standard_cost_changed_before_bill(self):
        product = self.new_product('standard', cost=100)
        po = self.purchase(product, 10, 100)
        product.standard_price = 90  # revaluation of the 10 on hand
        self.assertBalances(product, stock=900, variation=100)
        self.bill(po, price=100)
        # the bill matches the receipt value: no price difference, interim cleared
        self.assertBalances(product, stock=900, interim_in=0, pdiff=0)
        self.assertStockReconciled(product)


@tagged('post_install', '-at_install')
class TestValuationChanges(StockAccountingAutomationCommon):
    """Manual valuation, quantity corrections, cost updates, landed costs."""

    def test_manual_valuation_adjustment_on_receipt(self):
        product = self.new_product('fifo')
        po = self.purchase(product, 10, 100)
        move = po.picking_ids.move_ids
        move.value_manual = 1200
        # a revaluation of the inventory, not something the vendor owes us
        self.assertBalances(product, stock=1200, interim_in=-1000, variation=-200)
        self.bill(po)
        self.assertBalances(product, stock=1200, interim_in=0)
        self.assertStockReconciled(product)

    def test_done_delivery_revalued_by_odoo_is_not_booked(self):
        """Odoo re-runs FIFO when `_set_value` is called on a done delivery (e.g. Adjust
        Valuation), with the *current* FIFO stack. The inventory valuation never uses
        the value of done deliveries, so booking it would desynchronise the stock account."""
        product = self.new_product('fifo')
        self.bill(self.purchase(product, 5, 100))
        so = self.sale(product, 5)
        delivery = so.picking_ids.move_ids
        self.bill(self.purchase(product, 10, 120))
        delivery.value_manual = 450   # core recomputes the delivery from the 120 layer
        self.assertBalances(product, stock=1200, interim_out=500, variation=0)
        self.assertEqual(len(self.valuation_entries(delivery)), 1)
        self.assertStockReconciled(product)
        self.invoice(so)
        self.assertBalances(product, interim_out=0, cogs=500)
        self.assertStockReconciled(product)

    def test_quantity_correction_on_done_delivery(self):
        product = self.new_product('fifo')
        self.bill(self.purchase(product, 10, 100))
        so = self.sale(product, 4)
        so.picking_ids.move_ids.move_line_ids.quantity = 5
        self.assertBalances(product, stock=500, interim_out=500)
        self.invoice(so)
        self.assertBalances(product, stock=500, interim_out=0, cogs=500)
        self.assertStockReconciled(product)

    def test_quantity_correction_on_done_receipt(self):
        product = self.new_product('fifo')
        po = self.purchase(product, 10, 100)
        move = po.picking_ids.move_ids
        move.move_line_ids.quantity = 12
        self.assertBalances(product, stock=1200, interim_in=-1200)
        self.bill(po)
        self.assertBalances(product, stock=1200, interim_in=0)
        self.assertStockReconciled(product)

    def test_standard_price_change_revalues_on_hand(self):
        for cost_method in ('standard', 'average'):
            with self.subTest(cost_method=cost_method):
                product = self.new_product(cost_method, cost=10)
                self.inventory(product, 10)
                product.standard_price = 12
                self.assertBalances(product, stock=120, variation=-20)
                self.assertStockReconciled(product)
                self.inventory(product, 4)
                self.assertBalances(product, stock=48)
                self.assertStockReconciled(product)

    def test_landed_costs(self):
        if 'stock.landed.cost' not in self.env:
            self.skipTest('stock_landed_costs is not installed')
        for cost_method in ('fifo', 'average'):
            with self.subTest(cost_method=cost_method):
                product = self.new_product(cost_method)
                po = self.purchase(product, 10, 100)
                self.bill(po)
                self.invoice(self.sale(product, 4))
                lc_product = self.env['product.product'].create({
                    'name': 'Freight', 'type': 'service', 'landed_cost_ok': True,
                    'property_account_expense_id': self.pdiff_acc.id})
                lc = self.env['stock.landed.cost'].create({
                    'picking_ids': [Command.set(po.picking_ids.ids)],
                    'cost_lines': [Command.create({
                        'product_id': lc_product.id, 'price_unit': 100, 'split_method': 'equal',
                        'account_id': self.pdiff_acc.id})],
                })
                lc.compute_landed_cost()
                lc.button_validate()
                # 6 still in stock carry 60 of the landed cost; no second entry from this module
                self.assertBalances(product, stock=660, interim_in=0, interim_out=0)
                self.assertEqual(len(po.picking_ids.move_ids.account_valuation_move_ids), 1)
                self.assertStockReconciled(product)
                self.invoice(self.sale(product, 6))
                self.assertBalances(product, stock=0, interim_out=0)
                self.assertStockReconciled(product)
