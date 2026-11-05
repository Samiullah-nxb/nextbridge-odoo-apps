# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo.tests.common import TransactionCase


class TestWorkflowEngine(TransactionCase):

    def setUp(self):
        super().setUp()
        self.env.ref('clm.group_clm_legal_admin').write({'user_ids': [(4, self.env.user.id)]})
        # Each test creates the rules it counts on; the seeded rules
        # (data/clm_workflow_rule_data.xml) would add tasks of their own.
        self.env['clm.workflow.rule'].search([]).active = False
        self.contract_type_nda = self.env.ref('clm.contract_type_nda')
        self.contract_type_services = self.env.ref('clm.contract_type_services')
        self.contract = self.env['clm.contract'].create({
            'name': 'Engine Test Contract',
            'contract_type_id': self.contract_type_nda.id,
        })

    def _create_rule(self, task_type='approval', contract_type=None, min_value=0):
        return self.env['clm.workflow.rule'].create({
            'name': f'Rule: {task_type}',
            'task_type': task_type,
            'task_name': f'Auto {task_type}',
            'is_required': True,
            'contract_type_id': contract_type,
            'min_contract_value': min_value,
            'assign_to_legal_admin': False,
        })

    def test_generate_tasks_creates_matching_tasks(self):
        self._create_rule(task_type='approval')
        self.contract.state = 'in_review'
        self.contract._generate_workflow_tasks()
        tasks = self.contract.workflow_task_ids
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].task_type, 'approval')

    def test_generate_tasks_skips_non_matching_contract_type(self):
        self._create_rule(task_type='review', contract_type=self.contract_type_services.id)
        self.contract.state = 'in_review'
        self.contract._generate_workflow_tasks()
        # contract is 'nda', rule is for 'services' — no tasks expected
        self.assertEqual(len(self.contract.workflow_task_ids), 0)

    def test_evaluate_workflow_state_all_complete(self):
        self._create_rule(task_type='approval')
        self.contract.state = 'in_review'
        self.contract._generate_workflow_tasks()
        self.assertFalse(self.contract._evaluate_workflow_state())

        self.contract.workflow_task_ids.action_approve()
        self.assertTrue(self.contract._evaluate_workflow_state())

    def test_evaluate_workflow_state_no_tasks(self):
        # No tasks → nothing blocking → should return True
        self.assertTrue(self.contract._evaluate_workflow_state())

    def test_evaluate_workflow_state_optional_task_not_blocking(self):
        rule = self._create_rule(task_type='review')
        rule.is_required = False
        self.contract.state = 'in_review'
        self.contract._generate_workflow_tasks()
        # All tasks are optional — engine should not block
        self.assertTrue(self.contract._evaluate_workflow_state())
