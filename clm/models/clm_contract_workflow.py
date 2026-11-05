# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models, fields
from odoo.exceptions import UserError

# Review detours a contract can sit in while Legal Review is open.
_REVIEW_STAGES = (
    'in_review', 'awaiting_business', 'awaiting_finance',
    'awaiting_procurement', 'awaiting_compliance',
)
# Stages whose required tasks must be completed before Approved for Signature.
_PRE_APPROVAL_STAGES = _REVIEW_STAGES + ('internal_review_done', 'in_negotiation')
# Stages every contract has to pass through (the others are optional detours).
_MANDATORY_APPROVAL_STAGES = ('in_review', 'internal_review_done')


class ClmContract(models.Model):
    """
    Inherits clm.contract (defined in clm_base) to:
    - Add the workflow_task_ids relation
    - Provide _generate_workflow_tasks() for rule-driven task creation
    - Provide _evaluate_workflow_state() to drive contract state transitions
    """
    _inherit = 'clm.contract'

    workflow_task_ids = fields.One2many(
        'clm.workflow.task', 'contract_id', string='Workflow Tasks'
    )

    # -------------------------------------------------------------------------
    # Workflow engine
    # -------------------------------------------------------------------------

    def write(self, vals):
        if (
            vals.get('state') == 'approved'
            and not self.env.context.get('migration_mode')
            and not self.env.context.get('clm_force_approve')
        ):
            # Last line of defence: whatever button, RPC call or code path asks
            # for "Approved for Signature", the review gates must have passed.
            for contract in self:
                # pending_signature -> approved is a signature rollback, not a
                # new approval: the gates were already passed.
                if contract.state not in ('approved', 'pending_signature'):
                    contract._clm_assert_approval_gates_passed()
        if vals.get('state') == 'draft':
            # Returning to draft (amendment, rejection, return for clarification,
            # reopen) ends the review cycle: its tasks, approved or rejected,
            # must not carry over, otherwise a rejected task stays terminal and
            # strands the contract, and an old approval short-cuts the next
            # review. Only a transition that is allowed may close the cycle.
            leaving = self.filtered(lambda c: c.state != 'draft')
            leaving._validate_state_transition('draft')
            leaving._archive_workflow_cycle()
        res = super().write(vals)
        if 'state' in vals:
            for contract in self:
                contract._generate_workflow_tasks()
        return res

    def _archive_workflow_cycle(self):
        """Close the finished review cycle before an amendment starts a new one.

        The tasks stay on record (archived) for the audit trail; archiving them
        is what lets the amendment generate — and require — fresh review and
        approval tasks.
        """
        tasks = self.sudo().workflow_task_ids
        tasks.filtered(lambda t: t.status in ('pending', 'in_progress')).write({'status': 'cancelled'})
        tasks.write({'active': False})
        self.invalidate_recordset(['workflow_task_ids'])

    def _clm_visited_states_this_cycle(self):
        """States the contract entered since it was last (re)opened as a draft."""
        self.ensure_one()
        events = self.env['clm.audit.event'].sudo().search_read(
            [('contract_id', '=', self.id), ('event_type', '=', 'state_change')],
            ['new_value'], order='id asc',
        )
        visited = []
        for event in events:
            if event['new_value'] == 'draft':
                visited = []
            visited.append(event['new_value'])
        return set(visited)

    def _clm_approval_blockers(self):
        """Why this contract may not yet be Approved for Signature.

        Returns a list of human-readable reasons (empty = gates passed): a
        required task of any pre-approval stage that is not completed, or a
        mandatory stage (Legal Review / Management Approval) that the contract
        skipped on its way here.
        """
        self.ensure_one()
        contract = self.sudo()
        reasons = []
        for task in contract.workflow_task_ids.filtered(
            lambda t: t.is_required
            and t.rule_id.stage_trigger in _PRE_APPROVAL_STAGES
            and t.status != 'completed'
        ):
            reasons.append("'%s' is not completed" % task.name)
        visited = contract._clm_visited_states_this_cycle()
        rules = self.env['clm.workflow.rule'].sudo().search([
            ('active', '=', True), ('is_required', '=', True),
            ('stage_trigger', 'in', _MANDATORY_APPROVAL_STAGES),
        ])
        for rule in rules:
            if rule.stage_trigger in visited or contract.state == rule.stage_trigger:
                continue
            if rule.matches(contract, ignore_stage=True):
                stage_label = dict(rule._fields['stage_trigger'].selection)[rule.stage_trigger]
                reasons.append(
                    "the contract skipped the '%s' stage ('%s' was never raised)"
                    % (stage_label, rule.task_name)
                )
        return reasons

    def _clm_assert_approval_gates_passed(self):
        self.ensure_one()
        reasons = self._clm_approval_blockers()
        if reasons:
            raise UserError(
                "Incomplete workflow tasks: cannot approve '%s' for signature: %s."
                % (self.display_name, '; '.join(reasons))
            )

    def action_approve(self):
        # Business Requesters, Procurement and Finance never approve a contract.
        self._clm_require_groups('clm.group_clm_legal', 'clm.group_clm_manager')
        for contract in self:
            if contract._clm_approval_blockers():
                if self.env.user.has_group('clm.group_clm_legal_admin'):
                    return {
                        'name': 'Force Approve Contract',
                        'type': 'ir.actions.act_window',
                        'res_model': 'clm.force.approve.wizard',
                        'view_mode': 'form',
                        'target': 'new',
                        'context': {'default_contract_id': contract.id}
                    }
                else:
                    contract._clm_assert_approval_gates_passed()
            else:
                super(ClmContract, contract).action_approve()
        return True

    def _get_legal_admin_user(self):
        """Return the first active Legal Admin user, or False."""
        group = self.env.ref('clm.group_clm_legal_admin', raise_if_not_found=False)
        if not group:
            return False
        return group.sudo().user_ids[:1]

    def _generate_workflow_tasks(self):
        """
        Evaluate all active workflow rules against this contract and create
        the matching clm.workflow.task records. Called on contract creation
        or when entering a stage that requires a fresh task set.
        """
        # sudo: rules are configuration, and matching them must not depend on
        # what the acting user may read (a Legal Reviewer has no ACL on rules, a
        # requester cannot read contract_value, and neither sees others' tasks).
        rules = self.env['clm.workflow.rule'].sudo().search([('active', '=', True)])
        legal_admin = self._get_legal_admin_user()

        for rule in rules:
            if rule.matches(self.sudo()):
                # M-05: Skip if task already exists for this rule
                existing = self.sudo().workflow_task_ids.filtered(
                    lambda t: t.rule_id == rule
                )
                if existing:
                    continue

                vals = {
                    'contract_id': self.id,
                    'rule_id': rule.id,
                    'name': rule.task_name,
                    'task_type': rule.task_type,
                    'is_required': rule.is_required,
                }
                if rule.assign_to_legal_admin and legal_admin:
                    vals['assigned_user_id'] = legal_admin.id
                    vals['original_assigned_user_id'] = legal_admin.id
                self.env['clm.workflow.task'].sudo().create(vals)

    def _evaluate_workflow_state(self):
        """
        Inspect all required workflow tasks for this contract.
        If all required tasks are completed, return True. Otherwise return False.
        """
        self.ensure_one()
        # Find all tasks for the *current* stage (i.e. spawned by rules for this stage)
        # sudo: a gate must not pass just because the acting user cannot see the
        # tasks assigned to someone else.
        current_tasks = self.sudo().workflow_task_ids.filtered(lambda t: t.rule_id.stage_trigger == self.state)
        required_tasks = current_tasks.filtered(lambda t: t.is_required)
        
        if not required_tasks:
            return True  # no required tasks for this stage → nothing blocking
        
        return all(t.status == 'completed' for t in required_tasks)

    def _get_workflow_next_state(self):
        """Next state after required tasks for the *current* stage are done.

        Legal Review (``in_review`` / ``awaiting_*``) does **not** auto-advance
        to ``internal_review_done``. That step is intentional via
        ``action_complete_internal_review``, which is what raises Management
        Approval. Auto-advancing here made Management Approval appear on the
        Workflow Tasks list as soon as Legal Review was approved.
        """
        return {
            'internal_review_done': 'approved',
            'in_negotiation': 'approved',
        }.get(self.state)

    def action_complete_internal_review(self):
        """Move to Internal Review Completed only after required review tasks."""
        for contract in self:
            # Legal Review stays open until done, also while the contract sits in
            # a "waiting for input" detour (Awaiting Finance, ...).
            open_review_tasks = contract.sudo().workflow_task_ids.filtered(
                lambda t: t.is_required
                and t.rule_id.stage_trigger in _REVIEW_STAGES
                and t.status != 'completed'
            )
            if not contract._evaluate_workflow_state() or open_review_tasks:
                raise UserError(
                    "Complete all required workflow tasks for this stage "
                    "(for example Legal Review Approval) before completing "
                    "internal review."
                )
        return super().action_complete_internal_review()

    def _check_and_advance_workflow(self):
        """
        Centralized workflow transition service. Called after a task is completed.
        Evaluates stage completion, validates transitions, and advances the contract state.
        """
        for contract in self:
            if contract._evaluate_workflow_state():
                # All required tasks for the current stage are complete.
                next_state = contract._get_workflow_next_state()
                if next_state == 'approved' and contract._clm_approval_blockers():
                    # An earlier stage still has open required tasks.
                    continue
                if next_state:
                    contract.write({'state': next_state})

    def _handle_workflow_rejection(self, task):
        """
        Handle rejection of a workflow task by transitioning backwards
        based on the configured rejection path for the current state.
        """
        rejection_transitions = {
            'in_review': 'draft',
            'awaiting_business': 'in_review',
            'awaiting_finance': 'in_review',
            'awaiting_procurement': 'in_review',
            'awaiting_compliance': 'in_review',
            'internal_review_done': 'in_review',
            'in_negotiation': 'in_review',
            'approved': 'in_review',
            'pending_signature': 'in_review',
        }
        
        for contract in self:
            rejection_state = rejection_transitions.get(contract.state)
            if rejection_state:
                contract.write({'state': rejection_state})
