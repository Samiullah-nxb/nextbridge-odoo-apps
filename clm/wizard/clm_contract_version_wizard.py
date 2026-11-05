# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models
from odoo.exceptions import UserError

from ..services.final_contract_builder import comparable_snapshot

# Version types that describe a working draft and can be carried forward.
_WORKING_VERSION_TYPES = (
    'internal_draft', 'legal_draft', 'business_draft', 'finance_draft',
    'procurement_draft', 'negotiated_draft', 'final_legal_draft',
)


class ClmContractVersionWizard(models.TransientModel):
    """Freeze the contract as it is now into a new version."""
    _name = 'clm.contract.version.wizard'
    _description = 'Create Contract Version'

    contract_id = fields.Many2one(
        'clm.contract', string='Contract', required=True, readonly=True, ondelete='cascade'
    )
    parent_version_id = fields.Many2one(
        'clm.contract.version', string='Derived From',
        domain="[('contract_id', '=', contract_id)]",
        help='The version this new version is derived from.'
    )
    version_type = fields.Selection(
        selection='_selection_version_type', string='Version Type',
        required=True, default='internal_draft',
    )
    change_classification = fields.Selection(
        selection='_selection_change_classification', string='Change Classification',
        required=True, default='none',
    )
    change_summary = fields.Text(string='Change Summary', required=True)
    has_changes = fields.Boolean(compute='_compute_has_changes')

    @api.model
    def _selection_version_type(self):
        field = self.env['clm.contract.version']._fields['version_type']
        return [
            (value, label) for value, label in field._description_selection(self.env)
            if value != 'executed'
        ]

    @api.model
    def _selection_change_classification(self):
        field = self.env['clm.contract.version']._fields['change_classification']
        return field._description_selection(self.env)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        contract = self.env['clm.contract'].browse(
            res.get('contract_id') or self.env.context.get('default_contract_id')
        )
        if contract.exists():
            res['contract_id'] = contract.id
            parent = contract.current_version_id
            if parent:
                res.setdefault('parent_version_id', parent.id)
                if parent.version_type in _WORKING_VERSION_TYPES:
                    res['version_type'] = parent.version_type
        return res

    @api.depends('contract_id', 'parent_version_id')
    def _compute_has_changes(self):
        for wizard in self:
            parent_snapshot = wizard.parent_version_id.sudo().content_snapshot
            if not wizard.contract_id or not parent_snapshot:
                wizard.has_changes = True
                continue
            wizard.has_changes = comparable_snapshot(
                wizard.contract_id._clm_capture_content()
            ) != comparable_snapshot(parent_snapshot)

    def action_create_version(self):
        self.ensure_one()
        contract = self.contract_id
        contract._clm_check_content_editable()
        snapshot = contract._clm_capture_content()
        parent = self.parent_version_id
        parent_snapshot = parent.sudo().content_snapshot
        if parent_snapshot and comparable_snapshot(parent_snapshot) == comparable_snapshot(snapshot):
            raise UserError(
                'Nothing has changed since version v%s, so no new version was created.\n\n'
                'Edit the contract first, then create the new version.' % parent.version_number
            )
        version = contract._clm_create_content_version(
            version_type=self.version_type,
            parent_version=parent,
            change_summary=self.change_summary,
            change_classification=self.change_classification,
            snapshot=snapshot,
        )
        return {
            'type': 'ir.actions.act_window',
            'name': 'Version v%s' % version.version_number,
            'res_model': 'clm.contract.version',
            'res_id': version.id,
            'view_mode': 'form',
            'target': 'current',
        }
