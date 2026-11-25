"""End-to-end flows with the company default accounts created at installation
(the other test classes use dedicated accounts per test category)."""
from odoo import Command, fields
from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged('post_install', '-at_install')
class TestDefaultAccounts(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.write({'group_ids': [Command.link(cls.env.ref(xmlid).id) for xmlid in (
            'sales_team.group_sale_manager', 'purchase.group_purchase_manager', 'stock.group_stock_manager')]})
        cls.company = cls.env.company
        cls.stock_acc = cls.company.account_stock_valuation_id
        cls.in_acc = cls.company.account_stock_input_id
        cls.out_acc = cls.company.account_stock_output_id
        cls.pdiff_acc = cls.company_data['default_account_expense']
        cls.warehouse = cls.env['stock.warehouse'].search([('company_id', '=', cls.company.id)], limit=1)
        cls.vendor = cls.env['res.partner'].create({'name': 'Vendor'})
        cls.customer = cls.env['res.partner'].create({'name': 'Customer'})

    @classmethod
    def _categ(cls, cost_method):
        categ = cls.env['product.category'].create({'name': f'Perpetual {cost_method}'})
        categ.property_valuation = 'real_time'
        categ.property_cost_method = cost_method
        categ.property_price_difference_account_id = cls.pdiff_acc
        return categ

    def _product(self, cost_method, cost=0.0):
        return self.env['product.product'].create({
            'name': f'Product {cost_method}',
            'is_storable': True,
            'categ_id': self._categ(cost_method).id,
            'standard_price': cost,
            'invoice_policy': 'delivery',
            'purchase_method': 'receive',
        })

    def _balance(self, account, product):
        return sum(self.env['account.move.line'].search([
            ('account_id', '=', account.id),
            ('product_id', '=', product.id),
            ('parent_state', '=', 'posted'),
        ]).mapped('balance'))

    def _validate(self, picking):
        picking.action_confirm()
        picking.action_assign()
        for move in picking.move_ids:
            move.quantity = move.product_uom_qty
        picking.move_ids.picked = True
        picking.button_validate()
        self.assertEqual(picking.state, 'done')

    def _purchase(self, product, qty, price):
        po = self.env['purchase.order'].create({
            'partner_id': self.vendor.id,
            'order_line': [Command.create({'product_id': product.id, 'product_qty': qty, 'price_unit': price})],
        })
        po.button_confirm()
        self._validate(po.picking_ids)
        return po

    def _bill(self, po, price=None):
        bill = self.env['account.move'].browse(po.action_create_invoice()['res_id'])
        bill.invoice_date = fields.Date.today()
        if price is not None:
            bill.invoice_line_ids.price_unit = price
        bill.action_post()
        return bill

    def _sale(self, product, qty):
        so = self.env['sale.order'].create({
            'partner_id': self.customer.id,
            'order_line': [Command.create({'product_id': product.id, 'product_uom_qty': qty, 'price_unit': 50})],
        })
        so.action_confirm()
        self._validate(so.picking_ids)
        return so

    def _assert_stock_matches_valuation(self, product):
        product.invalidate_recordset()
        self.assertAlmostEqual(self._balance(self.stock_acc, product), product.total_value)

    def test_interim_accounts_created(self):
        self.assertTrue(self.in_acc.reconcile)
        self.assertTrue(self.out_acc.reconcile)

    def test_avco_purchase_sale_flow(self):
        product = self._product('average')
        po = self._purchase(product, 10, 12)
        self.assertAlmostEqual(self._balance(self.stock_acc, product), 120)
        self.assertAlmostEqual(self._balance(self.in_acc, product), -120)

        bill = self._bill(po, 13)
        self.assertEqual(bill.invoice_line_ids.account_id, self.in_acc)
        self.assertAlmostEqual(self._balance(self.stock_acc, product), 130)
        self.assertAlmostEqual(self._balance(self.in_acc, product), 0)

        so = self._sale(product, 4)
        self.assertAlmostEqual(self._balance(self.out_acc, product), 52)
        invoice = so._create_invoices()
        invoice.action_post()
        self.assertAlmostEqual(self._balance(self.out_acc, product), 0)
        self._assert_stock_matches_valuation(product)

    def test_fifo_late_bill_splits_price_difference(self):
        product = self._product('fifo')
        po = self._purchase(product, 4, 10)
        so = self._sale(product, 3)
        so._create_invoices().action_post()
        before = self._balance(self.pdiff_acc, product)
        self._bill(po, 12)
        self.assertAlmostEqual(self._balance(self.stock_acc, product), 12)
        self.assertAlmostEqual(self._balance(self.in_acc, product), 0)
        self.assertAlmostEqual(self._balance(self.pdiff_acc, product) - before, 6)
        self._assert_stock_matches_valuation(product)

    def test_standard_receipt_at_standard_price(self):
        product = self._product('standard', cost=10)
        po = self._purchase(product, 5, 12)
        self.assertAlmostEqual(self._balance(self.stock_acc, product), 50)
        self._bill(po)
        self.assertAlmostEqual(self._balance(self.stock_acc, product), 50)
        self.assertAlmostEqual(self._balance(self.in_acc, product), 0)

        product.standard_price = 11
        self._assert_stock_matches_valuation(product)

    def test_inventory_adjustment_and_scrap(self):
        product = self._product('average', cost=5)
        quant = self.env['stock.quant'].with_context(inventory_mode=True).create({
            'product_id': product.id,
            'location_id': self.warehouse.lot_stock_id.id,
            'inventory_quantity': 10,
        })
        quant.action_apply_inventory()
        self.assertAlmostEqual(self._balance(self.stock_acc, product), 50)
        scrap = self.env['stock.scrap'].create({
            'product_id': product.id, 'scrap_qty': 2, 'location_id': self.warehouse.lot_stock_id.id})
        scrap.action_validate()
        self.assertAlmostEqual(self._balance(self.stock_acc, product), 40)
        self._assert_stock_matches_valuation(product)

    def test_feature_disabled_keeps_standard_behaviour(self):
        self.company.stock_move_accounting = False
        product = self._product('average')
        po = self._purchase(product, 1, 10)
        self.assertFalse(po.picking_ids.move_ids.account_move_id)
        bill = self._bill(po)
        self.assertEqual(bill.invoice_line_ids.account_id, self.stock_acc)
