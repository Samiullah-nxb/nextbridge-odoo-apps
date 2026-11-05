# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError


class TestWorkflowActions(TransactionCase):

    def setUp(self):
        super().setUp()
        self.contract_type_nda = self.env.ref('clm.contract_type_nda')
        self.contract = self.env['clm.contract'].create({
            'name': 'Test Contract',
            'contract_type_id': self.contract_type_nda.id
        })
        self.user1 = self.env['res.users'].create({'name': 'User 1', 'login': 'user1_wfactions', 'email': 'user1@test.com', 'group_ids': [(4, self.env.ref('clm.group_clm_user').id), (4, self.env.ref('base.group_user').id)]})
        self.user2 = self.env['res.users'].create({'name': 'User 2', 'login': 'user2_wfactions', 'email': 'user2@test.com', 'group_ids': [(4, self.env.ref('clm.group_clm_user').id), (4, self.env.ref('base.group_user').id)]})
        self.task = self.env['clm.workflow.task'].create({
            'contract_id': self.contract.id,
            'name': 'Test Task',
            'task_type': 'approval',
            'original_assigned_user_id': self.user1.id,
            'assigned_user_id': self.user1.id,
        })

    def test_approve_transitions_status(self):
        action = self.task.with_user(self.user1).action_approve()
        self.assertEqual(self.task.status, 'completed')
        self.assertTrue(self.task.completion_date)
        # Reload the contract form so header buttons (Approve) match new state.
        self.assertEqual(action.get('type'), 'ir.actions.act_window')
        self.assertEqual(action.get('res_model'), 'clm.contract')
        self.assertEqual(action.get('res_id'), self.contract.id)

    def test_reject_transitions_status(self):
        action = self.task.with_user(self.user1).action_reject()
        self.assertEqual(self.task.status, 'rejected')
        self.assertTrue(self.task.completion_date)
        self.assertEqual(action.get('type'), 'ir.actions.act_window')
        self.assertEqual(action.get('res_id'), self.contract.id)

    def test_approve_raises_on_terminal_state(self):
        self.task.with_user(self.user1).action_approve()
        with self.assertRaises(UserError):
            self.task.with_user(self.user1).action_approve()

    def test_reject_raises_on_terminal_state(self):
        self.task.with_user(self.user1).action_reject()
        with self.assertRaises(UserError):
            self.task.with_user(self.user1).action_reject()

    def test_delegate_creates_delegation_record_and_updates_assignee(self):
        self.task.with_user(self.user1).action_delegate(self.user2.id, reason='Annual leave')

        self.assertEqual(self.task.assigned_user_id.id, self.user2.id)

        delegation = self.env['clm.workflow.delegation'].search([('task_id', '=', self.task.id)])
        self.assertEqual(len(delegation), 1)
        self.assertEqual(delegation.original_user_id.id, self.user1.id)
        self.assertEqual(delegation.delegated_to_id.id, self.user2.id)
        self.assertEqual(delegation.reason, 'Annual leave')

    def test_delegate_raises_on_completed_task(self):
        self.task.with_user(self.user1).action_approve()
        with self.assertRaises(UserError):
            self.task.with_user(self.user1).action_delegate(self.user2.id)
