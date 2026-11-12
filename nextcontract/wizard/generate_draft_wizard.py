# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models, fields
from odoo.exceptions import UserError


class NextcontractGenerateDraftWizard(models.TransientModel):
    _name = 'nextcontract.generate.draft.wizard'
    _description = 'Generate Contract Draft'

    contract_id = fields.Many2one('nextcontract.contract', string='Contract', required=True)
    renderer = fields.Selection([
        ('plain_text', 'Plain Text Preview'),
        ('docx', 'Microsoft Word (DOCX)'),
        ('pdf', 'PDF'),
    ], string='Output Format', default='plain_text', required=True)

    def action_generate(self):
        self.ensure_one()
        if not self.contract_id.template_id:
            raise UserError('Please select a published template on the contract first.')
        request = self.contract_id._generate_draft(renderer=self.renderer)
        return {
            'type': 'ir.actions.act_window',
            'name': 'Generated Draft',
            'res_model': 'nextcontract.assembly.request',
            'res_id': request.id,
            'view_mode': 'form',
            'target': 'current',
        }
