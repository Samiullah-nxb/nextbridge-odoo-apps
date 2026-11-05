# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import http
from odoo.http import request
from odoo.exceptions import AccessError
from odoo.addons.sign.controllers.portal import CustomerPortal as SignCustomerPortal


class ClmSignCustomerPortal(SignCustomerPortal):
    """Portal Sign pages: serve NextContract final PDF instead of a goofy dump."""

    @http.route(['/my/signature/<int:item_id>'], type='http', auth='user', website=True)
    def portal_my_signature(self, item_id, **kwargs):
        partner_id = request.env.user.partner_id
        sign_item_sudo = request.env['sign.request.item'].sudo().browse(item_id)
        if (
            not sign_item_sudo.exists()
            or sign_item_sudo.partner_id != partner_id
            or (sign_item_sudo.state == 'sent' and sign_item_sudo.is_mail_sent is False)
        ):
            return request.redirect('/my/')

        sign_req = sign_item_sudo.sign_request_id.sudo()
        url = (
            '/sign/document/%s/%s?portal=1'
            % (sign_req.id, sign_item_sudo.access_token)
        )
        values = {
            'page_name': 'signature',
            'my_sign_item': sign_item_sudo,
            'url': url,
            'clm_use_final_pdf': False,
            'clm_final_pdf_url': False,
            'clm_final_download_url': False,
        }

        contract = sign_req.clm_contract_id
        if (
            contract
            and sign_req.state == 'signed'
            and contract.final_contract_available
        ):
            final_url = '/my/clm/signature/%s/final.pdf' % sign_item_sudo.id
            values.update({
                'url': final_url,
                'clm_final_pdf_url': final_url,
                'clm_final_download_url': final_url + '?download=1',
                'clm_use_final_pdf': True,
            })

        kwargs.pop('access_token', None)
        values = self._get_page_view_values(
            sign_item_sudo,
            sign_item_sudo.access_token,
            values,
            'my_signatures_history',
            False,
            **kwargs,
        )
        return request.render('sign.sign_portal_my_request', values)

    @http.route(
        ['/my/clm/signature/<int:item_id>/final.pdf'],
        type='http',
        auth='user',
        website=True,
    )
    def portal_clm_signature_final_pdf(self, item_id, download=0, **kwargs):
        """Portal-safe Final Contract PDF for a Sign request the user can access."""
        partner = request.env.user.partner_id
        item = request.env['sign.request.item'].sudo().browse(item_id)
        if (
            not item.exists()
            or item.partner_id != partner
            or (item.state == 'sent' and not item.is_mail_sent)
        ):
            raise AccessError("You do not have access to this signature request.")

        sign_req = item.sign_request_id.sudo()
        contract = sign_req.clm_contract_id
        if not contract:
            return request.redirect(
                '/sign/download/%s/%s/completed' % (sign_req.id, sign_req.access_token)
            )

        try:
            attachment = contract.sudo()._get_final_contract_attachment('pdf')
        except Exception:
            attachment = False
        if not attachment or not attachment.raw:
            return request.redirect(
                '/sign/download/%s/%s/completed' % (sign_req.id, sign_req.access_token)
            )

        filename = attachment.name or (
            '%s_FINAL.pdf' % (contract.contract_ref or 'contract')
        )
        disposition = (
            'attachment' if str(download) in ('1', 'true', 'True') else 'inline'
        )
        headers = [
            ('Content-Type', 'application/pdf'),
            ('Content-Disposition', '%s; filename="%s"' % (disposition, filename)),
        ]
        return request.make_response(attachment.raw, headers=headers)
