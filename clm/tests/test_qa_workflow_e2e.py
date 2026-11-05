# Part of NextContract. See LICENSE file for full copyright and licensing details.
"""QA: review loops, version integrity and the full intake -> archive journey."""
import datetime

from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import tagged

from . import test_qa_lifecycle_security as qa


@tagged('post_install', '-at_install', 'clm_qa')
class TestWorkflowLoops(qa.TestClmQaBase):
    """Phase 5-6: reject / return / resubmit must not strand or short-cut a contract."""

    def _legal_task(self, contract):
        return contract.sudo().workflow_task_ids.filtered(
            lambda t: t.rule_id.stage_trigger == 'in_review')

    def test_rejected_review_can_be_redone_after_resubmission(self):
        """BUG-17: a rejected Legal Review is terminal and was never re-raised."""
        contract = self._contract(user=self.requester)
        contract.action_submit_for_review()
        self._legal_task(contract).with_user(self.admin).action_reject()
        self.assertEqual(contract.state, 'draft', 'rejection returns the contract to its initiator')

        contract.action_submit_for_review()
        self.assertEqual(contract.state, 'in_review')
        open_tasks = self._legal_task(contract).filtered(
            lambda t: t.status in ('pending', 'in_progress'))
        self.assertTrue(
            open_tasks,
            'After resubmission there is no open Legal Review task: the rejected '
            'one is terminal and no new one was raised, so the contract is stuck.')
        open_tasks.with_user(self.admin).action_approve()
        contract.with_user(self.legal).action_complete_internal_review()
        self.assertEqual(contract.state, 'internal_review_done')

    def test_stale_approval_does_not_survive_a_return_for_clarification(self):
        """BUG-17: approve -> return -> resubmit must be reviewed again."""
        contract = self._contract(user=self.requester)
        contract.action_submit_for_review()
        self._legal_task(contract).with_user(self.admin).action_approve()
        contract.action_return_for_clarification()
        self.assertEqual(contract.state, 'draft')
        contract.action_submit_for_review()
        self.assertTrue(
            self._tries(lambda: contract.with_user(self.legal).action_complete_internal_review()),
            'A returned-and-resubmitted contract skipped Legal Review because the '
            'approval from the previous round was still on record.')

    def test_double_submit_is_refused(self):
        contract = self._contract(user=self.requester)
        contract.action_submit_for_review()
        with self.assertRaises(UserError):
            contract.action_submit_for_review()
        self.assertEqual(len(self._legal_task(contract)), 1, 'exactly one Legal Review task')

    def test_state_changes_leave_an_audit_trail(self):
        contract = self._contract(user=self.requester)
        contract.action_submit_for_review()
        contract.action_return_for_clarification()
        events = self.env['clm.audit.event'].sudo().search(
            [('contract_id', '=', contract.id), ('event_type', '=', 'state_change')],
            order='id')
        self.assertEqual(events.mapped('new_value'), ['draft', 'in_review', 'draft'])
        self.assertTrue(all(events.mapped('create_date')))
        self.assertEqual(set(events.mapped('user_id')), {self.requester})


@tagged('post_install', '-at_install', 'clm_qa')
class TestVersionIntegrity(qa.TestClmQaBase):
    """Section 5: version relationships must stay inside one contract."""

    def test_parent_version_must_belong_to_same_contract(self):
        """BUG-18: a version can be parented to another contract's version."""
        contract_a = self._contract(name='Contract A')
        contract_b = self._contract(name='Contract B')
        version_a = self._version(contract_a)
        self.assertTrue(
            self._tries(lambda: self._version(
                contract_b, parent_version_id=version_a.id, version_number=2)),
            'A version of contract B was parented to a version of contract A')

    def test_version_numbers_increase_per_contract(self):
        contract = self._contract()
        v1 = self._version(contract, version_number=0)
        v2 = self._version(contract, version_number=0, status='draft')
        self.assertTrue(v1.version_number and v2.version_number > v1.version_number)


@tagged('post_install', '-at_install', 'clm_qa')
class TestEndToEndLifecycle(qa.TestClmQaBase):
    """Intake -> review -> approval -> signature -> execution -> activation,
    driven by the real roles."""

    def _complete_signers(self, request):
        for item in request.request_item_ids:
            self.env.cr.execute(
                "UPDATE sign_request_item SET state = 'completed' WHERE id = %s", (item.id,))
        request.invalidate_recordset()
        request.request_item_ids.invalidate_recordset()

    def test_intake_to_activation(self):
        request = self.env['clm.contract.request'].with_user(self.requester).create({
            'name': 'Master Supply Agreement', 'contract_type_id': self.nda.id,
            'partner_id': self.partner.id, 'partner_signatory_id': self.signatory.id,
            'department_id': self.dept_sales.id, 'estimated_value': 90000.0,
            'start_date': datetime.date.today(),
            'purpose': 'Supply of components', 'urgency': 'urgent',
            'required_legal_action': 'new_contract',
        })
        # intake: accepted by a Legal Reviewer, converted by a Legal Admin
        request.action_submit()
        request.with_user(self.legal).action_start_intake()
        request.with_user(self.legal).action_accept()
        wizard = self.env['clm.request.convert.wizard'].with_user(self.admin).with_context(
            active_id=request.id, active_model='clm.contract.request').create({})
        wizard.action_convert()
        contract = request.contract_id.with_user(self.admin)
        self.assertEqual(request.state, 'converted')
        self.assertEqual(contract.state, 'draft')

        # document, Legal Review, Management Approval (DoP)
        version = self._version(contract)
        contract.action_submit_for_review()
        legal_task = contract.sudo().workflow_task_ids.filtered(
            lambda t: t.rule_id.stage_trigger == 'in_review')
        legal_task.with_user(self.admin).action_approve()
        contract.with_user(self.legal).action_complete_internal_review()
        dop_task = contract.sudo().workflow_task_ids.filtered(
            lambda t: t.rule_id.stage_trigger == 'internal_review_done')
        self.assertTrue(dop_task, 'Management Approval (DoP) must be raised')
        dop_task.with_user(self.admin).action_approve()
        self.assertEqual(contract.state, 'approved')

        # signature
        contract.with_user(self.legal).action_prepare_signature_draft()
        self.assertEqual(version.version_type, 'signature_draft')
        contract.with_user(self.legal).action_send_for_signature()
        self.assertEqual(contract.state, 'pending_signature')
        sign_request = contract.sudo().sign_request_ids
        self.assertEqual(sign_request.clm_source_version_id, version)
        self.assertEqual(
            set(sign_request.request_item_ids.mapped('partner_id').ids),
            {contract.company_id.partner_id.id, self.signatory.id},
            'Signers are the company and the counterparty signatory')
        self._complete_signers(sign_request)
        sign_request.write({'state': 'signed'})
        self.assertEqual(contract.state, 'executed')
        executed = contract.final_version_id
        self.assertEqual(executed.version_type, 'executed')
        self.assertEqual(executed.source_version_id, version)
        with self.assertRaises(UserError):
            executed.write({'notes': 'tamper'})

        # activation: the checklist demands data the intake does not carry; the
        # Legal Reviewer completes it, and until then activation is refused
        self.assertEqual(
            set(contract._get_failed_activation_checks()),
            {'Legal Owner', 'Expiry Date or Auto-Renewal', 'Notice Period (Days)', 'Risk Rating'})
        with self.assertRaises(UserError):
            contract.with_user(self.legal).action_activate()
        contract.with_user(self.legal).write({
            'legal_owner_id': self.legal.id,
            'expiry_date': datetime.date.today() + datetime.timedelta(days=365),
            'notice_period_days': 30,
            'risk_rating': 'low',
        })
        self.assertFalse(contract._get_failed_activation_checks())
        contract.with_user(self.legal).action_activate()
        self.assertEqual(contract.state, 'active')
        self.assertTrue(contract.alert_ids, 'default expiry/renewal alerts are seeded')
