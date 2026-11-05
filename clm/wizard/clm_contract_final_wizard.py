# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models
from odoo.exceptions import UserError


class ClmContractFinalWizard(models.TransientModel):
    _name = 'clm.contract.final.wizard'
    _description = 'Final Contract Viewer'

    contract_id = fields.Many2one('clm.contract', required=True, readonly=True)
    contract_ref = fields.Char(related='contract_id.contract_ref', readonly=True)
    contract_name = fields.Char(related='contract_id.name', readonly=True)
    final_contract_kind = fields.Selection(related='contract_id.final_contract_kind', readonly=True)
    final_contract_status = fields.Char(related='contract_id.final_contract_status', readonly=True)
    signed_artifact_available = fields.Boolean(related='contract_id.signed_artifact_available', readonly=True)
    signed_artifact_is_pdf = fields.Boolean(related='contract_id.signed_artifact_is_pdf', readonly=True)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        active_id = self.env.context.get('active_id')
        if active_id and self.env.context.get('active_model') == 'clm.contract':
            contract = self.env['clm.contract'].browse(active_id)
            if not contract.final_contract_available:
                raise UserError('Final contract is not available for this contract yet.')
            res['contract_id'] = contract.id
        return res

    def _contract(self):
        self.ensure_one()
        return self.contract_id

    def action_view_pdf(self):
        return self._contract().action_view_final_pdf()

    def action_download_pdf(self):
        return self._contract().action_download_final_pdf()

    def action_download_docx(self):
        return self._contract().action_download_final_docx()

    def action_view_signed(self):
        """Open the authoritative Odoo Sign / uploaded signed PDF."""
        return self._contract().action_view_signed_artifact()

    def action_download_signed(self):
        return self._contract().action_download_signed_document()
