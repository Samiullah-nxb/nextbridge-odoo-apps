# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError


class TestClmContract(TransactionCase):

    def setUp(self):
        super().setUp()
        self.contract_type_nda = self.env.ref('clm.contract_type_nda')

    def test_create_contract_generates_reference(self):
        contract = self.env['clm.contract'].create({
            'name': 'Test Contract',
            'contract_type_id': self.contract_type_nda.id,
        })
        self.assertRegex(contract.contract_ref, r'^CNT-\d{4}-\d{5}$')

    def test_contract_default_state_is_draft(self):
        contract = self.env['clm.contract'].create({
            'name': 'Test Contract',
            'contract_type_id': self.contract_type_nda.id,
        })
        self.assertEqual(contract.state, 'draft')

    def test_valid_transition_draft_to_in_review(self):
        contract = self.env['clm.contract'].create({
            'name': 'Test Contract',
            'contract_type_id': self.contract_type_nda.id,
        })
        contract.action_submit_for_review()
        self.assertEqual(contract.state, 'in_review')

    def test_invalid_transition_draft_to_executed_blocked(self):
        contract = self.env['clm.contract'].create({
            'name': 'Test Contract',
            'contract_type_id': self.contract_type_nda.id,
        })
        with self.assertRaises(UserError):
            contract.write({'state': 'executed'})

    def test_invalid_transition_draft_to_active_blocked(self):
        contract = self.env['clm.contract'].create({
            'name': 'Test Contract',
            'contract_type_id': self.contract_type_nda.id,
        })
        with self.assertRaises(UserError):
            contract.write({'state': 'active'})

    def test_terminal_state_cannot_transition(self):
        contract = self.env['clm.contract'].create({
            'name': 'Test Contract',
            'contract_type_id': self.contract_type_nda.id,
        })
        # Walk to terminal. The workflow review gates are not under test here.
        contract.write({'state': 'in_review'})
        contract.with_context(clm_force_approve=True).write({'state': 'approved'})
        contract.write({'state': 'pending_signature'})
        contract.write({'state': 'executed'})
        contract.write({'state': 'active'})
        contract.write({'state': 'expired'})
        # Terminal — cannot go anywhere
        with self.assertRaises(UserError):
            contract.write({'state': 'active'})

    def test_migration_mode_bypasses_transition_check(self):
        contract = self.env['clm.contract'].with_context(migration_mode=True).create({
            'name': 'Legacy Contract',
            'contract_type_id': self.contract_type_nda.id,
            'state': 'active',  # Direct to active
        })
        self.assertEqual(contract.state, 'active')

    def test_audit_event_created_on_state_change(self):
        contract = self.env['clm.contract'].create({
            'name': 'Test Audit',
            'contract_type_id': self.contract_type_nda.id,
        })
        events = self.env['clm.audit.event'].sudo().search([
            ('contract_id', '=', contract.id),
            ('event_type', '=', 'state_change'),
        ])
        # At least one event from creation
        self.assertTrue(events)

    def test_contract_ref_unique(self):
        c1 = self.env['clm.contract'].create({
            'name': 'Contract 1',
            'contract_type_id': self.contract_type_nda.id,
        })
        c2 = self.env['clm.contract'].create({
            'name': 'Contract 2',
            'contract_type_id': self.contract_type_nda.id,
        })
        self.assertNotEqual(c1.contract_ref, c2.contract_ref)
