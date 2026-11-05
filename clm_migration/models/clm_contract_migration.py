# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models, fields

class ClmContractMigration(models.Model):
    _inherit = 'clm.contract'

    source = fields.Selection(
        selection_add=[('migration', 'Legacy Migration')],
        ondelete={'migration': 'set default'}
    )
    is_legacy_contract = fields.Boolean('Legacy Contract', readonly=True, copy=False)
    migration_batch_id = fields.Many2one('clm.migration.batch', string='Migration Batch', readonly=True, copy=False)
    migration_record_id = fields.Many2one('clm.migration.record', string='Migration Record', readonly=True, copy=False)
    legacy_execution_date = fields.Date('Legacy Execution Date', readonly=True, copy=False)
    legacy_source_reference = fields.Char('Legacy Source Reference', readonly=True, copy=False)
    legacy_contract_number = fields.Char('Legacy Contract Number', readonly=True, copy=False)
