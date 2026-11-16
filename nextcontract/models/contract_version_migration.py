# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models, fields

class NextcontractContractVersionMigration(models.Model):
    _inherit = 'nextcontract.contract.version'

    execution_method = fields.Selection(
        selection_add=[('legacy_migration', 'Legacy Migration')],
        ondelete={'legacy_migration': 'cascade'}
    )
