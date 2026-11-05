# Part of NextContract. See LICENSE file for full copyright and licensing details.

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import UserError


class ClmContractRenewWizard(models.TransientModel):
    _name = 'clm.contract.renew.wizard'
    _description = 'Renew Contract Wizard'

    contract_id = fields.Many2one('clm.contract', required=True, readonly=True)
    renewal_mode = fields.Selection([
        ('successor', 'Create renewed contract (recommended)'),
        ('in_place', 'Renew this contract in place'),
    ], string='Renewal Mode', required=True, default='successor')
    new_title = fields.Char(string='New Contract Title')
    effective_date = fields.Date(string='New Effective Date', required=True)
    expiry_date = fields.Date(string='New Expiry Date')
    notice_period_days = fields.Integer(string='Notice Period (Days)')
    auto_renewal = fields.Boolean(string='Auto-Renewal')
    notes = fields.Text(string='Renewal Notes')

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        active_id = self.env.context.get('active_id')
        if active_id and self.env.context.get('active_model') == 'clm.contract':
            contract = self.env['clm.contract'].browse(active_id)
            res['contract_id'] = contract.id
            res['new_title'] = '%s (Renewed)' % contract.name
            res['notice_period_days'] = contract.notice_period_days or 30
            res['auto_renewal'] = contract.auto_renewal
            # Default: start day after current expiry, or today; +1 year expiry
            today = fields.Date.context_today(self)
            start = (contract.expiry_date + relativedelta(days=1)) if contract.expiry_date else today
            res['effective_date'] = start
            if not contract.auto_renewal:
                res['expiry_date'] = start + relativedelta(years=1)
        return res

    def action_confirm_renew(self):
        self.ensure_one()
        contract = self.contract_id
        if contract.state not in ('active', 'expiring_soon'):
            raise UserError('Only Active or Expiring Soon contracts can be renewed.')
        if self.expiry_date and self.expiry_date < self.effective_date:
            raise UserError('New Expiry Date must be on or after New Effective Date.')
        if self.renewal_mode == 'in_place' and not self.expiry_date and not self.auto_renewal:
            raise UserError('Provide a New Expiry Date, or enable Auto-Renewal.')

        if self.renewal_mode == 'in_place':
            vals = {
                'effective_date': self.effective_date,
                'expiry_date': self.expiry_date,
                'renewal_date': fields.Date.context_today(self),
                'notice_period_days': self.notice_period_days or contract.notice_period_days,
                'auto_renewal': self.auto_renewal,
                'state': 'active',
            }
            # active/expiring_soon → renewed → active (two steps if currently active)
            if contract.state == 'active':
                contract.write({'state': 'renewed', 'renewal_date': fields.Date.context_today(self)})
            elif contract.state == 'expiring_soon':
                contract.write({'state': 'renewed', 'renewal_date': fields.Date.context_today(self)})
            contract.write(vals)
            if self.notes:
                contract.message_post(body='Contract renewed in place. %s' % self.notes)
            else:
                contract.message_post(body='Contract renewed in place with updated term dates.')
            contract._notify_lifecycle_event('contract_renewed')
            return {'type': 'ir.actions.act_window_close'}

        # Successor mode
        if not self.expiry_date and not self.auto_renewal:
            raise UserError('Provide a New Expiry Date, or enable Auto-Renewal for the successor.')

        successor_vals = contract._prepare_renewal_successor_vals(self)
        # Legal Admin bypass so successor can start as Active in the repository
        successor = self.env['clm.contract'].with_context(migration_mode=True).create(successor_vals)
        if hasattr(successor, '_create_default_alerts'):
            successor._create_default_alerts()

        contract.write({
            'state': 'renewed',
            'renewal_date': fields.Date.context_today(self),
        })
        contract._deactivate_contract_alerts()
        contract.message_post(
            body=(
                'Contract renewed. Successor created: '
                '<a href="#" data-oe-model="clm.contract" data-oe-id="%s">%s</a>'
            ) % (successor.id, successor.display_name)
        )
        successor.message_post(
            body=(
                'Created by renewal of '
                '<a href="#" data-oe-model="clm.contract" data-oe-id="%s">%s</a>. %s'
            ) % (
                contract.id,
                contract.display_name,
                self.notes or '',
            )
        )
        contract._notify_lifecycle_event('contract_renewed')
        successor._notify_lifecycle_event('contract_renewed')

        return {
            'type': 'ir.actions.act_window',
            'name': 'Renewed Contract',
            'res_model': 'clm.contract',
            'res_id': successor.id,
            'view_mode': 'form',
            'target': 'current',
        }
