# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models, fields

class ClmMigrationAiWizard(models.TransientModel):
    _name = 'clm.migration.ai.wizard'
    _description = 'Migration AI Extraction Wizard'

    scope = fields.Selection([
        ('all', 'All Migrated Contracts in Batch'),
        ('selected', 'Selected Contracts'),
        ('missing', 'Only Contracts with Missing Fields')
    ], string='Extraction Scope', default='missing', required=True)
    
    batch_id = fields.Many2one('clm.migration.batch')
    
    def action_run_ai(self):
        self.ensure_one()
        
        contracts = self.env['clm.contract']
        if self.scope == 'all':
            contracts = self.env['clm.contract'].search([('migration_batch_id', '=', self.batch_id.id)])
        elif self.scope == 'missing':
            contracts = self.env['clm.contract'].search([
                ('migration_batch_id', '=', self.batch_id.id),
                '|', '|',
                ('contract_value', '=', 0),
                ('effective_date', '=', False),
                ('expiry_date', '=', False)
            ])
            
        # Queue AI Jobs for extraction
        for contract in contracts:
            executed_version = contract.version_ids.filtered(lambda v: v.version_type == 'executed')[:1]
            self.env['clm.ai.job'].create({
                'contract_id': contract.id,
                'contract_version_id': executed_version.id if executed_version else False,
                'analysis_type': 'field_extraction',
                'provider': 'gemini',
                'status': 'pending'
            })
            
        return {'type': 'ir.actions.act_window_close'}
