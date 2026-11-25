"""Duplicate prevention: one initial entry per stock move, idempotent value changes."""
from psycopg2 import IntegrityError

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tools import mute_logger

from .common import StockAccountingAutomationCommon


@tagged('post_install', '-at_install')
class TestDuplicatePrevention(StockAccountingAutomationCommon):
    """Re-validation, re-posting and direct inserts never book a move twice."""

    def test_revalidation_does_not_post_again(self):
        product = self.new_product('fifo')
        po = self.purchase(product, 10, 100)
        so = self.sale(product, 4)
        moves = (po.picking_ids | so.picking_ids).move_ids
        entries = self.valuation_entries(moves)
        for picking in po.picking_ids | so.picking_ids:
            try:
                picking.button_validate()
            except UserError:
                pass
        moves._create_account_move()
        moves._set_value()
        self.env['stock.move'].browse(moves.ids)._create_account_move()
        self.assertEqual(self.valuation_entries(moves), entries)
        self.assertBalances(product, stock=600, interim_in=-1000, interim_out=400)
        self.assertEntriesTraced(moves)

    def test_invoice_and_bill_repost_do_not_duplicate(self):
        product = self.new_product('average')
        po = self.purchase(product, 10, 100)
        bill = self.bill(po, price=110)
        so = self.sale(product, 5)
        invoice = self.invoice(so)
        for _i in range(2):
            bill.button_draft()
            bill.action_post()
            invoice.button_draft()
            invoice.action_post()
        self.assertBalances(product, stock=550, interim_in=0, interim_out=0, cogs=550, pdiff=0)
        self.assertEqual(len(invoice.line_ids.filtered(lambda l: l.display_type == 'cogs')), 2)
        invoice._stock_move_accounting_reconcile()
        bill._stock_move_accounting_reconcile()
        self.assertInterimCleared(product)
        self.assertStockReconciled(product)

    def test_database_refuses_second_initial_entry(self):
        product = self.new_product('fifo')
        po = self.purchase(product, 10, 100)
        move = po.picking_ids.move_ids
        vals_list = move._prepare_valuation_aml_vals(1000, role='initial')
        with self.assertRaises(IntegrityError), mute_logger('odoo.sql_db'), self.env.cr.savepoint():
            self.env['account.move'].create({
                'journal_id': self.company.account_stock_journal_id.id,
                'line_ids': [Command.create(vals) for vals in vals_list],
            })
            self.env.flush_all()

    def test_valuation_entries_cannot_be_reset(self):
        product = self.new_product('fifo')
        po = self.purchase(product, 10, 100)
        entry = po.picking_ids.move_ids.account_move_id
        with self.assertRaisesRegex(UserError, 'cannot be reset to draft'):
            entry.button_draft()
        self.assertEqual(entry.state, 'posted')
