# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models, fields


class ClmTemplateVariable(models.Model):
    """
    Defines a named variable slot within a template (e.g. {{partner_name}}).

    Variables are resolved against the clm.contract record during assembly.
    Phase 4 may add computed/formula variables and external data sources.
    """
    _name = 'clm.template.variable'
    _description = 'Template Variable'
    _order = 'template_id, sequence asc'

    template_id = fields.Many2one(
        'clm.template', string='Template', required=True, ondelete='cascade'
    )
    name = fields.Char(
        string='Variable Name', required=True,
        help='Placeholder key used in template content, e.g. partner_name'
    )
    label = fields.Char(string='Display Label')
    field_path = fields.Char(
        string='Contract Field Path',
        help='Dot-notation path on clm.contract to resolve this variable, '
             'e.g. "partner_id.name" or "effective_date".'
    )
    default_value = fields.Char(
        string='Default Value',
        help='Used when the contract field is empty or not applicable.'
    )
    required = fields.Boolean(string='Required', default=False)
    sequence = fields.Integer(string='Sequence', default=10)
    notes = fields.Text(string='Notes')
