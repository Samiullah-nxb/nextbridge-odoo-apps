# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models
from odoo.exceptions import UserError


class ClmActivationChecklistItem(models.Model):
    """Configurable activation gate checks for contracts."""

    _name = 'clm.activation.checklist.item'
    _description = 'NextContract Activation Checklist Item'
    _order = 'sequence, id'

    name = fields.Char(string='Check', required=True)
    code = fields.Char(string='Code', required=True, index=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    required = fields.Boolean(
        string='Required',
        default=True,
        help='If enabled, this check must pass before Activate Contract.',
    )
    check_type = fields.Selection([
        ('field', 'Field is set'),
        ('field_or', 'One of fields is set'),
        ('boolean_true', 'Boolean must be True'),
        ('positive_int', 'Integer greater than zero'),
        ('executed_version', 'Executed document version exists'),
    ], string='Check Type', required=True, default='field')
    field_name = fields.Char(
        string='Field Name',
        help='Technical field name on clm.contract (e.g. partner_id).',
    )
    field_names = fields.Char(
        string='Field Names',
        help='Comma-separated field names for "One of fields is set".',
    )
    help_text = fields.Char(string='Help')

    _code_uniq = models.Constraint(
        'unique(code)',
        'Checklist item code must be unique.',
    )

    def evaluate(self, contract):
        """Return (passed: bool, detail: str)."""
        self.ensure_one()
        check = self.check_type
        if check == 'field':
            if not self.field_name or self.field_name not in contract._fields:
                return False, 'Invalid field configuration'
            value = contract[self.field_name]
            # Empty dates/selections are False; do not treat False as 0 (False == 0 in Python).
            if isinstance(value, models.BaseModel):
                passed = bool(value)
            else:
                passed = value not in (False, None, '')
            return passed, self.field_name if not passed else 'OK'
        if check == 'field_or':
            names = [n.strip() for n in (self.field_names or '').split(',') if n.strip()]
            if not names:
                return False, 'No fields configured'
            for name in names:
                if name in contract._fields and contract[name]:
                    return True, 'OK'
            return False, ' / '.join(names)
        if check == 'boolean_true':
            if not self.field_name or self.field_name not in contract._fields:
                return False, 'Invalid field configuration'
            return bool(contract[self.field_name]), self.field_name
        if check == 'positive_int':
            if not self.field_name or self.field_name not in contract._fields:
                return False, 'Invalid field configuration'
            value = contract[self.field_name] or 0
            return value > 0, self.field_name
        if check == 'executed_version':
            versions = contract.version_ids.filtered(lambda v: v.version_type == 'executed')
            return bool(versions), 'executed version'
        return False, 'Unknown check type'


class ClmContractActivationChecklist(models.Model):
    _inherit = 'clm.contract'

    activation_ready = fields.Boolean(
        string='Activation Ready',
        compute='_compute_activation_checklist',
    )
    activation_missing_count = fields.Integer(
        string='Missing Activation Checks',
        compute='_compute_activation_checklist',
    )
    activation_checklist_html = fields.Html(
        string='Activation Checklist',
        compute='_compute_activation_checklist',
        sanitize=False,
    )

    @api.depends(
        'state', 'partner_id', 'contract_type_id', 'user_id', 'legal_owner_id',
        'contract_value', 'currency_id', 'effective_date', 'expiry_date',
        'auto_renewal', 'signing_date', 'notice_period_days',
        'confidentiality_level', 'risk_rating', 'signed_copy_uploaded',
        'version_ids', 'version_ids.version_type', 'company_id',
    )
    def _compute_activation_checklist(self):
        items = self.env['clm.activation.checklist.item'].sudo().search([
            ('active', '=', True),
        ])
        for contract in self:
            rows = []
            missing = 0
            for item in items:
                passed, detail = item.evaluate(contract)
                if item.required and not passed:
                    missing += 1
                status = 'PASS' if passed else ('REQUIRED' if item.required else 'OPTIONAL')
                color = '#059669' if passed else ('#dc2626' if item.required else '#d97706')
                rows.append(
                    '<tr>'
                    '<td style="padding:6px 8px;border-bottom:1px solid #e5e7eb;">%s</td>'
                    '<td style="padding:6px 8px;border-bottom:1px solid #e5e7eb;color:%s;font-weight:bold;">%s</td>'
                    '<td style="padding:6px 8px;border-bottom:1px solid #e5e7eb;color:#6b7280;">%s</td>'
                    '</tr>' % (
                        item.name,
                        color,
                        status,
                        item.help_text or detail or '',
                    )
                )
            contract.activation_missing_count = missing
            contract.activation_ready = missing == 0
            if not rows:
                contract.activation_checklist_html = (
                    '<p>No activation checklist items are configured.</p>'
                )
            else:
                summary = (
                    '<p style="margin:0 0 8px 0;"><strong>%s</strong> — '
                    '%s required check(s) remaining.</p>'
                ) % (
                    'Ready to activate' if missing == 0 else 'Not ready',
                    missing,
                )
                contract.activation_checklist_html = (
                    '%s<table style="width:100%%;border-collapse:collapse;">'
                    '<thead><tr>'
                    '<th style="text-align:left;padding:6px 8px;border-bottom:2px solid #e5e7eb;">Check</th>'
                    '<th style="text-align:left;padding:6px 8px;border-bottom:2px solid #e5e7eb;">Status</th>'
                    '<th style="text-align:left;padding:6px 8px;border-bottom:2px solid #e5e7eb;">Notes</th>'
                    '</tr></thead><tbody>%s</tbody></table>'
                ) % (summary, ''.join(rows))

    def _get_failed_activation_checks(self):
        self.ensure_one()
        items = self.env['clm.activation.checklist.item'].sudo().search([
            ('active', '=', True),
            ('required', '=', True),
        ])
        failed = []
        for item in items:
            passed, _detail = item.evaluate(self)
            if not passed:
                failed.append(item.name)
        return failed

    def action_open_activation_checklist(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Activation Checklist',
            'res_model': 'clm.contract',
            'res_id': self.id,
            'view_mode': 'form',
            'views': [(False, 'form')],
            'target': 'current',
            'context': {'form_view_initial_mode': 'edit'},
        }
