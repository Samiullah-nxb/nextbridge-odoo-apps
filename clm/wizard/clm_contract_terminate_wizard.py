# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models
from odoo.exceptions import UserError


class ClmContractTerminateWizard(models.TransientModel):
    _name = 'clm.contract.terminate.wizard'
    _description = 'Terminate Contract Wizard'

    contract_id = fields.Many2one('clm.contract', required=True, readonly=True)
    termination_date = fields.Date(
        string='Termination Date',
        required=True,
        default=fields.Date.context_today,
    )
    reason = fields.Text(string='Termination Reason', required=True)
    confirm = fields.Boolean(
        string='I confirm this contract should be terminated',
        required=True,
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        active_id = self.env.context.get('active_id')
        if active_id and self.env.context.get('active_model') == 'clm.contract':
            res['contract_id'] = active_id
        return res

    def action_confirm_terminate(self):
        self.ensure_one()
        if not self.confirm:
            raise UserError('Please confirm termination before continuing.')
        contract = self.contract_id
        if contract.state not in ('active', 'expiring_soon'):
            raise UserError('Only Active or Expiring Soon contracts can be terminated.')
        contract.write({
            'state': 'terminated',
            'termination_date': self.termination_date,
            'termination_reason': self.reason,
        })
        contract._deactivate_contract_alerts()
        contract.message_post(
            body='Contract terminated on %s. Reason: %s' % (
                self.termination_date, self.reason,
            )
        )
        contract._notify_lifecycle_event('contract_terminated')
        return {'type': 'ir.actions.act_window_close'}


class ClmContractArchiveWizard(models.TransientModel):
    _name = 'clm.contract.archive.wizard'
    _description = 'Archive Contract Wizard'

    contract_id = fields.Many2one('clm.contract', required=True, readonly=True)
    reason = fields.Text(string='Archive Reason')
    confirm = fields.Boolean(
        string='I confirm this contract should be archived',
        required=True,
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        active_id = self.env.context.get('active_id')
        if active_id and self.env.context.get('active_model') == 'clm.contract':
            res['contract_id'] = active_id
        return res

    def action_confirm_archive(self):
        self.ensure_one()
        if not self.confirm:
            raise UserError('Please confirm archive before continuing.')
        contract = self.contract_id
        if contract.state not in ('expired', 'terminated', 'active', 'executed'):
            raise UserError(
                'Only Active, Executed, Expired, or Terminated contracts can be archived.'
            )
        contract.write({
            'state': 'archived',
            'archived_date': fields.Date.context_today(self),
            'archive_reason': self.reason,
        })
        contract._deactivate_contract_alerts()
        contract.message_post(
            body='Contract archived.%s' % (
                (' Reason: %s' % self.reason) if self.reason else ''
            )
        )
        contract._notify_lifecycle_event('contract_archived')
        return {'type': 'ir.actions.act_window_close'}
