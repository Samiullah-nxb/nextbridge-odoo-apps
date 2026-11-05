# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo.tests.common import TransactionCase
from odoo import fields
from datetime import timedelta

class TestPortalSecurity(TransactionCase):

    def setUp(self):
        super().setUp()
        self.partner = self.env['res.partner'].create({'name': 'Test Portal Partner'})
        self.user = self.env['res.users'].create({
            'name': 'Portal User',
            'login': 'portal_user_sec',
            'partner_id': self.partner.id,
            'group_ids': [(4, self.env.ref('base.group_portal').id)]
        })
        self.contract = self.env['clm.contract'].create({
            'name': 'Test Security Contract',
            'partner_id': self.partner.id,
            'contract_type_id': self.env.ref('clm.contract_type_nda').id,
        })
        self.access = self.env['clm.contract.portal.access'].create({
            'contract_id': self.contract.id,
            'partner_id': self.partner.id,
            'start_date': fields.Datetime.now(),
        })

    def test_active_access_future_start_date(self):
        # Test 1: start_date in future -> access.is_active() is False.
        self.access.start_date = fields.Datetime.now() + timedelta(days=1)
        self.assertFalse(self.access.is_active(), "Access should not be active before start_date")

    def test_active_access_past_expiry_date(self):
        # Test 2: expiry_date in past -> access.is_active() is False.
        self.access.start_date = fields.Datetime.now() - timedelta(days=2)
        self.access.expiry_date = fields.Datetime.now() - timedelta(days=1)
        self.assertFalse(self.access.is_active(), "Access should not be active after expiry_date")

    def test_active_access_valid_dates(self):
        # Test 3: valid dates -> access.is_active() is True.
        self.access.start_date = fields.Datetime.now() - timedelta(days=1)
        self.access.expiry_date = fields.Datetime.now() + timedelta(days=1)
        self.assertTrue(self.access.is_active(), "Access should be active within valid dates")
