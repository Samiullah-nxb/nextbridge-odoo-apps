# Part of NextContract. See LICENSE file for full copyright and licensing details.

import json

from odoo import api, fields, models


class ClmPdfPreviewWizard(models.TransientModel):
    _name = 'clm.pdf.preview.wizard'
    _description = 'Contract PDF Preview'

    contract_id = fields.Many2one('clm.contract', required=True, readonly=True)
    version_id = fields.Many2one('clm.contract.version', readonly=True, copy=False)
    attachment_id = fields.Many2one('ir.attachment', required=True, readonly=True)
    banner_message = fields.Text(readonly=True)
    banner_css = fields.Char(default='alert-info', readonly=True)
    warnings_json = fields.Text(readonly=True)
    show_create_version = fields.Boolean(readonly=True)
    content_locked = fields.Boolean(
        related='contract_id.content_locked', readonly=True,
    )
    preview_html = fields.Html(
        string='Preview', compute='_compute_preview_html', sanitize=False,
    )

    @api.depends(
        'contract_id', 'attachment_id', 'banner_message', 'banner_css', 'warnings_json',
    )
    def _compute_preview_html(self):
        for wizard in self:
            if not wizard.contract_id or not wizard.attachment_id:
                wizard.preview_html = False
                continue
            warnings = []
            if wizard.warnings_json:
                try:
                    warnings = json.loads(wizard.warnings_json) or []
                except (TypeError, ValueError):
                    warnings = []
            banner = {
                'message': wizard.banner_message or '',
                'css': wizard.banner_css or 'alert-info',
            }
            wizard.preview_html = wizard.contract_id._clm_wrap_pdf_preview_html(
                wizard.attachment_id, banner, warnings=warnings,
            )

    def action_open_new_version_wizard(self):
        self.ensure_one()
        return self.contract_id.action_open_new_version_wizard()

    def action_download_version_docx(self):
        self.ensure_one()
        if not self.version_id:
            return False
        return self.version_id.action_generate_docx()

    def action_download_version_pdf(self):
        self.ensure_one()
        if not self.version_id:
            return False
        return self.version_id.action_generate_pdf()
