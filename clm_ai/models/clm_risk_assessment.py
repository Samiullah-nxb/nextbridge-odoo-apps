# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models


class ClmRiskAssessment(models.Model):
    _inherit = 'clm.risk.assessment'

    def _add_ai_risk_items(self):
        super()._add_ai_risk_items()
        ai_factor = self.env.ref('clm_ai.risk_factor_ai_deviation', raise_if_not_found=False)
        if not ai_factor:
            return
        deviations = self.env['clm.clause.deviation'].search([
            ('version_id.contract_id', '=', self.contract_id.id),
            ('version_id.status', '=', 'active'),
            ('confirmed_risk_level', 'in', ['high', 'critical']),
        ])
        for dev in deviations:
            self.env['clm.risk.assessment.item'].create({
                'assessment_id': self.id,
                'factor_id': ai_factor.id,
                'evidence': dev.clause_title or dev.clause_id.name or 'Clause Deviation',
                'comments': f"AI Risk Level: {dev.ai_risk_level}",
            })
