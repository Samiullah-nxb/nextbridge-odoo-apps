# Part of NextContract. See LICENSE file for full copyright and licensing details.

import base64
import io
import zipfile

from odoo import http, fields
from odoo.http import request
from odoo.addons.portal.controllers.portal import CustomerPortal, pager as portal_pager
from odoo.exceptions import AccessError

# M-10: Maximum upload file size (50 MB)
_MAX_UPLOAD_SIZE = 50 * 1024 * 1024


def _is_word_document(content):
    """True when the bytes are a real .docx package, whatever the filename says."""
    if not content.startswith(b'PK'):
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as package:
            return 'word/document.xml' in package.namelist()
    except zipfile.BadZipFile:
        return False


class ClmPortal(CustomerPortal):

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        if 'contract_count' in counters:
            accesses = request.env['clm.contract.portal.access'].sudo().search([
                ('user_id', '=', request.env.user.id),
                ('can_view', '=', True)
            ])
            accesses = accesses.filtered(lambda a: a.is_active())
            domain = [('id', 'in', accesses.mapped('contract_id').ids)]
            values['contract_count'] = request.env['clm.contract'].sudo().search_count(domain)
        return values

    @http.route(['/my/contracts', '/my/contracts/page/<int:page>'], type='http', auth="user", website=True)
    def portal_my_contracts(self, page=1, sortby=None, **kw):
        values = self._prepare_portal_layout_values()
        
        # Security: only contracts explicitly shared via clm.contract.portal.access
        access_records = request.env['clm.contract.portal.access'].sudo().search([
            ('user_id', '=', request.env.user.id),
            ('can_view', '=', True)
        ])
        access_records = access_records.filtered(lambda a: a.is_active())
        contract_ids = access_records.mapped('contract_id').ids
        
        domain = [('id', 'in', contract_ids)]
        
        searchbar_sortings = {
            'date': {'label': 'Newest', 'order': 'create_date desc'},
            'name': {'label': 'Name', 'order': 'name'},
        }
        if not sortby:
            sortby = 'date'
        order = searchbar_sortings[sortby]['order']

        contract_count = request.env['clm.contract'].sudo().search_count(domain)
        pager = portal_pager(
            url="/my/contracts",
            url_args={'sortby': sortby},
            total=contract_count,
            page=page,
            step=self._items_per_page
        )
        
        contracts = request.env['clm.contract'].sudo().search(domain, order=order, limit=self._items_per_page, offset=pager['offset'])
        
        values.update({
            'contracts': contracts,
            'page_name': 'contract',
            'pager': pager,
            'default_url': '/my/contracts',
            'searchbar_sortings': searchbar_sortings,
            'sortby': sortby,
        })
        return request.render("clm.portal_my_contracts", values)

    @http.route(['/my/contracts/<int:contract_id>'], type='http', auth="user", website=True)
    def portal_my_contract_detail(self, contract_id, **kw):
        contract = request.env['clm.contract'].sudo().browse(contract_id)
        if not contract.exists():
            return request.redirect('/my/contracts')

        access = request.env['clm.contract.portal.access'].sudo().search([
            ('contract_id', '=', contract.id),
            ('user_id', '=', request.env.user.id)
        ], limit=1)
        
        if not access or not access.can_view or not access.is_active():
            raise AccessError("You do not have access to this contract.")
            
        # Find active negotiation round
        active_round = request.env['clm.negotiation.round'].sudo().search([
            ('contract_id', '=', contract.id),
            ('status', '=', 'in_progress')
        ], limit=1)

        values = {
            'contract': contract,
            'access': access,
            'active_round': active_round,
            'page_name': 'contract',
            'error': kw.get('error')
        }
        return request.render("clm.portal_contract_page", values)

    @http.route(['/my/contracts/<int:contract_id>/upload'], type='http', auth="user", methods=['POST'], website=True, csrf=True)
    def portal_my_contract_upload(self, contract_id, upload_file=None, notes=None, **kw):
        contract = request.env['clm.contract'].sudo().browse(contract_id)
        access = request.env['clm.contract.portal.access'].sudo().search([
            ('contract_id', '=', contract.id),
            ('user_id', '=', request.env.user.id)
        ], limit=1)
        
        if not access or not access.can_upload or not access.is_active():
            raise AccessError("You do not have permission to upload revisions.")

        active_round = request.env['clm.negotiation.round'].sudo().search([
            ('contract_id', '=', contract.id),
            ('status', '=', 'in_progress')
        ], limit=1)

        if not active_round:
            return request.redirect(f'/my/contracts/{contract.id}?error=No+active+negotiation+round')

        if not upload_file:
            return request.redirect(f'/my/contracts/{contract.id}?error=No+file+provided')

        # Strict validation (PRD §40 / User Decision)
        if not upload_file.filename.endswith('.docx') and 'wordprocessingml.document' not in upload_file.content_type:
            return request.redirect(f'/my/contracts/{contract.id}?error=Negotiation+revisions+must+be+uploaded+in+Microsoft+Word+(.docx)+format.')

        file_content = upload_file.read()

        # M-10: File size validation
        if len(file_content) > _MAX_UPLOAD_SIZE:
            return request.redirect(
                f'/my/contracts/{contract.id}?error=File+exceeds+maximum+size+of+50+MB'
            )
        
        if not _is_word_document(file_content):
            return request.redirect(
                f'/my/contracts/{contract.id}?error=The+file+is+not+a+valid+Microsoft+Word+(.docx)+document.'
            )

        # Create ir.attachment
        attachment = request.env['ir.attachment'].sudo().create({
            'name': upload_file.filename,
            'datas': base64.b64encode(file_content),
            'mimetype': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'res_model': 'clm.contract',
            'res_id': contract.id,
        })

        # Odoo 19 Documents: portal users (share=True) cannot own root docs/folders.
        # Keep attribution in version.source; store the file under an internal owner.
        owner = contract.legal_owner_id or contract.user_id
        if not owner or owner.share:
            owner = request.env.ref('base.user_admin')
        folder = contract.folder_id if hasattr(contract, 'folder_id') else False

        document = request.env['documents.document'].sudo().create({
            'name': upload_file.filename,
            'attachment_id': attachment.id,
            'folder_id': folder.id if folder else False,
            'owner_id': owner.id,
            'res_model': 'clm.contract',
            'res_id': contract.id,
        })

        # Create Inbound Version, then freeze a content_snapshot so Preview uses
        # the same frozen HTML layout as the outbound draft (v1), with cover-table
        # term edits (e.g. notice period) taken from the uploaded Word file.
        parent = active_round.outbound_version_id
        inbound_version = request.env['clm.contract.version'].sudo().create({
            'contract_id': contract.id,
            'version_type': 'negotiated_draft',
            'parent_version_id': parent.id,
            'document_id': document.id,
            'source': f'Counterparty Upload: {request.env.user.partner_id.name}',
            'change_summary': notes or 'Counterparty revision',
        })
        inbound_version.write({
            'content_snapshot': inbound_version._clm_snapshot_for_counterparty_upload(
                parent_version=parent,
            ),
        })

        # Rename the stored file after the version number is known so v2 is not
        # left labelled with the outbound v1 filename the counterparty downloaded.
        ref = (contract.contract_ref or 'Contract').replace(' ', '_')
        stored_name = '%s_V%s_counterparty.docx' % (ref, inbound_version.version_number)
        attachment.sudo().write({'name': stored_name})
        document.sudo().write({'name': stored_name})

        # Close Round
        active_round.sudo().write({
            'inbound_version_id': inbound_version.id,
            'status': 'completed',
            'received_at': fields.Datetime.now()
        })

        # Automatically generate redline comparison
        request.env['clm.document.comparison'].sudo().create({
            'base_version_id': active_round.outbound_version_id.id,
            'target_version_id': inbound_version.id
        }).action_generate_redline()

        return request.redirect(f'/my/contracts/{contract.id}')

    @http.route(['/my/contracts/download/<int:version_id>'], type='http', auth="user", website=True)
    def portal_my_contract_download(self, version_id, **kw):
        version = request.env['clm.contract.version'].sudo().browse(version_id)
        if not version.exists():
            return request.not_found()

        access = request.env['clm.contract.portal.access'].sudo().search([
            ('contract_id', '=', version.contract_id.id),
            ('user_id', '=', request.env.user.id)
        ], limit=1)
        
        if not access or not access.can_download or not access.is_active():
            raise AccessError("You do not have permission to download this document.")
            
        active_round = request.env['clm.negotiation.round'].sudo().search([
            ('contract_id', '=', version.contract_id.id),
            ('status', '=', 'in_progress')
        ], limit=1)
        
        allowed_version_ids = access.shared_version_ids.ids
        if active_round and active_round.outbound_version_id:
            allowed_version_ids.append(active_round.outbound_version_id.id)
            
        if version.id not in allowed_version_ids:
            raise AccessError("You do not have permission to download this specific version.")

        # Prefer the Documents file when present (uploads / Base DOCX sends).
        # Format 1 (no template) often has only generated_docx_id — or nothing
        # yet — so fall back to the same Generate DOCX path as the backend.
        attachment = False
        if version.document_id and version.document_id.attachment_id:
            attachment = version.document_id.attachment_id.sudo()
        elif version.generated_docx_id:
            attachment = version.generated_docx_id.sudo()
        else:
            try:
                attachment = version._clm_generate_document('docx')
            except Exception:
                attachment = False

        if not attachment:
            return request.not_found()

        if hasattr(version, '_clm_attachment_bytes'):
            filecontent = version._clm_attachment_bytes(attachment)
        else:
            raw = attachment.with_context(bin_size=False).raw
            if raw:
                filecontent = bytes(raw)
            else:
                datas = attachment.with_context(bin_size=False).datas
                if isinstance(datas, str):
                    datas = datas.encode('ascii')
                filecontent = base64.b64decode(datas or b'', validate=False)

        if not filecontent:
            return request.not_found()

        filename = attachment.name or 'contract_draft.docx'
        mimetype = (
            attachment.mimetype
            or 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        )
        return request.make_response(filecontent, [
            ('Content-Type', mimetype),
            ('Content-Disposition', http.content_disposition(filename)),
        ])
