# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo.tests.common import TransactionCase

class TestPurchaseIntegration(TransactionCase):

    def setUp(self):
        super().setUp()
        self.contract_type_nda = self.env.ref('clm.contract_type_nda')
        self.partner = self.env['res.partner'].create({'name': 'Vendor A'})
        self.contract = self.env['clm.contract'].with_context(migration_mode=True).create({
            'name': 'Master Supply Agreement',
            'contract_type_id': self.contract_type_nda.id,
            'partner_id': self.partner.id,
            'state': 'active'
        })
        self.po1 = self.env['purchase.order'].create({
            'partner_id': self.partner.id
        })
        self.po2 = self.env['purchase.order'].create({
            'partner_id': self.partner.id
        })

    def test_multi_po_link(self):
        self.contract.purchase_order_ids = [(6, 0, [self.po1.id, self.po2.id])]
        self.assertEqual(len(self.contract.purchase_order_ids), 2)
        self.assertIn(self.contract, self.po1.clm_contract_ids)
        self.assertIn(self.contract, self.po2.clm_contract_ids)
