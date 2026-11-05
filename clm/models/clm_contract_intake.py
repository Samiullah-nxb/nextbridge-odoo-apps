# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models, fields, api


class ClmContract(models.Model):
    """Intake-originated data carried onto the contract.

    These fields hold what the *request* asked for. They are deliberately kept
    apart from the lifecycle fields owned by clm_execution (``signing_date``,
    ``execution_date``), which stay empty until the contract is actually signed
    or executed.
    """
    _inherit = 'clm.contract'

    # ─── Origin / Audit Trail ────────────────────────────────────────────────
    request_ids = fields.One2many(
        'clm.contract.request', 'contract_id', string='Originating Requests',
        readonly=True,
        help='Intake requests that were converted into this contract.'
    )
    request_id = fields.Many2one(
        'clm.contract.request', string='Originating Request',
        compute='_compute_request_id', store=True, index=True,
        help='Intake request this contract was converted from.'
    )

    # ─── Intake Metadata ─────────────────────────────────────────────────────
    expected_execution_date = fields.Date(
        string='Expected Execution Date', tracking=True,
        help='Signature date planned at intake. The date the contract was '
             'really executed is recorded separately on Execution Date.'
    )
    required_legal_action = fields.Selection([
        ('new_contract', 'New Contract'),
        ('amendment', 'Amendment'),
        ('renewal', 'Renewal'),
        ('termination', 'Termination'),
    ], string='Legal Action Needed', tracking=True)
    urgency = fields.Selection([
        ('routine', 'Routine'),
        ('standard', 'Standard'),
        ('urgent', 'Urgent'),
        ('critical', 'Critical'),
    ], string='Urgency', tracking=True)

    # ─── Computed ────────────────────────────────────────────────────────────
    @api.depends('request_ids')
    def _compute_request_id(self):
        for contract in self:
            contract.request_id = contract.request_ids[:1]
