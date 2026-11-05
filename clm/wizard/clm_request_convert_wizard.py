# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models, fields, api
from odoo.exceptions import UserError

class ClmRequestConvertWizard(models.TransientModel):
    _name = 'clm.request.convert.wizard'
    _description = 'Wizard to convert request to contract'

    request_id = fields.Many2one('clm.contract.request', string='Contract Request', required=True)

    # Pre-populated fields for confirmation/override
    name = fields.Char(string='Contract Title', required=True)
    contract_type_id = fields.Many2one('clm.contract.type', string='Contract Type', required=True)
    partner_id = fields.Many2one('res.partner', string='Counterparty')
    partner_signatory_id = fields.Many2one(
        'res.partner',
        string='Counterparty Signatory',
        domain="[('id', 'child_of', partner_id), ('is_company', '=', False)]",
        help='Person who will sign for the counterparty company.',
    )
    department_id = fields.Many2one('hr.department', string='Department')
    user_id = fields.Many2one('res.users', string='Business Owner', required=True)
    company_id = fields.Many2one('res.company', string='Company', required=True)
    template_id = fields.Many2one(
        'clm.template', string='Contract Template',
        domain=[('state', '=', 'published')],
    )
    currency_id = fields.Many2one('res.currency', string='Currency')
    contract_value = fields.Monetary(string='Contract Value', currency_field='currency_id')
    effective_date = fields.Date(
        string='Effective Date',
        help="Taken from the expected start date of the request."
    )
    expected_execution_date = fields.Date(
        string='Expected Execution Date',
        help="Planned signature date. The actual signing/execution date stays "
             "empty until the contract is really executed."
    )
    purpose = fields.Text(string='Purpose / Business Need')

    copy_attachments = fields.Boolean(
        string='Copy Supporting Documents',
        default=True,
        help="Copy attachments from the request to the new contract"
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        active_id = self._context.get('active_id')
        if active_id and self._context.get('active_model') == 'clm.contract.request':
            request_record = self.env['clm.contract.request'].browse(active_id)
            res.update({
                'request_id': request_record.id,
                'name': request_record.name,
                'contract_type_id': request_record.contract_type_id.id,
                'partner_id': request_record.partner_id.id,
                'partner_signatory_id': request_record.partner_signatory_id.id,
                'department_id': request_record.department_id.id,
                'user_id': request_record.business_owner_id.id or request_record.requester_id.id,
                'company_id': request_record.company_id.id,
                'template_id': request_record.template_id.id,
                'currency_id': request_record.currency_id.id,
                'contract_value': request_record.estimated_value,
                'effective_date': request_record.start_date,
                'expected_execution_date': request_record.expected_execution_date,
                'purpose': request_record.purpose,
            })
        return res

    def _prepare_contract_vals(self):
        """Map the intake data onto the contract fields.

        Values the reviewer can adjust come from the wizard; the remaining
        intake metadata is copied straight from the request. The actual
        lifecycle dates (``signing_date`` / ``execution_date``) are never set
        here — they are only filled when the signature/execution really
        happens.
        """
        self.ensure_one()
        request = self.request_id
        vals = {
            'name': self.name,
            'contract_type_id': self.contract_type_id.id,
            'partner_id': self.partner_id.id,
            'partner_signatory_id': self.partner_signatory_id.id,
            'department_id': self.department_id.id,
            'user_id': self.user_id.id,
            'company_id': self.company_id.id,
            'currency_id': self.currency_id.id,
            'contract_value': self.contract_value,
            'effective_date': self.effective_date,
            'expected_execution_date': self.expected_execution_date,
            'remarks': self.purpose,
            'required_legal_action': request.required_legal_action,
            'urgency': request.urgency,
            'procurement_owner_id': request.procurement_owner_id.id,
            'legal_owner_id': request.assigned_reviewer_id.id,
            'source': 'intake',
            'template_id': self.template_id.id,
        }
        return vals

    def action_convert(self):
        self.ensure_one()

        if self.request_id.state != 'accepted':
            raise UserError("Only accepted requests can be converted.")

        # Create the contract
        new_contract = self.env['clm.contract'].create(self._prepare_contract_vals())

        # Link the request to the new contract and update state
        self.request_id.write({
            'contract_id': new_contract.id,
            'state': 'converted'
        })

        # Copy attachments if requested
        if self.copy_attachments and self.request_id.supporting_document_ids:
            for attachment in self.request_id.supporting_document_ids:
                attachment.copy({
                    'res_model': 'clm.contract',
                    'res_id': new_contract.id,
                })

        new_contract.message_post(
            body='Created from contract request %s.' % (
                self.request_id.request_ref or self.request_id.name
            )
        )
        self.request_id._notify_intake_converted()

        # Return action to open the new contract
        return {
            'type': 'ir.actions.act_window',
            'name': 'Contract',
            'view_mode': 'form',
            'res_model': 'clm.contract',
            'res_id': new_contract.id,
            'target': 'current',
        }
