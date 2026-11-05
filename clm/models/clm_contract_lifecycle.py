# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import fields, models
from odoo.exceptions import UserError


class ClmContractLifecycle(models.Model):
    _inherit = 'clm.contract'

    termination_date = fields.Date(string='Termination Date', tracking=True, copy=False)
    termination_reason = fields.Text(string='Termination Reason', tracking=True, copy=False)
    archived_date = fields.Date(string='Archived Date', tracking=True, copy=False)
    archive_reason = fields.Text(string='Archive Reason', tracking=True, copy=False)

    def action_open_renew_wizard(self):
        self.ensure_one()
        self._clm_require_groups('clm.group_clm_legal_admin')
        if self.state not in ('active', 'expiring_soon'):
            raise UserError('Only Active or Expiring Soon contracts can be renewed.')
        return {
            'type': 'ir.actions.act_window',
            'name': 'Renew Contract',
            'res_model': 'clm.contract.renew.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_contract_id': self.id,
                'active_id': self.id,
                'active_model': 'clm.contract',
            },
        }

    def action_open_terminate_wizard(self):
        self.ensure_one()
        self._clm_require_groups('clm.group_clm_legal_admin')
        if self.state not in ('active', 'expiring_soon'):
            raise UserError('Only Active or Expiring Soon contracts can be terminated.')
        return {
            'type': 'ir.actions.act_window',
            'name': 'Terminate Contract',
            'res_model': 'clm.contract.terminate.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_contract_id': self.id,
                'active_id': self.id,
                'active_model': 'clm.contract',
            },
        }

    def action_open_archive_wizard(self):
        self.ensure_one()
        self._clm_require_groups('clm.group_clm_legal_admin')
        if self.state not in ('expired', 'terminated', 'active', 'executed'):
            raise UserError(
                'Only Active, Executed, Expired, or Terminated contracts can be archived.'
            )
        return {
            'type': 'ir.actions.act_window',
            'name': 'Archive Contract',
            'res_model': 'clm.contract.archive.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_contract_id': self.id,
                'active_id': self.id,
                'active_model': 'clm.contract',
            },
        }

    def action_mark_expired(self):
        self._clm_require_groups('clm.group_clm_legal_admin')
        for contract in self:
            if contract.state not in ('active', 'expiring_soon'):
                raise UserError('Only Active or Expiring Soon contracts can be marked expired.')
            contract.write({'state': 'expired'})
            contract._deactivate_contract_alerts()
            contract.message_post(body='Contract marked as Expired.')
            contract._notify_lifecycle_event('contract_expired')
        return True

    def _deactivate_contract_alerts(self):
        self.ensure_one()
        if 'alert_ids' in self._fields and self.alert_ids:
            self.alert_ids.write({'active': False})

    def _notify_lifecycle_event(self, event_code):
        """Email business/legal owners for renew/terminate/archive/expire events."""
        self.ensure_one()
        template_map = {
            'contract_renewed': 'clm.mail_template_clm_contract_renewed',
            'contract_terminated': 'clm.mail_template_clm_contract_terminated',
            'contract_archived': 'clm.mail_template_clm_contract_archived',
            'contract_expired': 'clm.mail_template_clm_contract_expired',
        }
        template_xmlid = template_map.get(event_code)
        if not template_xmlid:
            return
        notify = self.env['clm.mail.notify']
        users = self.user_id | self.legal_owner_id
        email_to = notify.emails_for_users(users)
        if not email_to:
            email_to = notify.emails_for_group('clm.group_clm_legal_admin')
        if not email_to:
            return
        notify.send(
            self,
            template_xmlid,
            email_to=email_to,
            event_code=event_code,
        )

    def _prepare_renewal_successor_vals(self, wizard):
        self.ensure_one()
        name = wizard.new_title or ('%s (Renewed)' % self.name)
        return {
            'name': name,
            'contract_type_id': self.contract_type_id.id,
            'partner_id': self.partner_id.id,
            'department_id': self.department_id.id,
            'user_id': self.user_id.id,
            'legal_owner_id': self.legal_owner_id.id,
            'procurement_owner_id': self.procurement_owner_id.id,
            'finance_owner_id': self.finance_owner_id.id,
            'company_id': self.company_id.id,
            'currency_id': self.currency_id.id,
            'contract_value': self.contract_value,
            'effective_date': wizard.effective_date,
            'expiry_date': wizard.expiry_date,
            'signing_date': wizard.effective_date,
            'notice_period_days': wizard.notice_period_days or self.notice_period_days,
            'auto_renewal': wizard.auto_renewal,
            'termination_for_convenience': self.termination_for_convenience,
            'confidentiality_level': self.confidentiality_level,
            'risk_rating': self.risk_rating,
            'parent_contract_id': self.id,
            'source': 'manual',
            'signed_copy_uploaded': True,
            'state': 'active',
            'remarks': wizard.notes or (
                'Created by renewal of %s.' % (self.contract_ref or self.display_name)
            ),
        }
