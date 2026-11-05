# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models, fields, api
from odoo.exceptions import UserError


class SignRequest(models.Model):
    _inherit = 'sign.request'

    clm_contract_id = fields.Many2one('clm.contract', string='NextContract Contract')
    clm_source_version_id = fields.Many2one('clm.contract.version', string='Source Version')

    def _get_completed_document(self):
        """Helper to get the completed PDF document ID from sign request."""
        # sign.request saves the completed document as an ir.attachment linked to itself
        # We need to wrap it in a documents.document for NextContract
        self.ensure_one()
        attachment = False
        if hasattr(self, 'completed_document_attachment_ids') and self.completed_document_attachment_ids:
            attachment = self.completed_document_attachment_ids[0]

        if not attachment:
            attachment = self.env['ir.attachment'].search([
                ('res_model', '=', 'sign.request'),
                ('res_id', '=', self.id),
                ('mimetype', '=', 'application/pdf'),
                ('create_date', '>=', self.create_date)
            ], order='create_date desc', limit=1)

        if attachment and self.clm_contract_id:
            document = self.env['documents.document'].create({
                'name': attachment.name,
                'attachment_id': attachment.id,
                'folder_id': self.clm_contract_id.folder_id.id if self.clm_contract_id.folder_id else False,
                'res_model': 'clm.contract',
                'res_id': self.clm_contract_id.id,
            })
            return document.id
        return False

    @api.model_create_multi
    def create(self, vals_list):
        requests = super().create(vals_list)
        for req in requests:
            if req.clm_contract_id:
                req.clm_contract_id.signature_status = 'pending'
        return requests

    def write(self, vals):
        res = super().write(vals)
        # Fully signed only — never execute after a single party signs.
        if 'state' in vals and vals['state'] in ('signed', 'done'):
            for req in self:
                if not req.clm_contract_id or not req.clm_source_version_id:
                    continue
                if req.clm_contract_id.state == 'executed':
                    continue
                if not req.clm_contract_id._clm_sign_request_all_parties_signed(req):
                    # Keep pending until every signer completes.
                    req.clm_contract_id.sudo().message_post(
                        body=(
                            "Odoo Sign reported complete, but not every party has signed yet. "
                            "The contract will stay Pending Signature and will not start."
                        )
                    )
                    continue
                document_id = req._get_completed_document()
                req.clm_contract_id._process_contract_execution(
                    source_version_id=req.clm_source_version_id.id,
                    execution_method='odoo_sign',
                    execution_date=fields.Date.today(),
                    executed_document_id=document_id,
                    notes=f"Automatically executed via Odoo Sign (Request {req.id}) after all parties signed",
                )
        elif 'state' in vals and vals['state'] == 'canceled':
            if not self.env.context.get('clm_skip_signature_cancellation'):
                for req in self:
                    if req.clm_contract_id:
                        req.clm_contract_id.sudo()._process_signature_cancellation(
                            sign_request=req
                        )
        return res

class SignRequestItem(models.Model):
    _inherit = 'sign.request.item'

    def write(self, vals):
        res = super().write(vals)
        if vals.get('state') != 'completed':
            return res
        for item in self:
            request = item.sign_request_id
            contract = request.clm_contract_id
            if not contract or contract.state != 'pending_signature':
                continue
            done, total, progress = contract._clm_signature_progress_vals()
            if total and done < total:
                contract.sudo().message_post(
                    body=(
                        "<b>%s</b> signed. %s<br/>"
                        "Contract remains Pending Signature and will not start until both parties sign."
                    ) % (
                        item.partner_id.name or item.role_id.name or 'A party',
                        progress,
                    )
                )
            elif total and done >= total and request.state not in ('signed', 'done'):
                # All items done; Sign will flip the request to signed shortly.
                contract.sudo().message_post(
                    body="All parties have signed. Completing contract execution…"
                )
        return res
