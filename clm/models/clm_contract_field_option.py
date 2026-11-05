# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models, fields


class ClmContractFieldOption(models.Model):
    """One allowed value for a 'selection' field definition.

    'code' is what the AI returns and what is stored; 'name' is what users see.
    Keeping them separate means renaming a label never invalidates stored data.
    """
    _name = 'clm.contract.field.option'
    _description = 'Contract Field Option'
    _order = 'sequence, name'

    definition_id = fields.Many2one(
        'clm.contract.field.definition', string='Definition',
        required=True, ondelete='cascade', index=True
    )
    name = fields.Char(string='Name', required=True, translate=True)
    code = fields.Char(string='Code', required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    _unique_code_per_definition = models.Constraint('unique(definition_id, code)', 'Option code must be unique within its field definition.')
