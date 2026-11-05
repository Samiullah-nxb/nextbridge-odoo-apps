# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models, fields


class ClmWorkflowRule(models.Model):
    """
    Configuration model that defines which workflow tasks should be automatically
    generated for a contract based on its attributes.

    The workflow engine evaluates all active rules against a contract when tasks
    need to be generated (e.g. on contract creation or stage transition).
    """
    _name = 'clm.workflow.rule'
    _description = 'Workflow Rule'
    _order = 'sequence asc'

    name = fields.Char(string='Rule Name', required=True)
    sequence = fields.Integer(string='Sequence', default=10)
    active = fields.Boolean(string='Active', default=True)

    # Conditions — all non-False conditions must match for the rule to apply
    stage_trigger = fields.Selection([
        ('in_review', 'In Review'),
        ('awaiting_business', 'Awaiting Business'),
        ('awaiting_finance', 'Awaiting Finance'),
        ('awaiting_procurement', 'Awaiting Procurement'),
        ('awaiting_compliance', 'Awaiting Compliance'),
        ('internal_review_done', 'Internal Review Done'),
        ('in_negotiation', 'In Negotiation'),
        ('approved', 'Approved'),
    ], string='Trigger Stage', default='in_review', required=True, help='The contract stage where these tasks are required.')
    contract_type_id = fields.Many2one('clm.contract.type', string='Contract Type Filter', help='Leave empty to apply to all contract types.')
    min_contract_value = fields.Monetary(
        string='Min Contract Value',
        currency_field='currency_id',
        help='Apply rule only when the contract value is >= this amount. 0 = no minimum.'
    )
    currency_id = fields.Many2one('res.currency', string='Currency')

    # Task to create
    task_type = fields.Selection([
        ('approval', 'Approval'),
        ('review', 'Review'),
        ('signature', 'Signature'),
    ], string='Task Type', required=True)
    task_name = fields.Char(string='Task Name', required=True)
    is_required = fields.Boolean(string='Task Is Required', default=True)
    assign_to_legal_admin = fields.Boolean(
        string='Auto-assign to Legal Admin',
        default=True,
        help='If enabled, the first Legal Admin user is set as assignee on task creation.'
    )

    def matches(self, contract, ignore_stage=False):
        """Return True if this rule applies to the given clm.contract record.

        ``ignore_stage`` evaluates only the contract-type / value conditions.
        """
        self.ensure_one()
        if not ignore_stage and self.stage_trigger and contract.state != self.stage_trigger:
            return False
        if self.contract_type_id and contract.contract_type_id != self.contract_type_id:
            return False
        if self.min_contract_value and (contract.contract_value or 0) < self.min_contract_value:
            return False
        return True
