# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo.tests.common import TransactionCase


class TestContractFieldSecurity(TransactionCase):

    def setUp(self):
        super().setUp()
        self.type_nda = self.env.ref('clm.contract_type_nda')

        # CI_LESSONS_LEARNED 1.1/1.2: test users need email + base.group_user
        self.user_standard = self.env['res.users'].create({
            'name': 'Standard User',
            'login': 'field_std_user',
            'email': 'field_std_user@example.com',
            'group_ids': [
                (4, self.env.ref('base.group_user').id),
                (4, self.env.ref('clm.group_clm_user').id),
            ],
        })
        self.user_legal = self.env['res.users'].create({
            'name': 'Legal User',
            'login': 'field_legal_user',
            'email': 'field_legal_user@example.com',
            'group_ids': [
                (4, self.env.ref('base.group_user').id),
                (4, self.env.ref('clm.group_clm_legal').id),
            ],
        })

        self.contract = self.env['clm.contract'].create({
            'name': 'Security Test Contract',
            'contract_type_id': self.type_nda.id,
            'user_id': self.user_standard.id,
        })

        self.def_open = self.env['clm.contract.field.definition'].create({
            'name': 'Governing Law', 'code': 'gov_law_sec', 'value_type': 'char',
        })
        self.def_secret = self.env['clm.contract.field.definition'].create({
            'name': 'Settlement Amount', 'code': 'settlement_sec',
            'value_type': 'monetary', 'confidential': True,
        })

        self.value_open = self.env['clm.contract.field.value'].create({
            'contract_id': self.contract.id,
            'definition_id': self.def_open.id,
            'value_char': 'England and Wales',
        })
        self.value_secret = self.env['clm.contract.field.value'].create({
            'contract_id': self.contract.id,
            'definition_id': self.def_secret.id,
            'value_float': 750000.0,
        })

    def test_standard_user_reads_non_confidential_value(self):
        visible = self.env['clm.contract.field.value'].with_user(
            self.user_standard).search([('contract_id', '=', self.contract.id)])
        self.assertIn(self.value_open, visible)

    def test_standard_user_cannot_read_confidential_value(self):
        visible = self.env['clm.contract.field.value'].with_user(
            self.user_standard).search([('contract_id', '=', self.contract.id)])
        self.assertNotIn(self.value_secret, visible)

    def test_standard_user_cannot_reach_confidential_value_by_direct_search(self):
        """Searching the confidential definition directly must not leak it."""
        found = self.env['clm.contract.field.value'].with_user(
            self.user_standard).search([('definition_id', '=', self.def_secret.id)])
        self.assertFalse(found)

    def test_legal_user_reads_confidential_value(self):
        visible = self.env['clm.contract.field.value'].with_user(
            self.user_legal).search([('contract_id', '=', self.contract.id)])
        self.assertIn(self.value_secret, visible)

    def test_standard_user_cannot_write_values(self):
        from odoo.exceptions import AccessError
        with self.assertRaises(AccessError):
            self.value_open.with_user(self.user_standard).write(
                {'value_char': 'Scotland'})

    def test_standard_user_cannot_create_definitions(self):
        from odoo.exceptions import AccessError
        with self.assertRaises(AccessError):
            self.env['clm.contract.field.definition'].with_user(
                self.user_standard).create({
                    'name': 'Sneaky', 'code': 'sneaky_sec', 'value_type': 'char',
                })

    def test_legal_user_cannot_create_definitions(self):
        """Definitions are schema + AI-behaviour config: Legal Admin only."""
        from odoo.exceptions import AccessError
        with self.assertRaises(AccessError):
            self.env['clm.contract.field.definition'].with_user(
                self.user_legal).create({
                    'name': 'Also Sneaky', 'code': 'sneaky2_sec', 'value_type': 'char',
                })
