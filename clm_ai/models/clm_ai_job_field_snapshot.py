# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models, fields


class ClmAiJobFieldSnapshot(models.Model):
    """The field configuration a job actually ran against.

    Field definitions are editable, so pointing at them from a historical job
    does not tell you what the model was asked for. This table freezes the
    configuration at dispatch, which is what makes "extracted using
    definition X" reproducible six months later.
    """
    _name = 'clm.ai.job.field.snapshot'
    _description = 'AI Job Field Configuration Snapshot'
    _order = 'sequence, id'

    job_id = fields.Many2one(
        'clm.ai.job', string='AI Job',
        required=True, ondelete='cascade', index=True
    )
    definition_id = fields.Many2one(
        'clm.contract.field.definition', string='Definition',
        ondelete='set null',
        help='Reference only. The frozen values below are authoritative.'
    )
    definition_code = fields.Char(string='Code', required=True)
    definition_name = fields.Char(string='Name', required=True)
    value_type = fields.Char(string='Value Type', required=True)
    ai_hint = fields.Text(string='AI Hint')
    option_codes = fields.Char(
        string='Option Codes',
        help='Comma-separated vocabulary offered to the model.'
    )
    is_core = fields.Boolean(
        string='Core Field',
        help='True for allowlisted columns on clm.contract.'
    )
    sequence = fields.Integer(default=10)
