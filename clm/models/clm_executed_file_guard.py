# Part of NextContract. See LICENSE file for full copyright and licensing details.
"""Keep the file of an executed (signed) version intact.

``clm.contract.version`` already refuses to be edited or deleted once it is
executed, but the signed file itself lives in a ``documents.document`` and its
``ir.attachment``. Those are ordinary records: anyone allowed to write the
document (every CLM user who can see the contract) could replace or delete the
signed PDF without touching the version row, and the contract would silently
show a different "signed" document. These guards close that gap.

System code (``sudo``) is not blocked, so generation and migration keep working.
"""
from odoo import api, models
from odoo.exceptions import UserError

_DOCUMENT_LOCKED_FIELDS = {
    'attachment_id', 'datas', 'raw', 'name', 'mimetype', 'res_model', 'res_id',
}
_ATTACHMENT_LOCKED_FIELDS = {
    'datas', 'raw', 'db_datas', 'store_fname', 'name', 'mimetype', 'res_model', 'res_id',
}
_MESSAGE = (
    "This file is the signed document of an executed contract version and "
    "cannot be changed or deleted. Create an amendment instead."
)


class DocumentsDocument(models.Model):
    _inherit = 'documents.document'

    def _clm_executed_documents(self):
        """The subset of ``self`` that is the file of an executed version."""
        if self.env.su or not self.ids:
            return self.browse()
        versions = self.env['clm.contract.version'].sudo().search([
            ('version_type', '=', 'executed'), ('document_id', 'in', self.ids),
        ])
        return versions.document_id

    def write(self, vals):
        if _DOCUMENT_LOCKED_FIELDS & set(vals) or vals.get('active') is False:
            if self._clm_executed_documents():
                raise UserError(_MESSAGE)
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _clm_unlink_except_executed(self):
        if self._clm_executed_documents():
            raise UserError(_MESSAGE)


class IrAttachment(models.Model):
    _inherit = 'ir.attachment'

    def _clm_executed_attachments(self):
        if self.env.su or not self.ids:
            return self.browse()
        versions = self.env['clm.contract.version'].sudo().search([
            ('version_type', '=', 'executed'), ('document_id.attachment_id', 'in', self.ids),
        ])
        return versions.document_id.attachment_id

    def write(self, vals):
        if _ATTACHMENT_LOCKED_FIELDS & set(vals) and self._clm_executed_attachments():
            raise UserError(_MESSAGE)
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _clm_unlink_except_executed(self):
        if self._clm_executed_attachments():
            raise UserError(_MESSAGE)
