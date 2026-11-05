# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models, fields

class ClmRiskFactor(models.Model):
    _name = 'clm.risk.factor'
    _description = 'Contract Risk Factor Configuration'

    name = fields.Char('Factor Name', required=True)
    source_type = fields.Selection([
        ('metadata', 'Contract Metadata'),
        ('ai_deviation', 'AI Clause Deviation'),
        ('manual', 'Manual Entry')
    ], required=True, default='manual')
    
    weight = fields.Integer('Weight (Score)', default=10, required=True, help="Impact on the total calculated score")
    default_rating = fields.Selection([
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
        ('critical', 'Critical')
    ], string='Default Impact Rating', default='medium')
    
    active = fields.Boolean(default=True)
