# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo.tests.common import TransactionCase

class TestClmWorkflow(TransactionCase):
    def test_create_workflow_task_and_delegation(self):
        self.contract_type_nda = self.env.ref('clm.contract_type_nda')
        contract = self.env['clm.contract'].create({
            'name': 'Test Contract Workflow',
            'contract_type_id': self.contract_type_nda.id
        })
        task = self.env['clm.workflow.task'].create({
            'contract_id': contract.id,
            'name': 'Test Task',
            'task_type': 'approval',
            'status': 'pending',
            'is_required': True
        })
        self.assertEqual(task.contract_id, contract)
        self.assertEqual(task.task_type, 'approval')
        self.assertEqual(task.status, 'pending')

        delegation = self.env['clm.workflow.delegation'].create({
            'task_id': task.id,
            'reason': 'Delegated for review'
        })
        self.assertEqual(delegation.task_id, task)
        self.assertEqual(delegation.reason, 'Delegated for review')
