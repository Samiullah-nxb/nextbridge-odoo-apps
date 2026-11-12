# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError, AccessError

class TestNegotiation(TransactionCase):

    def setUp(self):
        super().setUp()
        self.partner = self.env['res.partner'].create({'name': 'Test Counterparty'})
        self.user = self.env['res.users'].create({
            'name': 'Portal User',
            'login': 'portal_user',
            'partner_id': self.partner.id,
            'group_ids': [(4, self.env.ref('base.group_portal').id)]
        })
        self.contract = self.env['nextcontract.contract'].create({
            'name': 'Test Negotiation Contract',
            'partner_id': self.partner.id,
            'contract_type_id': self.env.ref('nextcontract.contract_type_nda').id,
        })
        self.version_1 = self.env['nextcontract.contract.version'].create({
            'contract_id': self.contract.id,
            'version_type': 'internal_draft',
            'version_number': 1
        })

    def test_action_send_for_negotiation(self):
        # Sending for negotiation creates a round and grants portal access
        round_1 = self.version_1.action_send_for_negotiation(self.partner)
        
        self.assertTrue(round_1)
        self.assertEqual(round_1.outbound_version_id, self.version_1)
        self.assertEqual(round_1.status, 'in_progress')
        self.assertEqual(self.version_1.version_type, 'negotiated_draft')
        
        # Check portal access was granted
        access = self.env['nextcontract.contract.portal.access'].search([
            ('contract_id', '=', self.contract.id),
            ('partner_id', '=', self.partner.id)
        ])
        self.assertTrue(access)

    def test_portal_access_security(self):
        # Without access record, portal user should not see contract
        with self.assertRaises(AccessError):
            self.contract.with_user(self.user).read(['name'])
            
        # Grant access
        self.env['nextcontract.contract.portal.access'].create({
            'contract_id': self.contract.id,
            'partner_id': self.partner.id,
        })
        
        # Now should be able to read
        result = self.contract.with_user(self.user).read(['name'])
        self.assertEqual(len(result), 1)

    def test_redline_identical_version_without_uploaded_docx(self):
        """A version with no uploaded DOCX can still redline (generated/empty content)."""
        comp = self.env['nextcontract.document.comparison'].create({
            'base_version_id': self.version_1.id,
            'target_version_id': self.version_1.id,
        })
        comp.action_generate_redline()
        self.assertEqual(comp.status, 'done', comp.error_message)
        self.assertFalse(comp.has_differences)
        self.assertIn('No differences found', comp.html_redline)

    def test_record_rules_document_comparison(self):
        """Verify record rules restrict access to document comparisons for restricted contracts."""
        standard_user = self.env['res.users'].create({
            'name': 'Standard NextContract User',
            'login': 'std_nextcontract_user_comp_rr',
            'email': 'std_nextcontract_user_comp_rr@example.com',
            'group_ids': [
                (4, self.env.ref('base.group_user').id),
                (4, self.env.ref('nextcontract.group_nextcontract_user').id),
            ],
        })

        # Create a restricted contract owned by admin
        restricted_contract = self.env['nextcontract.contract'].create({
            'name': 'Restricted Negotiation Contract',
            'partner_id': self.partner.id,
            'contract_type_id': self.env.ref('nextcontract.contract_type_nda').id,
            'confidentiality_level': 'restricted',
            'user_id': self.env.user.id,
        })

        restricted_version_1 = self.env['nextcontract.contract.version'].create({
            'contract_id': restricted_contract.id,
            'version_type': 'internal_draft',
            'version_number': 1,
        })
        restricted_version_2 = self.env['nextcontract.contract.version'].create({
            'contract_id': restricted_contract.id,
            'version_type': 'internal_draft',
            'version_number': 2,
        })

        # Create comparison on restricted contract
        restricted_comp = self.env['nextcontract.document.comparison'].create({
            'base_version_id': restricted_version_1.id,
            'target_version_id': restricted_version_2.id,
        })

        # 1. Standard user cannot search or read comparison on restricted contract
        comparisons = self.env['nextcontract.document.comparison'].with_user(standard_user).search([('id', '=', restricted_comp.id)])
        self.assertNotIn(restricted_comp, comparisons)
        with self.assertRaises(AccessError):
            restricted_comp.with_user(standard_user).read(['status'])

        # 2. Standard user can search and read comparison on accessible (unrestricted) contract
        unrestricted_version_2 = self.env['nextcontract.contract.version'].create({
            'contract_id': self.contract.id,
            'version_type': 'internal_draft',
            'version_number': 2,
        })
        unrestricted_comp = self.env['nextcontract.document.comparison'].create({
            'base_version_id': self.version_1.id,
            'target_version_id': unrestricted_version_2.id,
        })
        user_comps = self.env['nextcontract.document.comparison'].with_user(standard_user).search([('id', '=', unrestricted_comp.id)])
        self.assertIn(unrestricted_comp, user_comps)
        res = unrestricted_comp.with_user(standard_user).read(['status'])
        self.assertEqual(res[0]['status'], 'pending')

        # 3. Legal reviewer has full access to the comparison on restricted contract
        legal_user = self.env['res.users'].create({
            'name': 'Legal Reviewer',
            'login': 'legal_user_comp_rr',
            'email': 'legal_user_comp_rr@example.com',
            'group_ids': [
                (4, self.env.ref('base.group_user').id),
                (4, self.env.ref('nextcontract.group_nextcontract_legal').id),
            ],
        })
        legal_comparisons = self.env['nextcontract.document.comparison'].with_user(legal_user).search([('id', '=', restricted_comp.id)])
        self.assertIn(restricted_comp, legal_comparisons)
        res_legal = restricted_comp.with_user(legal_user).read(['status'])
        self.assertEqual(res_legal[0]['status'], 'pending')

