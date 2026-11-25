"""Multi-company: per-company interim accounts, flows and feature switch."""
from odoo import Command
from odoo.tests import tagged

from .common import StockAccountingAutomationCommon


@tagged('post_install', '-at_install')
class TestMultiCompany(StockAccountingAutomationCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company_b = cls.setup_other_company(name='SMA company B')['company']
        Account = cls.env['account.account'].with_company(cls.company_b)
        cls.b_stock_acc = Account.create({
            'name': 'B Stock', 'code': '119992', 'account_type': 'asset_current',
            'company_ids': [Command.set(cls.company_b.ids)]})
        cls.b_cogs_acc = Account.create({
            'name': 'B COGS', 'code': '699995', 'account_type': 'expense_direct_cost',
            'company_ids': [Command.set(cls.company_b.ids)]})
        b_income = Account.create({
            'name': 'B Income', 'code': '499995', 'account_type': 'income',
            'company_ids': [Command.set(cls.company_b.ids)]})
        cls.b_categ = cls._create_categ(
            'fifo', company=cls.company_b, stock_acc=cls.b_stock_acc, cogs_acc=cls.b_cogs_acc,
            pdiff_acc=cls.b_cogs_acc, income_acc=b_income)

    def _switch_to_b(self):
        self.env = self.env(context=dict(self.env.context, allowed_company_ids=[self.company_b.id, self.company.id]))
        self.company = self.company_b
        self.currency = self.company_b.currency_id
        self.stock_acc = self.b_stock_acc
        self.cogs_acc = self.b_cogs_acc
        self.in_acc = self.company_b.account_stock_input_id
        self.out_acc = self.company_b.account_stock_output_id
        self.warehouse = self.env['stock.warehouse'].search([('company_id', '=', self.company_b.id)], limit=1)
        self.stock_location = self.warehouse.lot_stock_id

    def test_interim_accounts_per_company(self):
        for company in self.company | self.company_b:
            for account in company.account_stock_input_id | company.account_stock_output_id:
                self.assertTrue(account.reconcile)
                self.assertEqual(account.company_ids, company)
        self.assertNotEqual(self.company.account_stock_input_id, self.company_b.account_stock_input_id)

    def test_flow_in_second_company(self):
        a_in, a_out = self.in_acc, self.out_acc
        self._switch_to_b()
        product = self.new_product('fifo', categ=self.b_categ)
        po = self.purchase(product, 10, 100)
        self.assertEqual(po.company_id, self.company_b)
        entry = po.picking_ids.move_ids.account_move_id
        self.assertEqual(entry.company_id, self.company_b)
        self.assertEqual(entry.journal_id, self.company_b.account_stock_journal_id)
        self.bill(po, price=110)
        so = self.sale(product, 4)
        self.invoice(so)
        self.assertBalances(product, stock=660, interim_in=0, interim_out=0, cogs=440)
        self.assertStockReconciled(product, company=self.company_b)
        lines_in_a = self.env['account.move.line'].search([
            ('product_id', '=', product.id), ('account_id', 'in', (a_in | a_out).ids)])
        self.assertFalse(lines_in_a)

    def test_feature_per_company(self):
        self.company_b.stock_move_accounting = False
        self._switch_to_b()
        product = self.new_product('fifo', categ=self.b_categ)
        po = self.purchase(product, 10, 100)
        self.assertFalse(po.picking_ids.move_ids.account_move_id)
        self.assertEqual(self.bill(po).invoice_line_ids.account_id, self.b_stock_acc)
