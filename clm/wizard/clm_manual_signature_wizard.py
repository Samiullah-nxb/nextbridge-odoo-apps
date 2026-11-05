# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models, fields, api
from odoo.exceptions import UserError

class ClmManualSignatureWizard(models.TransientModel):
    _name = 'clm.manual.signature.wizard'
    _description = 'Manual Signature Wizard'

    contract_id = fields.Many2one('clm.contract', required=True)
    source_version_id = fields.Many2one('clm.contract.version', string='Source Version', required=True, 
                                        domain="[('contract_id', '=', contract_id), ('version_type', '=', 'signature_draft')]")
    
    signed_document = fields.Binary(
        'Signed Document', required=True,
        help='Upload the fully signed copy as PDF or Word (.doc / .docx). '
             'Word files are converted to PDF automatically for View Signed PDF.',
    )
    signed_document_name = fields.Char('Filename')
    
    execution_date = fields.Date('Signing Date', default=fields.Date.context_today, required=True)
    signatory = fields.Char('Signatory / Authorized Signer', required=True)
    reason = fields.Text('Reason for Manual Execution', required=True, 
                         help="Why was this manually signed instead of using Odoo Sign?")
    
    confirm_execution = fields.Boolean('I confirm this document is fully executed', required=True)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        active_id = self.env.context.get('active_id')
        if active_id and self.env.context.get('active_model') == 'clm.contract':
            contract = self.env['clm.contract'].browse(active_id)
            res['contract_id'] = contract.id
            signature_draft = self.env['clm.contract.version'].search([
                ('contract_id', '=', contract.id),
                ('version_type', '=', 'signature_draft'),
                ('status', '=', 'active'),
            ], limit=1)
            if signature_draft:
                res['source_version_id'] = signature_draft.id
        return res

    def action_confirm_manual_signature(self):
        self.ensure_one()
        if not self.confirm_execution:
            raise UserError("You must confirm the execution.")

        contract = self.contract_id
        # Manual upload means a fully executed copy (all parties). Cancel any
        # open Odoo Sign request so the dual-party gate does not block start.
        open_reqs = contract._clm_open_sign_requests()
        if open_reqs:
            waiting = open_reqs.mapped('request_item_ids').filtered(
                lambda i: i.state != 'completed'
            )
            if waiting:
                names = ', '.join(dict.fromkeys(
                    waiting.mapped(lambda i: i.partner_id.name or 'Signer')
                ))
                open_reqs.with_context(clm_skip_signature_cancellation=True).write(
                    {'state': 'canceled'}
                )
                contract.message_post(
                    body=(
                        "Open Odoo Sign request(s) canceled because a manually "
                        "signed copy was uploaded (was still waiting for: %s)."
                    ) % names
                )

        # 1. Store the uploaded signed copy (PDF or Word) in Documents.
        filename = self.signed_document_name or 'Manually_Signed_Contract.pdf'
        mimetype = self._guess_signed_mimetype(filename)
        document_vals = {
            'name': filename,
            'type': 'binary',
            'datas': self.signed_document,
            'mimetype': mimetype,
            'folder_id': contract.folder_id.id if contract.folder_id else False,
            'res_model': 'clm.contract',
            'res_id': contract.id,
        }
        document = self.env['documents.document'].create(document_vals)

        # 2. Funnel into the Execution Service
        notes = f"Signatory: {self.signatory} | Reason: {self.reason}"
        contract._process_contract_execution(
            source_version_id=self.source_version_id.id,
            execution_method='manual',
            execution_date=self.execution_date,
            executed_document_id=document.id,
            notes=notes,
            signatory=self.signatory,
        )

        return {'type': 'ir.actions.act_window_close'}

    @staticmethod
    def _guess_signed_mimetype(filename):
        name = (filename or '').lower()
        if name.endswith('.pdf'):
            return 'application/pdf'
        if name.endswith('.docx'):
            return (
                'application/vnd.openxmlformats-officedocument'
                '.wordprocessingml.document'
            )
        if name.endswith('.doc'):
            return 'application/msword'
        return 'application/octet-stream'
