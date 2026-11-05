# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models, fields

class ClmContractVersionMigration(models.Model):
    _inherit = 'clm.contract.version'

    execution_method = fields.Selection(
        selection_add=[('legacy_migration', 'Legacy Migration')],
        ondelete={'legacy_migration': 'cascade'}
    )
