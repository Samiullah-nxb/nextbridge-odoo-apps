# Part of NextContract. See LICENSE file for full copyright and licensing details.

from datetime import timedelta

from odoo import models, fields, api
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)

class ClmContractExecution(models.Model):
    _inherit = 'clm.contract'
    
    signature_status = fields.Selection([
        ('pending', 'Pending Signature'),
        ('signed', 'Fully Signed'),
        ('expired', 'Signature Expired'),
        ('cancelled', 'Signature Cancelled')
    ], string='Signature Status', tracking=True, copy=False)
    
    execution_date = fields.Date('Execution Date', tracking=True, copy=False)
    sign_request_ids = fields.One2many('sign.request', 'clm_contract_id', string='Signature Requests')
    signature_progress = fields.Char(
        string='Signature Progress',
        compute='_compute_signature_progress',
        help='How many parties have signed. The contract cannot start until all have signed.',
    )
    signatures_complete = fields.Boolean(
        string='All Parties Signed',
        compute='_compute_signature_progress',
    )
    
    risk_assessment_ids = fields.One2many('clm.risk.assessment', 'contract_id', string='Risk Assessments')
    obligation_ids = fields.One2many('clm.obligation', 'contract_id', string='Obligations')
    alert_ids = fields.One2many('clm.contract.alert', 'contract_id', string='Alerts')

    @api.depends(
        'signature_status',
        'sign_request_ids',
        'sign_request_ids.state',
        'sign_request_ids.request_item_ids',
        'sign_request_ids.request_item_ids.state',
    )
    def _compute_signature_progress(self):
        for contract in self:
            done, total, label = contract._clm_signature_progress_vals()
            contract.signature_progress = label
            contract.signatures_complete = contract._clm_signatures_fully_complete()
            if not label and contract.signature_status == 'signed':
                contract.signature_progress = 'Fully signed.'
                contract.signatures_complete = True
            elif not label and contract.state == 'pending_signature':
                contract.signature_progress = (
                    'Waiting for signatures. Contract will not start until both parties sign.'
                )

    def _get_or_create_sign_template(self, attachment):
        """Find or create a sign.template for an attachment (Odoo 19 Sign API)."""
        existing_document = self.env['sign.document'].search(
            [('attachment_id', '=', attachment.id)], limit=1,
        )
        if existing_document:
            template = existing_document.template_id
        else:
            template_id, _template_name = self.env['sign.template'].create_sign_template_from_ir_attachment_data(
                attachment_id=attachment.id,
                res_id=attachment.res_id,
                res_model=attachment.res_model,
            )
            template = self.env['sign.template'].browse(template_id)
        # Odoo Sign only allows as many signers as the template has roles.
        return self._clm_ensure_dual_party_sign_items(template)

    def _clm_provider_sign_role(self):
        return self.env.ref('sign.sign_item_role_default')

    def _clm_counterparty_sign_role(self):
        role = self.env.ref('hr_sign.sign_item_role_default', raise_if_not_found=False)
        if role:
            return role
        role = self.env['sign.item.role'].search([('name', 'ilike', 'Customer')], limit=1)
        if role:
            return role
        return self.env['sign.item.role'].create({'name': 'Counterparty'})

    def _clm_sign_item_type(self, xmlid, item_type=None):
        """Resolve a Sign field type by xmlid, with item_type fallback."""
        record = self.env.ref(xmlid, raise_if_not_found=False)
        if record:
            return record
        if item_type:
            return self.env['sign.item.type'].search(
                [('item_type', '=', item_type)], limit=1,
            )
        return self.env['sign.item.type']

    def _clm_partner_job_title(self, partner):
        """Best-effort job title for a signing partner (function / HR job / user)."""
        if not partner:
            return ''
        if partner.function:
            return partner.function
        Employee = self.env.get('hr.employee')
        if Employee is not None:
            emp = Employee.sudo().search([
                '|',
                ('work_contact_id', '=', partner.id),
                ('user_id.partner_id', '=', partner.id),
            ], limit=1)
            if emp:
                if emp.job_title:
                    return emp.job_title
                if emp.job_id and emp.job_id.name:
                    return emp.job_id.name
        user = self.env['res.users'].sudo().search([('partner_id', '=', partner.id)], limit=1)
        if user and getattr(user, 'function', None):
            return user.function or ''
        return ''

    def _clm_signatory_titles_for_roles(self):
        """Provider then counterparty job titles for signature-block Title lines."""
        self.ensure_one()
        provider_user = self.legal_owner_id or self.user_id
        provider_partner = (
            provider_user.partner_id if provider_user else False
        ) or (self.company_id.partner_id if self.company_id else False)
        provider_title = self._clm_partner_job_title(provider_partner)
        if not provider_title and provider_user and getattr(provider_user, 'function', None):
            provider_title = provider_user.function or ''
        partner_title = self._clm_partner_job_title(
            self.partner_signatory_id or self.partner_id
        )
        return [provider_title, partner_title]

    def _clm_ensure_dual_party_sign_items(self, template):
        """Add provider + counterparty Signature fields only.

        Title and Date are printed in the Base DOCX like Name (filled by docxtpl),
        not as floating Odoo Sign boxes — those looked goofy and overlapped the
        labels. Sign widgets are only for the handwritten signature stroke.
        """
        template.ensure_one()
        document = template.document_ids[:1]
        if not document:
            return template

        signature_type = self._clm_sign_item_type(
            'sign.sign_item_type_signature', item_type='signature',
        )
        if not signature_type:
            return template

        # Drop leftover Title / Date Sign widgets from older templates —
        # those values are printed in the DOCX like Name.
        SignItem = self.env['sign.item'].sudo()
        leftover = template.sudo().sign_item_ids.filtered(
            lambda i: i.type_id.item_type != 'signature'
        )
        if leftover:
            leftover.unlink()

        provider_role = self._clm_provider_sign_role()
        customer_role = self._clm_counterparty_sign_role()
        roles = (provider_role, customer_role)
        placements = self._clm_signature_block_field_placements(document)
        signatures = placements.get('signature') or []

        existing_items = template.sudo().sign_item_ids

        def _match_signature(role):
            for item in existing_items:
                if item.responsible_id == role and item.type_id.item_type == 'signature':
                    return item
            return False

        for idx, role in enumerate(roles):
            if idx >= len(signatures):
                break
            # Placement is (page, x, y) or (page, x, y, height).
            page, pos_x, pos_y = signatures[idx][:3]
            sig_height = (
                signatures[idx][3] if len(signatures[idx]) > 3 else 0.028
            )
            vals = {
                'page': page,
                'posX': pos_x,
                'posY': pos_y,
                'width': 0.32,
                'height': sig_height,
                'required': True,
                'constant': False,
                'name': 'Signature',
            }
            existing = _match_signature(role)
            if existing:
                existing.sudo().write(vals)
                continue
            SignItem.create({
                'document_id': document.id,
                'type_id': signature_type.id,
                'responsible_id': role.id,
                **vals,
            })
        return template

    def _clm_detect_labeled_line_placements(self, document, label):
        """Return ``[(page, posX, posY), ...]`` for printed ``Label:`` lines.

        Coordinates use Odoo Sign fractions (origin top-left). For Signature,
        X is placed just after ``Signature:`` on the same row as the printed
        label (same visual line as Name / Title / Date text).
        """
        attachment = document.attachment_id
        if not attachment:
            return []
        attachment = attachment.with_context(bin_size=False)
        raw = attachment.raw
        if not raw and attachment.datas:
            import base64
            datas = attachment.datas
            if isinstance(datas, memoryview):
                datas = datas.tobytes()
            if isinstance(datas, bytes) and datas[:4] == b'%PDF':
                raw = datas
            else:
                try:
                    raw = base64.b64decode(datas)
                except Exception:
                    return []
        if not raw:
            return []
        try:
            import pypdfium2
        except ImportError:
            return []

        label = (label or '').strip()
        if not label:
            return []
        found = []
        try:
            pdf = pypdfium2.PdfDocument(raw)
        except Exception:
            return []
        try:
            for page_index, page in enumerate(pdf):
                width, height = page.get_size()
                if not width or not height:
                    continue
                textpage = page.get_textpage()
                full = textpage.get_text_range() or ''
                start = 0
                while True:
                    idx = full.find(label, start)
                    if idx < 0:
                        break
                    start = idx + 1
                    window = full[idx:idx + 48].replace('\r', '')
                    # Require "Label:" so bare words (e.g. section "5. Signature") skip.
                    if f'{label}:' not in window and f'{label} :' not in window:
                        continue
                    try:
                        left, bottom, right, top = textpage.get_charbox(idx)
                    except Exception:
                        continue
                    # Prefer X just after the colon so the widget sits on the
                    # blank / underline portion of the same row.
                    place_x = right
                    colon_at = window.find(':')
                    if colon_at >= 0:
                        try:
                            _l, _b, colon_right, _t = textpage.get_charbox(
                                idx + colon_at,
                            )
                            place_x = colon_right
                        except Exception:
                            place_x = right
                    # Nudge X past the colon; keep Y on the text row top so a
                    # one-line-tall widget aligns with Name / Title / Date.
                    pos_x = min(max((place_x / width) + 0.012, 0.05), 0.68)
                    pos_y = min(max((height - top) / height, 0.02), 0.92)
                    found.append((page_index + 1, round(pos_x, 3), round(pos_y, 3)))
        finally:
            try:
                pdf.close()
            except Exception:
                pass

        found.sort(key=lambda row: (row[0], row[2], row[1]))
        return found

    def _clm_signature_block_field_placements(self, document):
        """Map Signature lines to provider / counterparty Sign placements.

        Default Format prints Signature / Name / Date. The Sign widget must sit
        on the Signature row — not grow upward from Date (that lands on Name).
        """
        signatures = self._clm_detect_labeled_line_placements(document, 'Signature')[:2]
        names = self._clm_detect_labeled_line_placements(document, 'Name')
        dates = self._clm_detect_labeled_line_placements(document, 'Date')
        if not signatures:
            page = max(document.num_pages or 1, 1)
            signatures = [
                (page, 0.18, 0.58),
                (page, 0.18, 0.72),
            ]

        preferred_h = 0.028
        min_h = 0.018
        gap = 0.006
        adjusted = []
        for sig in signatures:
            page, sx, sy = sig
            # Keep the top of the box on the Signature label / underline.
            top = max(sy - 0.004, 0.02)
            # Cap height so the box stops above Name (preferred) or Date.
            blockers = [
                row[2] for row in (names + dates)
                if row[0] == page and row[2] > sy + 0.002
            ]
            if blockers:
                next_y = min(blockers)
                available = max(next_y - top - gap, min_h)
                height = min(preferred_h, available)
            else:
                height = preferred_h
            adjusted.append((page, sx, round(top, 3), round(height, 3)))

        return {
            'signature': adjusted,
            'title': [],
            'date': [],
        }

    def _clm_detect_signature_line_placements(self, document):
        """Return ``[(page, posX, posY), ...]`` for printed Signature: underlines."""
        return self._clm_signature_block_field_placements(document).get('signature', [])[:2]

    def _clm_signing_partners(self):
        """Provider company then counterparty — both must e-sign.

        Provider prefers the company partner (e.g. Contract Provider Ltd.), then
        legal owner / business owner. Counterparty prefers
        ``partner_signatory_id`` (the person who signs), then ``partner_id``.
        """
        self.ensure_one()
        partners = []
        seen = set()

        def _add(partner):
            if partner and partner.id not in seen:
                partners.append(partner)
                seen.add(partner.id)

        company_partner = self.company_id.partner_id if self.company_id else False
        _add(company_partner)
        if not company_partner:
            if self.legal_owner_id and self.legal_owner_id.partner_id:
                _add(self.legal_owner_id.partner_id)
            elif self.user_id and self.user_id.partner_id:
                _add(self.user_id.partner_id)
        # Person who signs for the counterparty company, when set.
        if 'partner_signatory_id' in self._fields and self.partner_signatory_id:
            _add(self.partner_signatory_id)
        else:
            _add(self.partner_id)
        if not partners and self.env.user.partner_id:
            _add(self.env.user.partner_id)
        return partners

    def _clm_open_sign_requests(self):
        """Sign requests still in progress (not fully signed / canceled)."""
        self.ensure_one()
        # sudo: readers of the signature state (Legal Reviewers, the form's computed
        # fields) are not Odoo Sign users, yet the state is a property of the contract.
        return self.sudo().sign_request_ids.filtered(
            lambda r: r.state not in ('signed', 'canceled', 'done')
        )

    def _clm_sign_request_all_parties_signed(self, sign_request):
        """True only when every signer on the request has completed."""
        if not sign_request or not sign_request.request_item_ids:
            return False
        return all(
            item.state == 'completed'
            for item in sign_request.request_item_ids
        )

    def _clm_signatures_fully_complete(self):
        """Contract may start only after every required party has signed.

        - Odoo Sign: the signed request must have all request items completed.
        - Manual: signature_status is Fully Signed (wizard already confirms).
        - While any Sign request still has unsigned parties, not complete.
        """
        self.ensure_one()
        if self._clm_open_sign_requests():
            return False
        signed_requests = self.sudo().sign_request_ids.filtered(
            lambda r: r.state in ('signed', 'done')
        )
        if signed_requests:
            return any(
                self._clm_sign_request_all_parties_signed(req)
                for req in signed_requests
            )
        return self.signature_status == 'signed'

    def _clm_signature_progress_vals(self):
        """Return (done, total, label) for the latest open or signed request."""
        self.ensure_one()
        request = self._clm_open_sign_requests().sorted('id', reverse=True)[:1]
        if not request:
            request = self.sudo().sign_request_ids.filtered(
                lambda r: r.state in ('signed', 'done')
            ).sorted('id', reverse=True)[:1]
        if not request or not request.request_item_ids:
            return 0, 0, ''
        items = request.request_item_ids
        done = len(items.filtered(lambda i: i.state == 'completed'))
        total = len(items)
        waiting = items.filtered(lambda i: i.state != 'completed').mapped(
            lambda i: i.partner_id.name or i.role_id.name or 'Signer'
        )
        if done >= total and total:
            label = 'All %s parties have signed.' % total
        elif total:
            label = '%s of %s signed. Waiting for: %s. Contract will not start until both parties sign.' % (
                done, total, ', '.join(waiting) or 'remaining signers',
            )
        else:
            label = ''
        return done, total, label

    def _clm_assert_can_start(self):
        """Block Activate until every party has signed."""
        self.ensure_one()
        if self._clm_signatures_fully_complete():
            return
        _done, _total, progress = self._clm_signature_progress_vals()
        raise UserError(
            "This contract cannot start until both parties have signed.\n\n"
            + (progress or "Signature is still incomplete.")
            + "\n\nUse Sign with Odoo Sign for each remaining signer, "
            "or cancel the open Sign request before Mark as Manually Signed."
        )

    def _prepare_sign_request_items(self, contract, template):
        """One Sign request item per template role (provider + counterparty)."""
        provider_role = self._clm_provider_sign_role()
        customer_role = self._clm_counterparty_sign_role()
        partners = contract._clm_signing_partners()
        if not partners:
            raise UserError(
                "Cannot send for signature: no signer could be determined. "
                "Set a company, counterparty, or business owner on the contract."
            )
        counterparty = contract.partner_signatory_id or contract.partner_id
        if not counterparty:
            # Without one, Sign would give both roles to the provider company.
            raise UserError(
                "Cannot send for signature: the contract has no Counterparty. "
                "Set the Counterparty (and ideally its Signatory) first."
            )
        no_email = [p.display_name for p in partners if not p.email_normalized]
        if no_email:
            raise UserError(
                "Cannot send for signature: these signers have no valid email "
                "address: %s." % ', '.join(no_email)
            )

        template_roles = []
        for role in template.sign_item_ids.mapped('responsible_id'):
            if role not in template_roles:
                template_roles.append(role)

        # Prefer provider then counterparty order when both roles exist.
        preferred = [r for r in (provider_role, customer_role) if r in template_roles]
        roles = preferred or template_roles or [provider_role]

        request_item_vals = []
        for idx, role in enumerate(roles):
            partner = partners[idx] if idx < len(partners) else partners[-1]
            request_item_vals.append({
                'role_id': role.id,
                'partner_id': partner.id,
                'mail_sent_order': idx + 1,
            })
        return request_item_vals

    def _get_active_signature_draft(self):
        self.ensure_one()
        return self.env['clm.contract.version'].search([
            ('contract_id', '=', self.id),
            ('version_type', '=', 'signature_draft'),
            ('status', '=', 'active'),
        ], limit=1)

    def action_prepare_signature_draft(self):
        """Promote the current active version to Signature Draft (demo / pre-sign step)."""
        self._clm_require_groups('clm.group_clm_legal')
        for rec in self:
            if rec.state != 'approved':
                raise UserError("Only contracts in 'Approved for Signature' can be prepared for signing.")

            existing = rec._get_active_signature_draft()
            if existing:
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Signature Draft',
                    'res_model': 'clm.contract.version',
                    'res_id': existing.id,
                    'view_mode': 'form',
                    'target': 'current',
                }

            active_version = self.env['clm.contract.version'].search([
                ('contract_id', '=', rec.id),
                ('status', '=', 'active'),
            ], order='version_number desc', limit=1)

            if not active_version:
                raise UserError(
                    "No active document version found.\n\n"
                    "Go to the Document Versions tab, upload a PDF on Version 1, "
                    "and click Activate Version first."
                )
            # Signing an older version while a newer one is waiting would sign
            # content that is no longer current.
            newer = self.env['clm.contract.version'].search([
                ('contract_id', '=', rec.id),
                ('status', '=', 'draft'),
                ('version_number', '>', active_version.version_number),
            ], order='version_number desc', limit=1)
            if newer:
                raise UserError(
                    "Version %s is newer than the active version %s.\n\n"
                    "Activate version %s so the contract is signed with its latest "
                    "content, or Supersede it if it should not go forward."
                    % (newer.version_number, active_version.version_number, newer.version_number)
                )
            # A version with frozen content is signed as its generated PDF.
            if not active_version.document_id and not active_version.content_snapshot:
                raise UserError(
                    "The active version has no document attached.\n\n"
                    "Open Version 1 on the Document Versions tab and link or upload a PDF."
                )

            active_version.write({'version_type': 'signature_draft'})
            rec.message_post(
                body='Version %s prepared as Signature Draft.' % active_version.version_number,
            )
        return True

    def action_send_for_signature(self):
        self._clm_require_groups('clm.group_clm_legal')
        # Legal Reviewers are not Odoo Sign users, yet sending for signature is
        # theirs to do. The role was just checked, so the Sign records
        # (template, document, request) are created with elevated rights.
        self = self.sudo()
        for rec in self:
            if rec.state != 'approved':
                raise UserError("Only approved contracts can be sent for signature.")
            
            signature_draft = rec._get_active_signature_draft()
            
            if not signature_draft:
                raise UserError(
                    "No active Signature Draft found.\n\n"
                    "Before sending for signature:\n"
                    "1. Open the Document Versions tab\n"
                    "2. Open Version 1 (must be Active with a PDF attached)\n"
                    "3. Set Version Type to 'Signature Draft' and save\n"
                    "   — or click 'Prepare Signature Draft' on the contract header"
                )
                
            attachment = signature_draft._clm_signing_attachment()
            if not attachment:
                raise UserError("The active signature draft does not have an attached document.")
            template = rec._get_or_create_sign_template(attachment)
            request_item_vals = rec._prepare_sign_request_items(rec, template)

            # Sign items must be created with the request; Odoo validates on create.
            ref_parts = [rec.contract_ref or 'Contract']
            if rec.name:
                ref_parts.append(rec.name)
            self.env['sign.request'].with_context(no_sign_mail=True).create({
                'template_id': template.id,
                'reference': ' — '.join(ref_parts),
                'clm_contract_id': rec.id,
                'clm_source_version_id': signature_draft.id,
                'request_item_ids': [(0, 0, vals) for vals in request_item_vals],
            })
            
            rec.write({
                'state': 'pending_signature',
                'signature_status': 'pending'
            })
            rec._notify_signature_requested()

    def action_open_odoo_sign(self):
        """Open the active Odoo Sign request so the provider can e-sign in-app."""
        self.ensure_one()
        if self.state != 'pending_signature':
            raise UserError("Odoo Sign is only available while the contract is pending signature.")

        request = self.sign_request_ids.filtered(
            lambda r: r.state not in ('signed', 'canceled')
        ).sorted('id', reverse=True)[:1]
        if not request:
            raise UserError(
                "No open Odoo Sign request found.\n\n"
                "Click Send for Signature first, then use Sign with Odoo Sign."
            )

        # Re-place Signature / Title / Date fields before opening.
        if request.template_id:
            self._clm_ensure_dual_party_sign_items(request.template_id)
            # Constant Title values are filled when the request is created; refresh
            # them if Title fields were added after Send for Signature.
            if request.state not in ('signed', 'canceled') and hasattr(request, '_populate_constant_items'):
                request.sudo()._populate_constant_items()

        pending = request.request_item_ids.filtered(lambda i: i.state == 'sent')
        user_partner = self.env.user.partner_id
        company_partner = self.company_id.partner_id

        # Prefer the logged-in user's item, then company (provider) item, then next in order.
        target = pending.filtered(lambda i: i.partner_id == user_partner)[:1]
        if not target and company_partner:
            target = pending.filtered(lambda i: i.partner_id == company_partner)[:1]
        if not target and hasattr(request, '_get_next_sign_request_items'):
            target = request._get_next_sign_request_items()[:1]
        if not target:
            target = pending[:1]

        if target:
            action = request.go_to_signable_document(target)
            if action:
                return action

        return {
            'type': 'ir.actions.act_window',
            'name': request.reference,
            'res_model': 'sign.request',
            'res_id': request.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def _notify_signature_requested(self):
        """Email counterparty and business owner that signature is required."""
        self.ensure_one()
        notify = self.env['clm.mail.notify']
        partners = self.partner_id
        if self.user_id and self.user_id.partner_id:
            partners |= self.user_id.partner_id
        email_to = notify.emails_for_partners(partners)
        if not email_to and self.user_id and self.user_id.email:
            email_to = self.user_id.email
        if not email_to:
            return
        notify.send(
            self,
            'clm.mail_template_clm_signature_request',
            email_to=email_to,
        )

    def _notify_expiry_alert(self, alert=None):
        """Email legal/business owners for expiry/renewal alerts."""
        self.ensure_one()
        notify = self.env['clm.mail.notify']
        users = self.legal_owner_id | self.user_id
        email_to = notify.emails_for_users(users)
        if not email_to:
            email_to = notify.emails_for_group('clm.group_clm_legal_admin')
        if not email_to:
            _logger.warning(
                'NextContract expiry alert skipped (no recipients) for contract %s',
                self.display_name,
            )
            return False
        return notify.send(
            self,
            'clm.mail_template_clm_expiry_alert',
            email_to=email_to,
            event_code='expiry_alert',
        )

    def _process_contract_execution(self, source_version_id, execution_method, execution_date, executed_document_id=False, notes=False, signatory=False):
        """
        Converged Execution Service.
        Creates an immutable Executed Version linked back to the source draft.
        Transitions the contract to EXECUTED.
        """
        self.ensure_one()
        
        # 1. Validate State
        if self.state in ['executed', 'active', 'expired', 'terminated']:
            raise UserError(f"Contract is already in {self.state} state and cannot be executed again.")
            
        if self.state != 'pending_signature':
            raise UserError(
                f"Contract must be in 'pending_signature' state to execute. "
                f"Current state: {self.state}"
            )

        # Dual-party rule: never execute while any Sign request still has unsigned parties.
        open_incomplete = self._clm_open_sign_requests().filtered(
            lambda r: not self._clm_sign_request_all_parties_signed(r)
        )
        if open_incomplete and execution_method == 'odoo_sign':
            raise UserError(
                "Cannot execute until both parties have signed via Odoo Sign.\n\n"
                + (self._clm_signature_progress_vals()[2] or '')
            )
        if open_incomplete and execution_method == 'manual':
            waiting = open_incomplete.mapped('request_item_ids').filtered(
                lambda i: i.state != 'completed'
            ).mapped(lambda i: i.partner_id.name or 'Signer')
            raise UserError(
                "An Odoo Sign request is still waiting for: %s.\n\n"
                "Wait for both parties to sign in Odoo Sign, or cancel the open "
                "Sign request first if you will upload a fully signed PDF instead."
                % (', '.join(dict.fromkeys(waiting)) or 'remaining signers')
            )
            
        # 2. Validate Source Version
        source_version = self.env['clm.contract.version'].browse(source_version_id)
        if not source_version.exists():
            raise UserError("Source signature draft version not found.")
            
        if source_version.contract_id != self:
            raise UserError("The provided source version does not belong to this contract.")
            
        if source_version.version_type != 'signature_draft' and execution_method != 'legacy_migration':
            # Allow legacy migration to bypass this strict type check, but standard execution requires signature_draft
            raise UserError("Source version must be a 'signature_draft' to execute.")
            
        if execution_method == 'manual' and not executed_document_id:
            raise UserError("Manual execution requires an executed document.")
            
        # 3. Create the new executed version
        new_version_vals = {
            'contract_id': self.id,
            'version_type': 'executed',
            'execution_method': execution_method,
            'source_version_id': source_version.id,
            'parent_version_id': source_version.id,
            # What was signed is exactly what the source version says.
            'content_snapshot': source_version.content_snapshot or False,
            'status': 'active', # Executed versions are permanently immutable by _is_executed check
            # Captured at create time: the executed version is immutable
            # afterwards, and the final contract's signature block reads these.
            'execution_signatory': signatory or False,
            'execution_notes': notes or False,
        }

        if executed_document_id:
            new_version_vals['document_id'] = executed_document_id
        elif source_version.document_id:
            # Odoo Sign: keep the same Word file that was sent for signature
            # (e.g. counterparty upload without Force Majeure). Otherwise View
            # Final regenerates from Base DOCX and revives removed clauses.
            new_version_vals['document_id'] = source_version.document_id.id

        executed_version = self.env['clm.contract.version'].create(new_version_vals)
        
        # 4. Update Contract State
        self.write({
            'state': 'executed',
            'signature_status': 'signed',
            'execution_date': execution_date,
            'signing_date': execution_date,
            'signed_copy_uploaded': True,
        })
        
        # 5. Audit Log
        msg = f"Contract executed via {execution_method}."
        if notes:
            msg += f" Notes: {notes}"
        self.message_post(body=msg)
        
        return executed_version

    def _process_signature_cancellation(self, sign_request=None, reason=None):
        """
        Reverts the contract from pending_signature back to approved
        when a sign request is canceled or refused.
        """
        self.ensure_one()
        if self.state != 'pending_signature':
            return  # Already moved past this state, ignore

        self.write({
            'state': 'approved',
            'signature_status': 'cancelled',
        })

        msg = "Signature request was canceled or refused."
        if sign_request:
            msg += f" (Sign Request #{sign_request.id})"
        if reason:
            msg += f" Reason: {reason}"
        self.message_post(body=msg)

    def action_activate(self):
        self._clm_require_groups('clm.group_clm_legal')
        for contract in self:
            if contract.state != 'executed':
                raise UserError('Only executed contracts can be activated.')
            contract._clm_assert_can_start()
            failed = contract._get_failed_activation_checks()
            if failed:
                raise UserError(
                    "Cannot activate contract. Complete the Activation Checklist first.\n\n"
                    "Missing required items:\n- %s\n\n"
                    "Open the Activation Checklist tab, fill the missing fields, then try again."
                    % '\n- '.join(failed)
                )
            contract.write({'state': 'active'})
            contract.message_post(body='Contract activated. Activation checklist passed.')
            if not contract.alert_ids:
                contract._create_default_alerts()
            contract._notify_contract_activated()

    def _notify_contract_activated(self):
        """Email business and legal owners when contract goes Active."""
        self.ensure_one()
        notify = self.env['clm.mail.notify']
        users = self.user_id | self.legal_owner_id
        email_to = notify.emails_for_users(users)
        if not email_to:
            email_to = notify.emails_for_group('clm.group_clm_legal_admin')
        if not email_to:
            return
        notify.send(
            self,
            'clm.mail_template_clm_contract_activated',
            email_to=email_to,
            event_code='contract_activated',
        )

    def _create_default_alerts(self):
        self.ensure_one()
        Alert = self.env['clm.contract.alert']
        for alert_type, lead_days in [('expiry', 90), ('renewal', 60), ('notice_period', 30)]:
            if self.alert_ids.filtered(lambda a, t=alert_type: a.alert_type == t):
                continue
            Alert.create({
                'contract_id': self.id,
                'alert_type': alert_type,
                'lead_days': lead_days,
            })
        self.write({'alerts_configured': True})

    def action_ensure_default_alerts(self):
        """UI helper: seed standard alerts if the Alerts tab is empty."""
        for contract in self:
            contract._create_default_alerts()
        return True

    def action_run_alerts_now(self):
        """
        Demo/admin helper: process alerts for this contract immediately.
        Clears last_notified so due alerts can fire again in a demo.
        """
        self.ensure_one()
        if self.state not in ('active', 'executed', 'expiring_soon'):
            raise UserError(
                'Alerts can only be run for Active, Executed, or Expiring Soon contracts.'
            )
        if not self.alert_ids:
            self._create_default_alerts()
        self.alert_ids.filtered('active').write({'last_notified': False})
        fired = self._process_contract_alerts(force=True)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Contract Alerts',
                'message': (
                    '%s alert(s) fired for %s.' % (fired, self.display_name)
                    if fired else
                    'No due alerts. Check expiry/renewal dates and lead days on the Alerts tab.'
                ),
                'type': 'success' if fired else 'warning',
                'sticky': False,
            },
        }

    def _process_contract_alerts(self, force=False, renotify_days=7):
        """
        Evaluate alerts on the current recordset.
        Returns number of alerts that fired (email/activity).
        """
        today = fields.Date.today()
        fired = 0
        for contract in self:
            if contract.state not in ('active', 'executed', 'expiring_soon'):
                continue
            for alert in contract.alert_ids.filtered('active'):
                target_date = alert._get_target_date()
                if not target_date:
                    continue
                lead = alert.lead_days or 0
                window_start = target_date - timedelta(days=lead)
                if today < window_start:
                    continue
                if alert.last_notified and not force:
                    last_date = fields.Datetime.context_timestamp(
                        contract, alert.last_notified
                    ).date()
                    if (today - last_date).days < renotify_days:
                        continue
                user_id = (contract.legal_owner_id or contract.user_id).id
                alert_label = dict(alert._fields['alert_type'].selection).get(
                    alert.alert_type, alert.alert_type
                )
                if user_id:
                    contract.activity_schedule(
                        'mail.mail_activity_data_todo',
                        summary='NextContract Alert: %s' % alert_label,
                        note=(
                            'Contract %s — %s alert.\n'
                            'Target date: %s\n'
                            'Lead days: %s\n'
                            'Days remaining to target: %s'
                        ) % (
                            contract.display_name,
                            alert_label,
                            target_date,
                            lead,
                            (target_date - today).days,
                        ),
                        user_id=user_id,
                        date_deadline=min(target_date, today + timedelta(days=max(lead, 1))),
                    )
                sent = contract._notify_expiry_alert(alert=alert)
                alert.last_notified = fields.Datetime.now()
                contract.message_post(
                    body=(
                        'Contract alert fired: <b>%s</b> '
                        '(target %s, lead %s days)%s'
                    ) % (
                        alert_label,
                        target_date,
                        lead,
                        ' — email sent' if sent else ' — email skipped/failed',
                    )
                )
                fired += 1

            if contract.expiry_date:
                if contract.expiry_date < today and contract.state in ('active', 'expiring_soon'):
                    contract.write({'state': 'expired'})
                    if hasattr(contract, '_deactivate_contract_alerts'):
                        contract._deactivate_contract_alerts()
                    contract.message_post(body='Contract auto-marked Expired by alert processor.')
                    if hasattr(contract, '_notify_lifecycle_event'):
                        contract._notify_lifecycle_event('contract_expired')
                elif (
                    contract.state == 'active'
                    and contract.expiry_date <= today + timedelta(days=30)
                ):
                    contract.write({'state': 'expiring_soon'})
                    contract.message_post(
                        body='Contract moved to Expiring Soon (within 30 days of expiry).'
                    )
        return fired

    @api.model
    def _cron_process_contract_alerts(self):
        contracts = self.search([('state', 'in', ('active', 'executed', 'expiring_soon'))])
        fired = contracts._process_contract_alerts(force=False)
        _logger.info(
            'NextContract contract alerts cron fired %s alert(s) across %s contract(s).',
            fired, len(contracts),
        )
        return fired

    @api.model
    def action_cron_process_all_alerts(self):
        """Manual menu action: run the same logic as the daily cron."""
        fired = self._cron_process_contract_alerts()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'NextContract Alerts Processed',
                'message': '%s alert(s) fired.' % fired,
                'type': 'success' if fired else 'warning',
                'sticky': False,
            },
        }

