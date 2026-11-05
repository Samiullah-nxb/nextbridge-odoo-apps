# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models, fields, api


class ClmClauseDeviationAI(models.Model):
    """
    AI-specific extension of clm.clause.deviation.

    M-08: The base model (_name) is defined in clm.
    This module extends it via _inherit to add AI analysis fields.
    AI-created deviations default to status='pending' (vs 'confirmed'
    for manual entries from the base model).
    """
    _inherit = 'clm.clause.deviation'

    _unique_version_clause = models.Constraint('unique(version_id, clause_id)', 'A version can only have one deviation record per clause.')

    # ─── AI Job Link ─────────────────────────────────────────────────────────
    job_id = fields.Many2one(
        'clm.ai.job', string='AI Job',
        ondelete='cascade', index=True,
        help='The AI job that generated this deviation analysis.'
    )

    # ─── AI Suggestions ──────────────────────────────────────────────────────
    matched_text = fields.Text(
        string='Inbound Text',
        help='The text from the inbound document'
    )
    standard_text = fields.Html(
        string='Standard Text',
        related='clause_id.standard_wording',
        help='Our standard library wording'
    )

    ai_deviation_type = fields.Selection([
        ('standard', 'Standard'),
        ('approved_alternative', 'Approved Alternative'),
        ('modified', 'Modified'),
        ('non_standard', 'Non-Standard'),
        ('rejected', 'Rejected'),
    ], string='AI Classification')

    ai_risk_level = fields.Selection([
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
        ('critical', 'Critical'),
    ], string='AI Risk')

    ai_confidence = fields.Float(string='AI Confidence (%)')
    explanation = fields.Text(string='AI Explanation')

    # ─── Legal Confirmation (Authoritative) ──────────────────────────────────
    confirmed_deviation_type = fields.Selection([
        ('standard', 'Standard'),
        ('approved_alternative', 'Approved Alternative'),
        ('modified', 'Modified'),
        ('non_standard', 'Non-Standard'),
        ('rejected', 'Rejected'),
    ], string='Confirmed Classification')

    confirmed_risk_level = fields.Selection([
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
        ('critical', 'Critical'),
    ], string='Confirmed Risk')

    reviewed_by_id = fields.Many2one('res.users', string='Reviewed By')
    reviewed_at = fields.Datetime(string='Reviewed At')

    def action_confirm(self):
        """Confirm the authoritative values."""
        if not self.env.su and not self.env.user.has_group('clm.group_clm_legal'):
            from odoo.exceptions import AccessError
            raise AccessError("Only Legal Reviewers can approve or reject AI suggestions.")

        for deviation in self:
            if not deviation.confirmed_deviation_type:
                deviation.confirmed_deviation_type = deviation.ai_deviation_type
            if not deviation.confirmed_risk_level:
                deviation.confirmed_risk_level = deviation.ai_risk_level

            deviation.write({
                'status': 'confirmed',
                'reviewed_by_id': self.env.user.id,
                'reviewed_at': fields.Datetime.now(),
            })

    def action_reject(self):
        """Reject the AI deviation suggestion."""
        if not self.env.su and not self.env.user.has_group('clm.group_clm_legal'):
            from odoo.exceptions import AccessError
            raise AccessError("Only Legal Reviewers can approve or reject AI suggestions.")

        self.write({
            'status': 'rejected',
            'reviewed_by_id': self.env.user.id,
            'reviewed_at': fields.Datetime.now(),
        })

    def action_approve(self):
        """Alias for confirm to support unified UI workflows."""
        return self.action_confirm()
