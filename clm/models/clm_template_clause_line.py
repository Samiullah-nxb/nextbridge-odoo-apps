# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models, fields


class ClmTemplateClauseLine(models.Model):
    """
    Ordered junction between clm.template and clm.clause (PRD §23).

    Preserves clause sequence, section grouping, required/optional flags,
    and conditional-section metadata for the Phase 4 assembly engine.
    """
    _name = 'clm.template.clause.line'
    _description = 'Template Clause Line'
    _order = 'template_id, sequence asc'

    template_id = fields.Many2one(
        'clm.template', string='Template', required=True, ondelete='cascade'
    )
    clause_id = fields.Many2one(
        'clm.clause', string='Clause', required=True, ondelete='restrict'
    )
    sequence = fields.Integer(string='Sequence', default=10)
    section = fields.Char(
        string='Section',
        help='Section heading this clause belongs to (e.g. "Confidentiality").'
    )
    required = fields.Boolean(
        string='Required', default=True,
        help='Required clauses always appear in the assembled document.'
    )
    enabled = fields.Boolean(
        string='Enabled', default=True,
        help='Disabled clauses are excluded from draft assembly.'
    )
    condition_enabled = fields.Boolean(
        string='Conditional', default=False,
        help='If checked, this clause is only included if the condition domain is met.'
    )
    condition_domain = fields.Char(
        string='Condition Domain',
        help='Odoo domain string evaluated against the contract. '
             'Example: [("contract_type", "=", "nda")]'
    )
    condition_description = fields.Char(
        string='Condition Description',
        help='Human-readable description of when this clause is included.'
    )
    label = fields.Char(
        string='Label Override',
        help='Optional display label overriding the clause title in the assembled document.'
    )
    notes = fields.Text(string='Internal Notes')
