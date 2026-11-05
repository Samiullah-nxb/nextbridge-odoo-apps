# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models, fields


class ClmContractFieldDefinition(models.Model):
    """Declares a contract data point that is not a column on clm.contract.

    Definitions are configuration, not AI artifacts: values may be produced by
    manual entry, migration, import, or AI extraction. They live in the core
    app so that it never has to depend on clm_ai.
    """
    _name = 'clm.contract.field.definition'
    _description = 'Contract Field Definition'
    _inherit = ['mail.thread']
    _order = 'sequence, name'

    name = fields.Char(string='Name', required=True, translate=True)
    code = fields.Char(
        string='Code', required=True,
        help='Machine-facing identifier. Used as the AI schema field name.'
    )
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    contract_type_ids = fields.Many2many(
        'clm.contract.type', string='Contract Types',
        help='Leave empty to apply this field to every contract type.'
    )

    value_type = fields.Selection([
        ('char', 'Text (short)'),
        ('text', 'Text (long)'),
        ('date', 'Date'),
        ('float', 'Number'),
        ('monetary', 'Monetary'),
        ('boolean', 'Yes/No'),
        ('selection', 'Selection'),
    ], string='Value Type', required=True, default='char', tracking=True)

    required = fields.Boolean(
        string='Required', tracking=True,
        help='Blocks contract-level review confirmation while empty. Does not '
             'constrain draft, intake, or migration flows.'
    )
    confidential = fields.Boolean(
        string='Confidential', tracking=True,
        help='Restricts reads of this field\'s values to Legal and above.'
    )

    ai_extractable = fields.Boolean(string='AI Extractable', default=True, tracking=True)
    ai_hint = fields.Text(
        string='AI Hint', tracking=True,
        help='Instruction describing exactly what to extract. Supplied to the '
             'model as field metadata, never as a prompt instruction.'
    )

    option_ids = fields.One2many(
        'clm.contract.field.option', 'definition_id', string='Options'
    )

    _unique_code = models.Constraint('unique(code)', 'Field definition code must be unique.')

    def applies_to_type(self, contract_type):
        """True when this definition is in scope for the given contract type.

        An empty contract_type_ids means the definition is global.
        """
        self.ensure_one()
        if not self.contract_type_ids:
            return True
        return contract_type.id in self.contract_type_ids.ids
