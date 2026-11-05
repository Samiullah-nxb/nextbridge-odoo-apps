# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError

class TestWorkflowTransitions(TransactionCase):

    def setUp(self):
        super().setUp()
        self.user = self.env['res.users'].create({
            'name': 'Test User',
            'login': 'test_user_wf',
            'group_ids': [(4, self.env.ref('clm.group_clm_user').id), (4, self.env.ref('base.group_user').id)]
        })
        self.partner = self.env['res.partner'].create({'name': 'Test Partner'})
        self.contract = self.env['clm.contract'].with_user(self.user).create({
            'name': 'Test Contract',
            'partner_id': self.partner.id,
            'contract_type_id': self.env.ref('clm.contract_type_nda').id,
        })

    def _reach_internal_review_done(self):
        """Walk the contract to internal_review_done through allowed states.

        self.contract belongs to the restricted user who created it; the
        state machine is driven here as the test superuser. The seeded rules
        are archived first so only the tasks a test creates count.
        """
        self.env['clm.workflow.rule'].search([]).active = False
        contract = self.contract.with_env(self.env)
        contract.state = 'in_review'
        contract.state = 'internal_review_done'

    def test_incomplete_required_task(self):
        self.contract.state = 'in_review'
        rule = self.env['clm.workflow.rule'].create({
            'name': 'Test Rule',
            'task_name': 'Review Task',
            'task_type': 'review',
            'stage_trigger': 'in_review',
            'is_required': True,
        })
        self.env['clm.workflow.task'].create({
            'contract_id': self.contract.id, 'task_type': 'approval',
            'rule_id': rule.id,
            'name': 'Approval Task', 'task_type': 'approval',
            'status': 'pending',
            'is_required': True,
            'assigned_user_id': self.user.id
        })
        
        # Approving is an Approver / Legal right; give the requester that role so
        # the test exercises the task gate and not the role check.
        self.user.group_ids = [(4, self.env.ref('clm.group_clm_manager').id)]
        with self.assertRaisesRegex(UserError, "Incomplete workflow tasks"):
            self.contract.with_user(self.user).action_approve()

    def test_legal_review_does_not_spawn_management_until_complete_internal_review(self):
        """Approving Legal Review must not immediately raise Management Approval."""
        self.contract.state = 'in_review'
        legal_rule = self.env.ref('clm.workflow_rule_legal_review')
        management_rule = self.env.ref('clm.workflow_rule_management_approval')
        # Avoid leftover standard tasks from write(state=…); seed only Legal Review.
        self.contract.with_env(self.env).workflow_task_ids.unlink()
        task = self.env['clm.workflow.task'].create({
            'contract_id': self.contract.id,
            'rule_id': legal_rule.id,
            'name': legal_rule.task_name,
            'task_type': legal_rule.task_type,
            'status': 'pending',
            'is_required': True,
            'assigned_user_id': self.user.id,
        })

        task.with_user(self.user).action_approve()

        self.assertEqual(task.status, 'completed')
        self.assertEqual(self.contract.state, 'in_review')
        self.assertFalse(
            self.contract.workflow_task_ids.filtered(lambda t: t.rule_id == management_rule),
            'Management Approval must not appear until Complete Internal Review.',
        )

        self.contract.action_complete_internal_review()
        self.assertEqual(self.contract.state, 'internal_review_done')
        management = self.contract.workflow_task_ids.filtered(
            lambda t: t.rule_id == management_rule
        )
        self.assertTrue(management, 'Complete Internal Review raises Management Approval.')
        self.assertEqual(management.status, 'pending')

    def test_automatic_advancement(self):
        self._reach_internal_review_done()
        rule = self.env['clm.workflow.rule'].create({
            'name': 'Review Rule',
            'task_name': 'Review Task',
            'task_type': 'review',
            'stage_trigger': 'internal_review_done',
            'is_required': True,
        })
        task = self.env['clm.workflow.task'].create({
            'contract_id': self.contract.id, 'task_type': 'approval',
            'rule_id': rule.id,
            'name': 'Approval Task', 'task_type': 'approval',
            'status': 'pending',
            'is_required': True,
            'assigned_user_id': self.user.id
        })

        # Completing required tasks at internal_review_done advances to approved.
        task.with_user(self.user).action_approve()

        self.assertEqual(self.contract.state, 'approved')

    def test_optional_task_advancement(self):
        self._reach_internal_review_done()
        rule1 = self.env['clm.workflow.rule'].create({
            'name': 'Required Rule',
            'task_name': 'Required Task',
            'task_type': 'review',
            'stage_trigger': 'internal_review_done',
            'is_required': True,
        })
        rule2 = self.env['clm.workflow.rule'].create({
            'name': 'Optional Rule',
            'task_name': 'Optional Task',
            'task_type': 'review',
            'stage_trigger': 'internal_review_done',
            'is_required': False,
        })
        task_req = self.env['clm.workflow.task'].create({
            'contract_id': self.contract.id, 'task_type': 'approval',
            'rule_id': rule1.id,
            'name': 'Required Task',
            'status': 'pending',
            'is_required': True,
            'assigned_user_id': self.user.id
        })
        task_opt = self.env['clm.workflow.task'].create({
            'contract_id': self.contract.id, 'task_type': 'approval',
            'rule_id': rule2.id,
            'name': 'Optional Task',
            'status': 'pending',
            'is_required': False,
            'assigned_user_id': self.user.id
        })

        # Complete only the required task
        task_req.with_user(self.user).action_approve()

        # Contract should advance even though the optional task is still pending
        self.assertEqual(self.contract.state, 'approved')
        self.assertEqual(task_opt.status, 'pending')

    def test_transition_idempotency(self):
        self._reach_internal_review_done()

        # No required tasks for this stage → advance to approved.
        self.contract._check_and_advance_workflow()
        self.assertEqual(self.contract.state, 'approved')

        # Calling it again shouldn't push it further.
        self.contract._check_and_advance_workflow()
        self.assertEqual(self.contract.state, 'approved')

    def test_rejection_logic(self):
        self.contract.state = 'in_review'
        rule = self.env['clm.workflow.rule'].create({
            'name': 'Review Rule',
            'task_name': 'Review Task',
            'task_type': 'review',
            'stage_trigger': 'in_review',
            'is_required': True,
        })
        task = self.env['clm.workflow.task'].create({
            'contract_id': self.contract.id, 'task_type': 'approval',
            'rule_id': rule.id,
            'name': 'Approval Task', 'task_type': 'approval',
            'status': 'pending',
            'is_required': True,
            'assigned_user_id': self.user.id
        })
        
        task.with_user(self.user).action_reject()
        
        # Rejection from in_review falls back to draft
        self.assertEqual(self.contract.state, 'draft')
        self.assertEqual(task.status, 'rejected')

    def test_unauthorized_user(self):
        self.contract.state = 'in_review'
        rule = self.env['clm.workflow.rule'].create({
            'name': 'Review Rule',
            'task_name': 'Review Task',
            'task_type': 'review',
            'stage_trigger': 'in_review',
            'is_required': True,
        })
        task = self.env['clm.workflow.task'].create({
            'contract_id': self.contract.id, 'task_type': 'approval',
            'rule_id': rule.id,
            'name': 'Approval Task', 'task_type': 'approval',
            'status': 'pending',
            'is_required': True,
            'assigned_user_id': self.user.id
        })
        
        unauthorized_user = self.env['res.users'].create({
            'name': 'Unauthorized User',
            'login': 'unauthorized_user',
            'group_ids': [(4, self.env.ref('clm.group_clm_user').id), (4, self.env.ref('base.group_user').id)]
        })
        
        from odoo.exceptions import AccessError
        with self.assertRaises(AccessError):
            task.with_user(unauthorized_user).action_approve()
            
        with self.assertRaises(AccessError):
            task.with_user(unauthorized_user).action_delegate(self.user.id)
