# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import api, models, fields
from odoo.exceptions import ValidationError

class ClmContractVersionExecution(models.Model):
    _inherit = 'clm.contract.version'

    version_type = fields.Selection(
        selection_add=[('executed', 'Executed')],
        ondelete={'executed': 'cascade'}
    )
    
    execution_method = fields.Selection([
        ('odoo_sign', 'Odoo Sign'),
        ('manual', 'Manual Signature')
    ], string='Execution Method', readonly=True)
    
    @api.constrains('source_version_id', 'contract_id')
    def _check_source_within_contract(self):
        for version in self.sudo():
            source = version.source_version_id
            if source and source.contract_id != version.contract_id:
                raise ValidationError(
                    "The source draft of an executed version must belong to the same contract."
                )

    source_version_id = fields.Many2one('clm.contract.version', string='Source Draft Version', readonly=True,
                                        help="The final signature draft that this executed version is based on.")
