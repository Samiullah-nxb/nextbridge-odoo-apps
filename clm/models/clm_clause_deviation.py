# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models, fields


class ClmClauseDeviation(models.Model):
    """
    Records a manual deviation classification for a clause within a contract version.

    Phase 3: Legal manually classifies each negotiated clause.
    Phase 4+: The comparison engine will suggest the classification; Legal confirms.

    Structure:
        clm.contract.version
            └── clause_deviation_ids
                    ├── clause reference (optional — may not be in library)
                    ├── deviation_type  (manual in Phase 3)
                    └── rationale
    """
    _name = 'clm.clause.deviation'
    _description = 'Clause Deviation'
    _rec_name = 'clause_title'

    version_id = fields.Many2one(
        'clm.contract.version', string='Contract Version',
        required=True, ondelete='cascade'
    )
    clause_id = fields.Many2one(
        'clm.clause', string='Reference Clause', ondelete='set null',
        help='The standard clause this deviation is measured against.'
    )
    clause_title = fields.Char(
        string='Clause Title',
        help='Free-text title when the clause is not in the library.'
    )
    deviation_type = fields.Selection([
        ('standard', 'Standard'),
        ('approved_alternative', 'Approved Alternative'),
        ('modified', 'Modified'),
        ('non_standard', 'Non-standard'),
        ('rejected', 'Rejected'),
    ], string='Deviation Type', required=True, default='standard')

    # M-08: Status field — base model owns it, default 'confirmed' for manual entries.
    # clm_ai extends this model via _inherit and sets default='pending' for AI deviations.
    status = fields.Selection([
        ('pending', 'Pending Review'),
        ('confirmed', 'Confirmed'),
        ('rejected', 'Rejected'),
    ], string='Status', default='confirmed', required=True)

    rationale = fields.Text(string='Deviation Rationale')
    classified_by_id = fields.Many2one(
        'res.users', string='Classified By', default=lambda self: self.env.uid
    )
    classification_date = fields.Datetime(
        string='Classification Date', default=fields.Datetime.now
    )
