"""Feature switch, access rights and moves outside pickings."""
from odoo import Command
from odoo.tests import new_test_user, tagged

from .common import StockAccountingAutomationCommon


@tagged('post_install', '-at_install')
class TestConfiguration(StockAccountingAutomationCommon):
    """Behaviour that depends on settings and on the user."""

    def test_stock_user_without_accounting_rights(self):
        """Warehouse users validate transfers; the entries are posted for them."""
        stock_user = new_test_user(self.env, login='sma_stock_user', groups='stock.group_stock_user',
                                   company_id=self.company.id, company_ids=[Command.set(self.company.ids)])
        product = self.new_product('fifo')
        po = self.purchase(product, 10, 100, receive=False)
        receipt = po.picking_ids.with_user(stock_user)
        receipt.action_assign()
        receipt.move_ids.quantity = 10
        receipt.move_ids.picked = True
        receipt.button_validate()
        so = self.sale(product, 4, deliver=False)
        delivery = so.picking_ids.with_user(stock_user)
        delivery.action_assign()
        delivery.move_ids.quantity = 4
        delivery.move_ids.picked = True
        delivery.button_validate()
        self.assertBalances(product, stock=600, interim_in=-1000, interim_out=400)
        delivery.move_ids.move_line_ids.quantity = 5   # correction by the warehouse user
        self.assertBalances(product, stock=500, interim_out=500)
        self.assertStockReconciled(product)

    def test_move_without_reference(self):
        """Moves created outside a picking have no reference; they are posted all the same."""
        product = self.new_product('fifo', cost=7)
        supplier = self.env.ref('stock.stock_location_suppliers')
        move = self.env['stock.move'].create({
            'product_id': product.id, 'product_uom_qty': 3,
            'location_id': supplier.id, 'location_dest_id': self.stock_location.id,
        })
        move._action_confirm()
        move.quantity = 3
        move.picked = True
        move._action_done()
        self.assertTrue(move.account_move_id.ref)
        self.assertBalances(product, stock=21, interim_in=-21)
        self.assertStockReconciled(product)

    def test_feature_disabled_keeps_standard_behaviour(self):
        self.company.stock_move_accounting = False
        product = self.new_product('average')
        po = self.purchase(product, 1, 10)
        self.assertFalse(po.picking_ids.move_ids.account_move_id)
        bill = self.bill(po)
        self.assertEqual(bill.invoice_line_ids.account_id, self.stock_acc)
