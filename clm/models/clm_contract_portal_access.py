# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models, fields, api

class ClmContractPortalAccess(models.Model):
    """
    Explicit portal access to a specific contract.
    Counterparties only see contracts where they have an active access record.
    """
    _name = 'clm.contract.portal.access'
    _description = 'Contract Portal Access'

    contract_id = fields.Many2one(
        'clm.contract', string='Contract', required=True, ondelete='cascade',
        index=True
    )
    partner_id = fields.Many2one(
        'res.partner', string='Partner', required=True, ondelete='cascade',
        index=True
    )
    user_id = fields.Many2one(
        'res.users', string='Portal User',
        compute='_compute_user_id', store=True
    )
    
    start_date = fields.Datetime(string='Start Date', default=fields.Datetime.now, required=True)
    expiry_date = fields.Datetime(string='Expiry Date')
    
    can_view = fields.Boolean(string='Can View', default=True)
    can_download = fields.Boolean(string='Can Download', default=True)
    can_upload = fields.Boolean(string='Can Upload Revisions', default=True)
    can_comment = fields.Boolean(string='Can Comment', default=True)
    shared_version_ids = fields.Many2many('clm.contract.version', string='Shared Versions')

    def is_active(self):
        self.ensure_one()
        now = fields.Datetime.now()
        if self.start_date and now < self.start_date:
            return False
        if self.expiry_date and now > self.expiry_date:
            return False
        return True

    _unique_contract_partner = models.Constraint('unique(contract_id, partner_id)', 'A partner can only have one access record per contract.')

    @api.depends('partner_id', 'partner_id.user_ids')
    def _compute_user_id(self):
        # Depending on partner_id.user_ids matters: a counterparty is often
        # invited to the portal *after* the access record was created.
        for record in self:
            if record.partner_id:
                # Find if the partner has an associated portal user
                user = self.env['res.users'].sudo().search(
                    [('partner_id', '=', record.partner_id.id)], limit=1)
                record.user_id = user.id if user else False
            else:
                record.user_id = False

class ClmContractPortalInherit(models.Model):
    _inherit = 'clm.contract'
    
    portal_access_ids = fields.One2many('clm.contract.portal.access', 'contract_id', string='Portal Access Records')
