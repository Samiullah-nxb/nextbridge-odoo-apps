# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models, fields

class ClmAiJobWizard(models.TransientModel):
    _name = 'clm.ai.job.wizard'
    _description = 'AI Analysis Wizard'

    contract_version_id = fields.Many2one('clm.contract.version', required=True)
    
    analysis_type = fields.Selection([
        ('field_extraction', 'Field Extraction'),
        ('clause_analysis', 'Clause Analysis'),
        ('full', 'Full Analysis (Fields + Clauses)')
    ], string='Analysis Type', default='full', required=True)
    
    provider = fields.Selection([
        ('mock', 'Mock (Testing)'),
        ('gemini', 'Google Gemini')
    ], string='AI Provider', default='gemini', required=True)

    def action_create_job(self):
        self.ensure_one()
        
        # Explicitly validate access to the underlying contract version first
        self.contract_version_id.contract_id.check_access_rule('read')
        
        job = self.env['clm.ai.job'].sudo().create({
            'contract_version_id': self.contract_version_id.id,
            'analysis_type': self.analysis_type,
            'provider': self.provider
        })
        
        # We no longer run the job synchronously here.
        # It remains in 'pending' status and is picked up by the ir.cron worker pool.
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'AI Analysis Job',
            'res_model': 'clm.ai.job',
            'res_id': job.id,
            'view_mode': 'form',
            'target': 'current',
        }
