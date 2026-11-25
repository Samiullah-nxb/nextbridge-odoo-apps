"""Accrued expense ("Bill to Receive" / GRNI) and accrued revenue ("Invoices to Issue") entries."""
from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import StockAccountingAutomationCommon


@tagged('post_install', '-at_install')
class TestAccruals(StockAccountingAutomationCommon):
    """Accruals never duplicate what the interim accounts already carry."""

    def _accrual_wizard(self, orders):
        accrual_acc = self.env['account.account'].create({
            'name': 'Accrued', 'code': '219991', 'account_type': 'liability_current'})
        return self.env['account.accrued.orders.wizard'].with_context(
            active_model=orders._name, active_ids=orders.ids,
        ).create({'account_id': accrual_acc.id, 'date': self.today}), accrual_acc

    def test_accrual_received_not_billed_not_duplicated(self):
        product = self.new_product('fifo')
        po = self.purchase(product, 10, 100)
        self.assertBalances(product, stock=1000, interim_in=-1000)
        wizard, _accrual_acc = self._accrual_wizard(po)
        with self.assertRaisesRegex(UserError, 'nothing to accrue'):
            wizard.create_entries()
        # GRNI is carried by Stock Interim (Received) only, cleared once by the bill
        self.bill(po)
        self.assertBalances(product, stock=1000, interim_in=0)

    def test_accrual_mixed_order_keeps_other_lines(self):
        product = self.new_product('fifo')
        consumable = self.env['product.product'].create({
            'name': 'SMA consumable', 'type': 'consu', 'is_storable': False, 'purchase_method': 'receive',
            'property_account_expense_id': self.cogs_acc.id, 'supplier_taxes_id': [Command.clear()]})
        po = self.env['purchase.order'].create({
            'partner_id': self.vendor.id,
            'order_line': [
                Command.create({'product_id': product.id, 'product_qty': 10, 'price_unit': 100, 'tax_ids': []}),
                Command.create({'product_id': consumable.id, 'product_qty': 2, 'price_unit': 50, 'tax_ids': []}),
            ],
        })
        po.button_confirm()
        self.process(po.picking_ids)
        wizard, accrual_acc = self._accrual_wizard(po)
        entries = self.env['account.move'].search(wizard.create_entries()['domain'])
        accrual = entries.filtered(lambda m: m.date == self.today)
        self.assertEqual(self.balance(accrual_acc, date=self.today), -100)
        self.assertFalse(accrual.line_ids.filtered(lambda l: l.product_id == product))
        self.assertBalances(product, stock=1000, interim_in=-1000)

    def test_accrual_standard_price_difference(self):
        product = self.new_product('standard', cost=10)
        po = self.purchase(product, 5, 12)
        self.assertBalances(product, stock=50, interim_in=-50)
        wizard, _accrual_acc = self._accrual_wizard(po)
        entries = self.env['account.move'].search(wizard.create_entries()['domain'])
        accrual = entries.filtered(lambda m: m.date == self.today)
        lines = accrual.line_ids.filtered(lambda l: l.product_id == product)
        self.assertEqual(sorted(lines.mapped(lambda l: (l.account_id.id, l.balance))),
                         sorted([(self.pdiff_acc.id, 10.0), (self.in_acc.id, -10.0)]))
        # at period end, GRNI = liability at purchase price
        self.assertEqual(self.balance(self.in_acc, product, date=self.today), -60)
        self.post_reversals(entries)
        self.bill(po)
        self.assertEqual(self.balance(self.in_acc, product), 0)
        self.assertEqual(self.balance(self.pdiff_acc, product), 10)

    def _accrual(self, so):
        accrual_acc = self.env['account.account'].create({
            'name': 'Accrued revenue', 'code': '129991', 'account_type': 'asset_current'})
        wizard = self.env['account.accrued.orders.wizard'].with_context(
            active_model='sale.order', active_ids=so.ids,
        ).create({'account_id': accrual_acc.id, 'date': self.today})
        entries = self.env['account.move'].search(wizard.create_entries()['domain'])
        self.accrual_entries = entries
        return entries.filtered(lambda m: m.date == self.today), accrual_acc

    def test_accrual_delivered_not_invoiced(self):
        product = self._stocked()
        so = self.sale(product, 5, price=150)
        self.assertBalances(product, stock=500, interim_out=500)
        accrual, accrual_acc = self._accrual(so)
        self.assertEqual(self.balance(accrual_acc, date=self.today), 750)
        # cost accrued from the interim balance, nothing on the stock / variation accounts
        self.assertFalse(accrual.line_ids.filtered(lambda l: l.account_id in (self.stock_acc | self.variation_acc)))
        self.assertEqual(self.balance(self.out_acc, product, date=self.today), 0)
        self.assertEqual(self.balance(self.cogs_acc, product, date=self.today), 500)
        # after the reversal and the real invoice: COGS once, interim cleared
        self.post_reversals(self.accrual_entries)
        self.invoice(so)
        self.assertBalances(product, stock=500, interim_out=0, cogs=500)
        self.assertStockReconciled(product)

    def test_accrual_invoiced_not_delivered(self):
        product = self._stocked('average', invoice_policy='order')
        so = self.sale(product, 5, deliver=False)
        self.invoice(so)
        self.assertBalances(product, interim_out=-500, cogs=500)
        accrual, _accrual_acc = self._accrual(so)
        self.assertEqual(self.balance(self.out_acc, product, date=self.today), 0)
        self.assertEqual(self.balance(self.cogs_acc, product, date=self.today), 0)
        self.post_reversals(self.accrual_entries)
        self.process(so.picking_ids)
        self.assertBalances(product, interim_out=0, cogs=500)

    def test_accrual_after_partial_return(self):
        product = self._stocked()
        so = self.sale(product, 10)
        self.return_picking(so.picking_ids, 3)
        self.assertBalances(product, interim_out=700)
        self._accrual(so)
        self.assertEqual(self.balance(self.out_acc, product, date=self.today), 0)
        self.assertEqual(self.balance(self.cogs_acc, product, date=self.today), 700)
