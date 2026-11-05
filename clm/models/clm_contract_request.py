# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models, fields, api
from odoo.exceptions import UserError

class ClmContractRequest(models.Model):
    _name = 'clm.contract.request'
    _description = 'Contract Request'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    request_ref = fields.Char(
        string='Request ID', readonly=True, copy=False, index=True, default='New',
    )
    name = fields.Char(string='Request Title', required=True)
    state = fields.Selection([
        ('new', 'New'),
        ('submitted', 'Submitted'),
        ('intake', 'Legal Intake'),
        ('accepted', 'Accepted'),
        ('rejected', 'Rejected'),
        ('converted', 'Converted')
    ], string='Status', default='new')
    contract_id = fields.Many2one('clm.contract', string='Generated Contract', readonly=True)
    requester_id = fields.Many2one('res.users', string='Requester', default=lambda self: self.env.user, readonly=True)
    
    department_id = fields.Many2one('hr.department', string='Department')
    company_id = fields.Many2one('res.company', string='Company', default=lambda self: self.env.company, required=True)
    partner_id = fields.Many2one('res.partner', string='Counterparty')
    partner_signatory_id = fields.Many2one(
        'res.partner',
        string='Counterparty Signatory',
        domain="[('id', 'child_of', partner_id), ('is_company', '=', False)]",
        help='Person who will sign for the counterparty company. '
             'Carried to the contract on conversion.',
    )
    contract_type_id = fields.Many2one('clm.contract.type', string='Contract Type')
    template_id = fields.Many2one(
        'clm.template', string='Contract Template',
        domain=[('state', '=', 'published')],
        help='Standard template requested for this contract. Carried over to '
             'the contract on conversion.'
    )
    purpose = fields.Text(string='Purpose / Business Need')
    estimated_value = fields.Monetary(string='Estimated Value')
    currency_id = fields.Many2one('res.currency', string='Currency', default=lambda self: self.env.company.currency_id)
    start_date = fields.Date(string='Expected Start Date')
    expected_execution_date = fields.Date(string='Expected Execution Date')
    business_owner_id = fields.Many2one('res.users', string='Business Owner', default=lambda self: self.env.user)
    procurement_owner_id = fields.Many2one('res.users', string='Procurement Owner')
    supporting_document_ids = fields.Many2many('ir.attachment', string='Supporting Documents')
    required_legal_action = fields.Selection([
        ('new_contract', 'New Contract'),
        ('amendment', 'Amendment'),
        ('renewal', 'Renewal'),
        ('termination', 'Termination')
    ], string='Legal Action Needed')
    internal_approvals = fields.Text(string='Internal Approvals Required')
    reference = fields.Char(string='External Reference')
    urgency = fields.Selection([
        ('routine', 'Routine'),
        ('standard', 'Standard'),
        ('urgent', 'Urgent'),
        ('critical', 'Critical')
    ], string='Urgency', default='routine')
    assigned_reviewer_id = fields.Many2one(
        'res.users', string='Assigned Legal Reviewer', tracking=True,
    )
    rejection_reason = fields.Text(string='Rejection Reason')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('request_ref', 'New') == 'New':
                vals['request_ref'] = (
                    self.env['ir.sequence'].sudo().next_by_code('clm.contract.request')
                    or 'New'
                )
        return super().create(vals_list)

    def write(self, vals):
        for rec in self:
            # Legal Reviewers (and Admins, who imply that role) screen requests.
            if rec.state not in ('new', 'rejected') and not self.env.user.has_group('clm.group_clm_legal'):
                raise UserError("You cannot modify a request after it has been submitted.")
        return super(ClmContractRequest, self).write(vals)

    @api.onchange('partner_id')
    def _onchange_partner_id_clear_signatory(self):
        if not self.partner_id:
            self.partner_signatory_id = False
            return
        signatory = self.partner_signatory_id
        if not signatory:
            return
        allowed = self.env['res.partner'].search([
            ('id', 'child_of', self.partner_id.id),
            ('is_company', '=', False),
        ])
        if signatory not in allowed:
            self.partner_signatory_id = False

    def action_submit(self):
        for rec in self:
            if rec.state != 'new':
                raise UserError('You can only submit new requests.')
            if rec.requester_id != self.env.user and not self.env.user.has_group('clm.group_clm_legal_admin'):
                raise UserError('Only the requester can submit this request.')
            rec.state = 'submitted'
            rec.message_post(body='Contract request submitted for legal intake review.')
            rec._notify_intake_submitted()

    def _notify_intake_submitted(self):
        """Email Legal (assigned reviewer if set, else Legal Admin group)."""
        self.ensure_one()
        notify = self.env['clm.mail.notify']
        if self.assigned_reviewer_id and self.assigned_reviewer_id.email:
            email_to = self.assigned_reviewer_id.email
        else:
            email_to = notify.emails_for_group('clm.group_clm_legal_admin')
            if not email_to:
                email_to = notify.emails_for_group('clm.group_clm_legal')
        if not email_to:
            return
        notify.send(self, 'clm.mail_template_clm_intake_submitted', email_to=email_to)

    def action_start_intake(self):
        for rec in self:
            if rec.state != 'submitted':
                raise UserError('Only submitted requests can move to legal intake.')
            rec.state = 'intake'
            rec.message_post(body='Legal intake review started.')

    def action_accept(self):
        for rec in self:
            if rec.state not in ('submitted', 'intake'):
                raise UserError('Only submitted or intake requests can be accepted.')
            rec.state = 'accepted'
            rec.message_post(body='Contract request accepted by Legal.')
            rec._notify_intake_decision('accepted')

    def action_reject(self):
        for rec in self:
            if rec.state not in ('submitted', 'intake'):
                raise UserError('Only submitted or intake requests can be rejected.')
            rec.state = 'rejected'
            body = 'Contract request rejected by Legal.'
            if rec.rejection_reason:
                body += f'\nReason: {rec.rejection_reason}'
            rec.message_post(body=body)
            rec._notify_intake_decision('rejected')

    def _notify_intake_decision(self, decision):
        """Email requester about accept/reject."""
        self.ensure_one()
        email_to = self.requester_id.email or (
            self.business_owner_id.email if self.business_owner_id else False
        )
        if not email_to:
            return
        template = (
            'clm.mail_template_clm_intake_accepted'
            if decision == 'accepted'
            else 'clm.mail_template_clm_intake_rejected'
        )
        self.env['clm.mail.notify'].send(self, template, email_to=email_to)

    def _notify_intake_converted(self):
        """Email requester/business owner after conversion to contract."""
        self.ensure_one()
        notify = self.env['clm.mail.notify']
        users = self.requester_id | self.business_owner_id
        email_to = notify.emails_for_users(users)
        if not email_to:
            return
        notify.send(self, 'clm.mail_template_clm_intake_converted', email_to=email_to)

    def action_open_conversion_wizard(self):
        self.ensure_one()
        if self.state != 'accepted':
            raise UserError("Only accepted requests can be converted.")
            
        return {
            'name': 'Convert to Contract',
            'type': 'ir.actions.act_window',
            'res_model': 'clm.request.convert.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'active_id': self.id, 'active_model': self._name},
        }
