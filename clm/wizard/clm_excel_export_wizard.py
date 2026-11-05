# Part of NextContract. See LICENSE file for full copyright and licensing details.

import base64
import io
from datetime import datetime

from odoo import api, fields, models
from odoo.exceptions import UserError

try:
    import xlsxwriter
except ImportError:  # pragma: no cover
    xlsxwriter = None


class ClmExcelExportWizard(models.TransientModel):
    _name = 'clm.excel.export.wizard'
    _description = 'NextContract Excel Export'

    export_type = fields.Selection([
        ('contracts', 'Contracts'),
        ('requests', 'Contract Requests (Intake)'),
        ('obligations', 'Obligations'),
        ('tasks', 'Workflow Tasks'),
    ], string='Export', required=True, default='contracts')

    state_filter = fields.Selection([
        ('all', 'All Statuses'),
        ('active_pipeline', 'Active Pipeline (not archived/cancelled)'),
        ('active_only', 'Active Contracts Only'),
        ('draft_review', 'Draft / In Review'),
    ], string='Status Filter', default='all', required=True)

    date_from = fields.Date(string='From Date')
    date_to = fields.Date(string='To Date')
    contract_type_id = fields.Many2one('clm.contract.type', string='Contract Type')
    partner_id = fields.Many2one('res.partner', string='Counterparty')

    file_data = fields.Binary(string='Excel File', readonly=True)
    file_name = fields.Char(string='Filename', readonly=True)
    state = fields.Selection([
        ('choose', 'Choose'),
        ('done', 'Done'),
    ], default='choose')

    record_count = fields.Integer(string='Records Exported', readonly=True)

    def action_export(self):
        self.ensure_one()
        if not xlsxwriter:
            raise UserError(
                "The 'xlsxwriter' Python library is required for Excel export."
            )

        exporters = {
            'contracts': self._export_contracts,
            'requests': self._export_requests,
            'obligations': self._export_obligations,
            'tasks': self._export_tasks,
        }
        headers, rows, sheet_name, prefix = exporters[self.export_type]()
        if not rows and not headers:
            raise UserError('No records found for the selected filters.')

        content = self._build_xlsx(headers, rows, sheet_name)
        filename = '%s_%s.xlsx' % (prefix, datetime.now().strftime('%Y%m%d_%H%M%S'))
        self.write({
            'file_data': content,
            'file_name': filename,
            'record_count': len(rows),
            'state': 'done',
        })
        return {
            'type': 'ir.actions.act_window',
            'name': 'Excel Export',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_export_again(self):
        self.ensure_one()
        self.write({
            'file_data': False,
            'file_name': False,
            'record_count': 0,
            'state': 'choose',
        })
        return {
            'type': 'ir.actions.act_window',
            'name': 'Excel Export',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def _build_xlsx(self, headers, rows, sheet_name):
        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})
        sheet = workbook.add_worksheet(sheet_name[:31])

        header_fmt = workbook.add_format({
            'bold': True,
            'bg_color': '#4F46E5',
            'font_color': '#FFFFFF',
            'border': 1,
        })
        cell_fmt = workbook.add_format({'border': 1, 'valign': 'top'})
        date_fmt = workbook.add_format({
            'border': 1, 'num_format': 'yyyy-mm-dd', 'valign': 'top',
        })

        for col, header in enumerate(headers):
            sheet.write(0, col, header, header_fmt)
            sheet.set_column(col, col, max(14, min(40, len(header) + 4)))

        for row_idx, row in enumerate(rows, start=1):
            for col_idx, value in enumerate(row):
                if isinstance(value, datetime):
                    sheet.write_datetime(row_idx, col_idx, value, date_fmt)
                elif hasattr(value, 'strftime'):
                    sheet.write(row_idx, col_idx, value.strftime('%Y-%m-%d'), cell_fmt)
                elif value is False or value is None:
                    sheet.write(row_idx, col_idx, '', cell_fmt)
                else:
                    sheet.write(row_idx, col_idx, value, cell_fmt)

        workbook.close()
        return base64.b64encode(output.getvalue())

    def _selection_label(self, model, field_name, value):
        if not value:
            return ''
        field = self.env[model]._fields.get(field_name)
        if not field or not field.selection:
            return value
        selection = field.selection
        if callable(selection):
            selection = selection(self.env[model])
        return dict(selection).get(value, value)

    def _contract_domain(self):
        domain = []
        if self.contract_type_id:
            domain.append(('contract_type_id', '=', self.contract_type_id.id))
        if self.partner_id:
            domain.append(('partner_id', '=', self.partner_id.id))
        if self.date_from:
            domain.append(('create_date', '>=', fields.Datetime.to_datetime(self.date_from)))
        if self.date_to:
            domain.append((
                'create_date', '<=',
                fields.Datetime.to_datetime(self.date_to).replace(hour=23, minute=59, second=59),
            ))
        if self.state_filter == 'active_pipeline':
            domain.append(('state', 'not in', ['archived', 'cancelled']))
        elif self.state_filter == 'active_only':
            domain.append(('state', 'in', ['active', 'expiring_soon']))
        elif self.state_filter == 'draft_review':
            domain.append(('state', 'in', [
                'draft', 'in_review', 'awaiting_business', 'awaiting_finance',
                'awaiting_procurement', 'awaiting_compliance', 'internal_review_done',
            ]))
        return domain

    def _export_contracts(self):
        contracts = self.env['clm.contract'].search(
            self._contract_domain(), order='contract_ref desc',
        )
        headers = [
            'Contract ID', 'Title', 'Contract Type', 'Counterparty', 'Status',
            'Business Owner', 'Legal Owner', 'Department', 'Contract Value',
            'Currency', 'Effective Date', 'Expiry Date', 'Signing Date',
            'Risk Rating', 'Confidentiality', 'Company',
        ]
        rows = []
        for c in contracts:
            rows.append([
                c.contract_ref or '',
                c.name or '',
                c.contract_type_id.name or '',
                c.partner_id.name or '',
                self._selection_label('clm.contract', 'state', c.state),
                c.user_id.name or '',
                c.legal_owner_id.name or '',
                c.department_id.name or '',
                c.contract_value or 0.0,
                c.currency_id.name or '',
                c.effective_date or '',
                c.expiry_date or '',
                c.signing_date or '',
                self._selection_label('clm.contract', 'risk_rating', c.risk_rating),
                self._selection_label('clm.contract', 'confidentiality_level', c.confidentiality_level),
                c.company_id.name or '',
            ])
        return headers, rows, 'Contracts', 'clm_contracts'

    def _export_requests(self):
        if 'clm.contract.request' not in self.env:
            raise UserError('Intake module is not installed.')
        domain = []
        if self.partner_id:
            domain.append(('partner_id', '=', self.partner_id.id))
        if self.contract_type_id:
            domain.append(('contract_type_id', '=', self.contract_type_id.id))
        if self.date_from:
            domain.append(('create_date', '>=', fields.Datetime.to_datetime(self.date_from)))
        if self.date_to:
            domain.append((
                'create_date', '<=',
                fields.Datetime.to_datetime(self.date_to).replace(hour=23, minute=59, second=59),
            ))
        if self.state_filter == 'active_pipeline':
            domain.append(('state', 'not in', ['rejected']))
        elif self.state_filter == 'draft_review':
            domain.append(('state', 'in', ['new', 'submitted', 'intake']))
        requests = self.env['clm.contract.request'].search(domain, order='request_ref desc')
        headers = [
            'Request ID', 'Title', 'Status', 'Requester', 'Business Owner',
            'Counterparty', 'Contract Type', 'Urgency', 'Estimated Value',
            'Currency', 'Department', 'Expected Start', 'Expected Execution',
            'Generated Contract', 'Company',
        ]
        rows = []
        for r in requests:
            rows.append([
                r.request_ref or '',
                r.name or '',
                self._selection_label('clm.contract.request', 'state', r.state),
                r.requester_id.name or '',
                r.business_owner_id.name or '',
                r.partner_id.name or '',
                r.contract_type_id.name or '',
                self._selection_label('clm.contract.request', 'urgency', r.urgency),
                r.estimated_value or 0.0,
                r.currency_id.name or '',
                r.department_id.name or '',
                r.start_date or '',
                r.expected_execution_date or '',
                r.contract_id.display_name or '',
                r.company_id.name or '',
            ])
        return headers, rows, 'Intake Requests', 'clm_requests'

    def _export_obligations(self):
        if 'clm.obligation' not in self.env:
            raise UserError('Execution module is not installed.')
        domain = []
        if self.partner_id:
            domain.append(('contract_id.partner_id', '=', self.partner_id.id))
        if self.contract_type_id:
            domain.append(('contract_id.contract_type_id', '=', self.contract_type_id.id))
        if self.date_from:
            domain.append(('due_date', '>=', self.date_from))
        if self.date_to:
            domain.append(('due_date', '<=', self.date_to))
        if self.state_filter == 'active_pipeline':
            domain.append(('status', 'in', ['pending', 'in_progress', 'overdue']))
        elif self.state_filter == 'active_only':
            domain.append(('status', '=', 'overdue'))
        obligations = self.env['clm.obligation'].search(domain, order='due_date asc')
        headers = [
            'Contract', 'Counterparty', 'Obligation', 'Type', 'Owner',
            'Due Date', 'Status', 'Priority', 'Completed Date',
        ]
        rows = []
        for o in obligations:
            rows.append([
                o.contract_id.display_name or '',
                o.contract_id.partner_id.name or '',
                o.name or '',
                self._selection_label('clm.obligation', 'obligation_type', o.obligation_type),
                o.owner_id.name or '',
                o.due_date or '',
                self._selection_label('clm.obligation', 'status', o.status),
                'High' if o.priority == '1' else 'Normal',
                o.completed_date or '',
            ])
        return headers, rows, 'Obligations', 'clm_obligations'

    def _export_tasks(self):
        if 'clm.workflow.task' not in self.env:
            raise UserError('Workflow module is not installed.')
        domain = []
        if self.partner_id:
            domain.append(('contract_id.partner_id', '=', self.partner_id.id))
        if self.contract_type_id:
            domain.append(('contract_id.contract_type_id', '=', self.contract_type_id.id))
        if self.date_from:
            domain.append(('create_date', '>=', fields.Datetime.to_datetime(self.date_from)))
        if self.date_to:
            domain.append((
                'create_date', '<=',
                fields.Datetime.to_datetime(self.date_to).replace(hour=23, minute=59, second=59),
            ))
        if self.state_filter == 'active_pipeline':
            domain.append(('status', 'in', ['pending', 'in_progress']))
        elif self.state_filter == 'draft_review':
            domain.append(('status', '=', 'pending'))
        tasks = self.env['clm.workflow.task'].search(domain, order='id desc')
        headers = [
            'Task', 'Task Type', 'Contract', 'Counterparty', 'Assignee',
            'Status', 'Required', 'Due Date', 'Completion Date',
        ]
        rows = []
        for t in tasks:
            rows.append([
                t.name or '',
                self._selection_label('clm.workflow.task', 'task_type', t.task_type),
                t.contract_id.display_name or '',
                t.contract_id.partner_id.name or '',
                t.assigned_user_id.name or '',
                self._selection_label('clm.workflow.task', 'status', t.status),
                'Yes' if t.is_required else 'No',
                t.due_date or '',
                t.completion_date or '',
            ])
        return headers, rows, 'Workflow Tasks', 'clm_tasks'
