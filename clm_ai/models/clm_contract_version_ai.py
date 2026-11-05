# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models, fields


class ClmContractVersion(models.Model):
    _inherit = 'clm.contract.version'

    ai_job_ids = fields.One2many('clm.ai.job', 'contract_version_id', string='AI Jobs')

    def action_run_ai_extraction(self):
        """
        Manually trigger an AI extraction job for this version (Phase 6).
        Opens the wizard to select analysis type and provider.
        """
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Extract via AI',
            'res_model': 'clm.ai.job.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_contract_version_id': self.id,
            }
        }
