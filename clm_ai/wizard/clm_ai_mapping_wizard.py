# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models, fields, api
from odoo.exceptions import UserError

class ClmAiMappingWizard(models.TransientModel):
    _name = 'clm.ai.mapping.wizard'
    _description = 'AI Mapping Wizard'

    suggestion_id = fields.Many2one('clm.ai.field.suggestion', string='Suggestion', required=True, ondelete='cascade')
    field_name = fields.Char(related='suggestion_id.field_name', string='Field')
    suggested_value = fields.Char(related='suggestion_id.suggested_value', string='AI Extracted Value')
    instruction_text = fields.Char(string='Instruction', compute='_compute_instruction_text')
    
    # We dynamically show a many2one depending on the target model
    partner_id = fields.Many2one('res.partner', string='Select Partner')
    contract_type_id = fields.Many2one('clm.contract.type', string='Select Contract Type')
    currency_id = fields.Many2one('res.currency', string='Select Currency')

    @api.depends('field_name')
    def _compute_instruction_text(self):
        for record in self:
            record.instruction_text = "The AI extracted a value that doesn't match an existing %s. Please review and resolve." % (
                record.field_name.replace('_id', '').replace('_', ' ').title() if record.field_name else 'record'
            )


    def action_confirm_mapping(self):
        self.ensure_one()
        
        resolved_id = False
        resolved_model = False
        
        # Determine the mapped field logic based on the original mapping dictionary
        # field might be 'partner_id' or 'partner' depending on how the AI extraction outputs it
        field_to_check = self.field_name.replace('_id', '') + '_id' 
        
        if field_to_check == 'partner_id':
            resolved_id = self.partner_id.id
            resolved_model = 'res.partner'
        elif field_to_check == 'contract_type_id':
            resolved_id = self.contract_type_id.id
            resolved_model = 'clm.contract.type'
        elif field_to_check == 'currency_id':
            resolved_id = self.currency_id.id
            resolved_model = 'res.currency'
            
        if not resolved_id:
            raise UserError("You must select a matching record before confirming.")
            
        # Update the suggestion
        self.suggestion_id.write({
            'resolved_record_id': resolved_id,
            'resolved_model': resolved_model,
            'status': 'pending' # Revert to pending so it can be approved
        })
        
        # Instantly approve it
        self.suggestion_id.action_approve()
        
        return {'type': 'ir.actions.act_window_close'}
