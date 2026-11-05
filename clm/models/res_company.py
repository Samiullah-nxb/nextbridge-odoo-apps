# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models, fields, api


class ResCompany(models.Model):
    """Cloud AI processing consent, scoped per company.

    Stored here rather than in ir.config_parameter because config parameters
    are global: one company may approve a cloud provider while another
    prohibits any data egress. The engine factory reads this field for the
    company that owns the contract being processed.
    """
    _inherit = 'res.company'

    clm_ai_cloud_processing_acknowledged = fields.Boolean(
        string='Cloud AI Processing Acknowledged',
        default=False, copy=False,
        help='When enabled, contract content from this company may be '
             'transmitted to the configured third-party AI provider.'
    )
    clm_ai_cloud_consent_user_id = fields.Many2one(
        'res.users', string='Consent Given By', readonly=True, copy=False
    )
    clm_ai_cloud_consent_date = fields.Datetime(
        string='Consent Given On', readonly=True, copy=False
    )

    def acknowledge_cloud_ai_processing(self):
        """Record an explicit, attributable opt-in for this company."""
        for company in self:
            company.write({
                'clm_ai_cloud_processing_acknowledged': True,
                'clm_ai_cloud_consent_user_id': self.env.user.id,
                'clm_ai_cloud_consent_date': fields.Datetime.now(),
            })
        return True

    def revoke_cloud_ai_processing(self):
        """Withdraw consent going forward.

        The consenting user and date are deliberately preserved: withdrawal
        blocks future jobs and must not rewrite the record of what was
        permitted at the time earlier extractions ran.
        """
        for company in self:
            company.clm_ai_cloud_processing_acknowledged = False
        return True
