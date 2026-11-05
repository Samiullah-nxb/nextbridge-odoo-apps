# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models, fields


class ClmContractType(models.Model):
    _name = 'clm.contract.type'
    _description = 'Contract Type'
    _order = 'sequence, name'
    _rec_name = 'name'

    name = fields.Char(string='Name', required=True, translate=True)
    code = fields.Char(
        string='Code', required=True,
        help='Short unique code, e.g. nda, services, supply'
    )
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    description = fields.Text(string='Description')

    company_id = fields.Many2one(
        'res.company', string='Company',
        default=lambda self: self.env.company,
        help='Leave empty for global (shared across all companies).'
    )

    _unique_code = models.Constraint('unique(code)', 'Contract type code must be unique.')
