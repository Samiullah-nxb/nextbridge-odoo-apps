# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models, fields, api
from odoo.exceptions import ValidationError

# Maps a definition's value_type to the column that must hold its value.
# 'monetary' shares value_float: Odoo monetary values are floats paired with a
# currency, and the contract already carries currency_id.
VALUE_COLUMN_BY_TYPE = {
    'char': 'value_char',
    'text': 'value_text',
    'date': 'value_date',
    'float': 'value_float',
    'monetary': 'value_float',
    'boolean': 'value_bool',
    'selection': 'value_option_id',
}

_ALL_VALUE_COLUMNS = (
    'value_char', 'value_text', 'value_date',
    'value_float', 'value_bool', 'value_option_id',
)


class ClmContractFieldValue(models.Model):
    """The value of one field definition on one contract.

    Typed columns rather than a single text blob, so values stay filterable,
    sortable and groupable in list and pivot views.
    """
    _name = 'clm.contract.field.value'
    _description = 'Contract Field Value'
    _order = 'definition_sequence, id'

    contract_id = fields.Many2one(
        'clm.contract', string='Contract',
        required=True, ondelete='cascade', index=True
    )
    definition_id = fields.Many2one(
        'clm.contract.field.definition', string='Field',
        required=True, ondelete='restrict', index=True
    )
    definition_sequence = fields.Integer(
        related='definition_id.sequence', store=True, index=True
    )
    company_id = fields.Many2one(
        related='contract_id.company_id', store=True, index=True
    )
    is_confidential = fields.Boolean(
        related='definition_id.confidential', store=True, index=True,
        help='Stored so record rules can filter on it without a join.'
    )

    value_char = fields.Char(string='Text Value')
    value_text = fields.Text(string='Long Text Value')
    value_date = fields.Date(string='Date Value')
    value_float = fields.Float(string='Numeric Value')
    value_bool = fields.Boolean(string='Yes/No Value')
    value_option_id = fields.Many2one(
        'clm.contract.field.option', string='Selected Option', ondelete='restrict'
    )

    display_value = fields.Char(
        string='Value', compute='_compute_display_value', store=True
    )

    _unique_definition_per_contract = models.Constraint(
        'unique(contract_id, definition_id)',
        'This field already has a value on this contract.',
    )

    @api.depends('value_char', 'value_text', 'value_date', 'value_float',
                 'value_bool', 'value_option_id', 'definition_id.value_type')
    def _compute_display_value(self):
        for record in self:
            value_type = record.definition_id.value_type
            if value_type == 'selection':
                record.display_value = record.value_option_id.name or False
            elif value_type == 'boolean':
                record.display_value = 'Yes' if record.value_bool else 'No'
            elif value_type == 'date':
                record.display_value = (
                    fields.Date.to_string(record.value_date) if record.value_date else False
                )
            elif value_type in ('float', 'monetary'):
                record.display_value = (
                    str(record.value_float) if record.value_float else False
                )
            elif value_type == 'text':
                record.display_value = record.value_text or False
            else:
                record.display_value = record.value_char or False

    @api.constrains('definition_id', 'contract_id', *_ALL_VALUE_COLUMNS)
    def _check_value_integrity(self):
        """Enforce every integrity rule regardless of who is writing.

        Values arrive from AI, migration, manual entry, import and RPC, so this
        belongs in the model rather than in the review UI.
        """
        for record in self:
            definition = record.definition_id
            expected_column = VALUE_COLUMN_BY_TYPE[definition.value_type]

            populated = [column for column in _ALL_VALUE_COLUMNS if record[column]]
            if len(populated) > 1:
                raise ValidationError(
                    "Field '%s' has more than one value populated (%s). Exactly "
                    "one value column may be set."
                    % (definition.name, ', '.join(populated))
                )
            if populated and populated[0] != expected_column:
                raise ValidationError(
                    "Field '%s' is of type '%s' and must store its value in '%s', "
                    "but '%s' was populated."
                    % (definition.name, definition.value_type,
                       expected_column, populated[0])
                )
            if record.value_option_id and \
                    record.value_option_id.definition_id != definition:
                raise ValidationError(
                    "Option '%s' does not belong to field '%s'."
                    % (record.value_option_id.name, definition.name)
                )
            if not definition.applies_to_type(record.contract_id.contract_type_id):
                raise ValidationError(
                    "Field '%s' does not apply to contract type '%s'."
                    % (definition.name, record.contract_id.contract_type_id.name)
                )

    def set_typed_value(self, raw_value):
        """Write raw_value into the column matching the definition's type.

        For 'selection', raw_value is an option *code*; the matching option
        record is resolved and stored. For 'boolean', raw_value commonly
        arrives as a string (an AI suggestion's suggested_value is always
        Char) -- Odoo's Boolean field coerces via bool(value), so the
        string 'False' would otherwise write True. Parsed explicitly here
        rather than upstream, so every caller gets the same behavior
        through this one choke point.
        """
        self.ensure_one()
        definition = self.definition_id
        column = VALUE_COLUMN_BY_TYPE[definition.value_type]

        if definition.value_type == 'selection':
            option = self.env['clm.contract.field.option'].search([
                ('definition_id', '=', definition.id),
                ('code', '=', raw_value),
            ], limit=1)
            if not option:
                raise ValidationError(
                    "'%s' is not a valid option for field '%s'."
                    % (raw_value, definition.name)
                )
            return self.write({column: option.id})

        if definition.value_type == 'boolean':
            if isinstance(raw_value, str):
                raw_value = raw_value.strip().lower() in ('true', '1', 'yes')
            return self.write({column: bool(raw_value)})

        return self.write({column: raw_value})
