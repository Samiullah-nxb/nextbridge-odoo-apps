# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models, fields, api
from odoo.exceptions import UserError, AccessError

_TERMINAL_STATES = ('completed', 'cancelled', 'rejected')


class ClmWorkflowTask(models.Model):
    _name = 'clm.workflow.task'
    _description = 'Workflow Task'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    contract_id = fields.Many2one('clm.contract', string='Contract', required=True, ondelete='cascade')
    active = fields.Boolean(
        string='Active', default=True,
        help='Tasks of an earlier review cycle are archived when a signed contract '
             'is amended, so the amendment is reviewed and approved again. They '
             'stay on record.'
    )
    rule_id = fields.Many2one('clm.workflow.rule', string='Originating Rule')
    name = fields.Char(string='Task Name', required=True)
    task_type = fields.Selection([
        ('approval', 'Approval'),
        ('review', 'Review'),
        ('signature', 'Signature')
    ], string='Task Type', required=True)
    original_assigned_user_id = fields.Many2one('res.users', string='Original Assignee', tracking=True)
    assigned_user_id = fields.Many2one('res.users', string='Current Assignee', tracking=True)
    is_required = fields.Boolean(string='Is Required', default=True)
    status = fields.Selection([
        ('pending', 'Pending'),
        ('in_progress', 'In Progress'),
        ('completed', 'Completed'),
        ('rejected', 'Rejected'),
        ('cancelled', 'Cancelled')
    ], string='Status', default='pending', tracking=True)
    due_date = fields.Datetime(string='Due Date')
    completion_date = fields.Datetime(string='Completion Date')

    escalation_level = fields.Selection([
        ('0', 'None'),
        ('1', 'Level 1'),
        ('2', 'Level 2'),
        ('3', 'Level 3'),
    ], string='Escalation Level', default='0')
    escalation_date = fields.Date(string='Last Escalation Date')
    is_overdue = fields.Boolean(string='Is Overdue', compute='_compute_is_overdue', store=True)

    @api.depends('due_date', 'status')
    def _compute_is_overdue(self):
        today = fields.Date.today()
        for task in self:
            task.is_overdue = (
                task.due_date and task.due_date.date() < today
                and task.status not in ('completed', 'cancelled')
            )

    @api.model_create_multi
    def create(self, vals_list):
        tasks = super().create(vals_list)
        tasks._notify_task_assigned()
        return tasks

    def _notify_task_assigned(self):
        notify = self.env['clm.mail.notify']
        for task in self:
            if not task.assigned_user_id or not task.assigned_user_id.email:
                continue
            if task.status in _TERMINAL_STATES:
                continue
            notify.send(
                task,
                'clm.mail_template_clm_task_assigned',
                email_to=task.assigned_user_id.email,
            )

    def _check_not_terminal(self):
        for task in self:
            if task.status in _TERMINAL_STATES:
                raise UserError(
                    f"Cannot act on task '{task.id}': it is already {task.status}."
                )

    def _action_open_contract(self):
        """Reload the contract form after a task changes contract.state.

        Approving / rejecting a task can advance (or roll back) the contract.
        Without an explicit return action the open form keeps a stale ``state``,
        so header buttons like Approve stay visible until a manual refresh.
        """
        contracts = self.mapped('contract_id')
        if len(contracts) != 1:
            return {'type': 'ir.actions.client', 'tag': 'soft_reload'}
        return {
            'type': 'ir.actions.act_window',
            'name': contracts.display_name,
            'res_model': 'clm.contract',
            'res_id': contracts.id,
            'view_mode': 'form',
            'views': [(False, 'form')],
            'target': 'current',
        }

    def action_approve(self):
        self._check_not_terminal()
        for task in self:
            if self.env.user.id != task.assigned_user_id.id and not self.env.user.has_group('clm.group_clm_legal_admin'):
                raise AccessError("Only the assignee or a Legal Admin can approve.")
        self.write({
            'status': 'completed',
            'completion_date': fields.Datetime.now(),
        })
        for task in self:
            task.contract_id._check_and_advance_workflow()
            task._notify_task_result('approved')
        return self._action_open_contract()

    def action_reject(self):
        self._check_not_terminal()
        for task in self:
            # Same rule as approving: a task is decided by its assignee (or a
            # Legal Admin), not by anyone who can merely open it.
            if self.env.user.id != task.assigned_user_id.id and not self.env.user.has_group('clm.group_clm_legal_admin'):
                raise AccessError("Only the assignee or a Legal Admin can reject.")
        self.write({
            'status': 'rejected',
            'completion_date': fields.Datetime.now(),
        })
        # Rejection should return the contract to draft (or input-required state)
        for task in self:
            task.contract_id._handle_workflow_rejection(task)
            task._notify_task_result('rejected')
        return self._action_open_contract()

    def _notify_task_result(self, result):
        """Email business/legal owners when a task is approved or rejected."""
        self.ensure_one()
        notify = self.env['clm.mail.notify']
        contract = self.contract_id
        users = contract.user_id | contract.legal_owner_id
        email_to = notify.emails_for_users(users)
        if not email_to:
            email_to = notify.emails_for_group('clm.group_clm_legal_admin')
        if not email_to:
            return
        template = (
            'clm.mail_template_clm_task_approved'
            if result == 'approved'
            else 'clm.mail_template_clm_task_rejected'
        )
        notify.send(self, template, email_to=email_to)

    def action_delegate(self, new_user_id, reason=None):
        self._check_not_terminal()
        for task in self:
            if self.env.user.id != task.assigned_user_id.id and not self.env.user.has_group('clm.group_clm_legal_admin'):
                raise AccessError("Only the assignee or a Legal Admin can delegate.")
            self.env['clm.workflow.delegation'].create({
                'task_id': task.id,
                'original_user_id': task.assigned_user_id.id,
                'delegated_to_id': new_user_id,
                'delegated_by_id': self.env.uid,
                'reason': reason,
            })
            task.assigned_user_id = new_user_id
            task.message_post(
                body=f"Task delegated to user ID {new_user_id}. Reason: {reason or 'N/A'}"
            )
            task._notify_task_assigned()

    def action_open_delegate_wizard(self):
        self.ensure_one()
        return {
            'name': 'Delegate Task',
            'type': 'ir.actions.act_window',
            'res_model': 'clm.delegate.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_task_id': self.id},
        }
