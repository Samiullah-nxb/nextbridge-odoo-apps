# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo.tests.common import TransactionCase
from odoo.exceptions import AccessError


class TestContractRecordRules(TransactionCase):

    def setUp(self):
        super().setUp()
        self.contract_type_nda = self.env.ref('clm.contract_type_nda')

        self.dept_legal = self.env['hr.department'].create({'name': 'Legal'})
        self.dept_sales = self.env['hr.department'].create({'name': 'Sales'})

        self.user_sales = self.env['res.users'].create({
            'name': 'Sales User',
            'login': 'sales_user_rr',
            'group_ids': [(4, self.env.ref('clm.group_clm_user').id)],
        })
        self.employee_sales = self.env['hr.employee'].create({
            'name': 'Sales User',
            'user_id': self.user_sales.id,
            'department_id': self.dept_sales.id,
        })
        self.user_legal_admin = self.env['res.users'].create({
            'name': 'Legal Admin',
            'login': 'legal_admin_rr',
            'group_ids': [(4, self.env.ref('clm.group_clm_legal_admin').id)],
        })
        self.employee_legal = self.env['hr.employee'].create({
            'name': 'Legal Admin',
            'user_id': self.user_legal_admin.id,
            'department_id': self.dept_legal.id,
        })

        # Restricted contract in Legal department
        self.restricted_contract = self.env['clm.contract'].create({
            'name': 'Restricted M&A Deal',
            'contract_type_id': self.contract_type_nda.id,
            'department_id': self.dept_legal.id,
            'confidentiality_level': 'restricted',
        })

        # Normal contract in Sales department
        self.sales_contract = self.env['clm.contract'].create({
            'name': 'Sales NDA',
            'contract_type_id': self.contract_type_nda.id,
            'department_id': self.dept_sales.id,
            'confidentiality_level': 'internal',
        })

    def test_sales_user_sees_own_department_contracts(self):
        contracts = self.env['clm.contract'].with_user(self.user_sales).search([])
        self.assertIn(self.sales_contract, contracts)

    def test_sales_user_cannot_see_restricted_contract(self):
        contracts = self.env['clm.contract'].with_user(self.user_sales).search([])
        self.assertNotIn(self.restricted_contract, contracts)

    def test_legal_admin_sees_all_contracts(self):
        contracts = self.env['clm.contract'].with_user(self.user_legal_admin).search([])
        self.assertIn(self.restricted_contract, contracts)
        self.assertIn(self.sales_contract, contracts)

    def test_audit_events_not_readable_by_standard_user(self):
        # Audit events should only be visible to Legal Admin
        with self.assertRaises(AccessError):
            self.env['clm.audit.event'].with_user(self.user_sales).search([])

    def test_audit_events_readable_by_legal_admin(self):
        events = self.env['clm.audit.event'].with_user(self.user_legal_admin).search([])
        # Should see audit events from setUp contract creation
        self.assertTrue(len(events) > 0)
