"""Inventory adjustments, scrap, location valuation accounts, internal / transit moves, manufacturing."""
from odoo import Command
from odoo.tests import Form, tagged

from .common import StockAccountingAutomationCommon


@tagged('post_install', '-at_install')
class TestInventoryOperations(StockAccountingAutomationCommon):
    """Moves to / from virtual locations and internal moves."""

    def test_inventory_increase_and_decrease(self):
        product = self.new_product('average', cost=5)
        self.inventory(product, 10)
        self.assertBalances(product, stock=50, cogs=-50)
        self.inventory(product, 7)
        self.assertBalances(product, stock=35, cogs=-35)
        self.assertStockReconciled(product)

    def test_scrap(self):
        product = self.new_product('fifo')
        self.bill(self.purchase(product, 10, 100))
        scrap = self.env['stock.scrap'].create({
            'product_id': product.id, 'scrap_qty': 2, 'location_id': self.stock_location.id})
        scrap.action_validate()
        self.assertBalances(product, stock=800, cogs=200)
        self.assertEqual(scrap.move_ids.account_move_id.line_ids.stock_valuation_move_id, scrap.move_ids)
        self.assertStockReconciled(product)

    def test_location_with_valuation_account_left_to_odoo(self):
        """Locations carrying their own valuation account keep Odoo's entry (not a second one)."""
        loss_acc = self.env['account.account'].create({
            'name': 'Inventory loss', 'code': '699994', 'account_type': 'expense'})
        product = self.new_product('fifo')
        self.bill(self.purchase(product, 10, 100))
        inventory_loc = product.with_company(self.company).property_stock_inventory
        inventory_loc.valuation_account_id = loss_acc
        self.inventory(product, 8)
        move = self.env['stock.move'].search([('product_id', '=', product.id), ('location_dest_id', '=', inventory_loc.id)])
        self.assertEqual(len(move.account_move_id), 1)
        self.assertEqual(move.account_move_id.line_ids.stock_valuation_move_id, move)
        self.assertEqual(self.balance(loss_acc, product), 200)
        move._create_account_move()
        self.assertEqual(len(self.valuation_entries(move)), 1, "Odoo's location entry must not be posted twice")
        self.assertBalances(product, stock=800)
        self.assertStockReconciled(product)

    def test_internal_transfer_and_inter_warehouse_transit(self):
        product = self.new_product('fifo')
        self.bill(self.purchase(product, 10, 100))
        shelf = self.env['stock.location'].create({'name': 'Shelf', 'location_id': self.stock_location.id})
        transit = self.env['stock.location'].create({
            'name': 'SMA transit', 'usage': 'transit', 'company_id': self.company.id,
            'location_id': self.warehouse.view_location_id.id})
        for dest in (shelf, transit):
            picking = self.env['stock.picking'].create({
                'picking_type_id': self.warehouse.int_type_id.id,
                'location_id': self.stock_location.id, 'location_dest_id': dest.id,
                'move_ids': [Command.create({
                    'product_id': product.id, 'product_uom_qty': 2,
                    'location_id': self.stock_location.id, 'location_dest_id': dest.id})],
            })
            self.process(picking)
            self.assertFalse(picking.move_ids.account_move_id)
            self.assertFalse(picking.move_ids.account_valuation_tracked)
        self.assertBalances(product, stock=1000, interim_in=0, interim_out=0)
        self.assertStockReconciled(product)

    def test_inter_company_transit(self):
        """Out to the inter-company transit location (no company) is a sale to the
        other company: Stock Interim (Delivered), cleared by its invoice."""
        product = self.new_product('fifo')
        self.bill(self.purchase(product, 10, 100))
        transit = self.env.ref('stock.stock_location_inter_company')
        picking = self.env['stock.picking'].create({
            'picking_type_id': self.warehouse.out_type_id.id,
            'location_id': self.stock_location.id, 'location_dest_id': transit.id,
            'move_ids': [Command.create({
                'product_id': product.id, 'product_uom_qty': 3,
                'location_id': self.stock_location.id, 'location_dest_id': transit.id})],
        })
        self.process(picking)
        self.assertBalances(product, stock=700, interim_out=300)
        self.assertStockReconciled(product)

    def test_manufacturing(self):
        if 'mrp.production' not in self.env:
            self.skipTest('mrp is not installed')
        component = self.new_product('average')
        self.bill(self.purchase(component, 10, 10))
        finished = self.new_product('fifo')
        bom = self.env['mrp.bom'].create({
            'product_tmpl_id': finished.product_tmpl_id.id, 'product_qty': 1,
            'bom_line_ids': [Command.create({'product_id': component.id, 'product_qty': 2})],
        })
        production_form = Form(self.env['mrp.production'])
        production_form.product_id = finished
        production_form.bom_id = bom
        production_form.product_qty = 3
        production = production_form.save()
        production.action_confirm()
        production.action_assign()
        production_form = Form(production)
        production_form.qty_producing = 3
        production = production_form.save()
        production.move_raw_ids.picked = True
        production.button_mark_done()
        self.assertEqual(production.state, 'done')
        self.assertBalances(component, stock=40, cogs=60)
        self.assertBalances(finished, stock=60, cogs=-60)
        self.assertEqual(self.balance(self.cogs_acc), 0)
        self.assertStockReconciled(component)
        self.assertStockReconciled(finished)
        self.assertEntriesTraced(production.move_raw_ids | production.move_finished_ids)
