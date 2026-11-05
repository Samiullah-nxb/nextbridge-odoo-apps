# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models, fields

class ClmMigrationForceImportWizard(models.TransientModel):
    _name = 'clm.migration.force.import.wizard'
    _description = 'Force Import Reason Wizard'

    record_id = fields.Many2one('clm.migration.record', required=True)
    reason = fields.Text('Reason for Force Import', required=True, help="Why is this not a duplicate?")
    
    def action_confirm(self):
        self.ensure_one()
        self.record_id.state = 'force_imported'
        self.record_id.force_import_reason = self.reason
        return {'type': 'ir.actions.act_window_close'}
