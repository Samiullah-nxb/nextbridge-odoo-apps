# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import http
from odoo.exceptions import AccessError, MissingError, UserError
from odoo.http import request

from ..models.clm_template_docx_editor import DOCX_MIMETYPE, DocxEditorConflict


class ClmDocxEditorController(http.Controller):

    def _template(self, template_id):
        template = request.env['clm.template'].browse(template_id)
        if not template.exists():
            raise MissingError(request.env._('This template no longer exists.'))
        template.check_access('read')
        return template

    @staticmethod
    def _message(error):
        return str(error.args[0] if error.args else error)

    def _error(self, error, status, **extra):
        return request.make_json_response({'error': self._message(error), **extra}, status=status)

    @http.route('/clm_docx_editor/template/<int:template_id>/document', type='http',
                auth='user', methods=['GET'])
    def template_document(self, template_id):
        try:
            data = self._template(template_id)._clm_docx_editor_bytes()
        except (AccessError, MissingError) as error:
            return self._error(error, 403)
        except UserError as error:
            return self._error(error, 400)
        return request.make_response(data, headers=[
            ('Content-Type', DOCX_MIMETYPE),
            ('Content-Length', str(len(data))),
            ('Cache-Control', 'no-store'),
        ])

    @http.route('/clm_docx_editor/template/<int:template_id>/validate', type='http',
                auth='user', methods=['POST'])
    def template_validate(self, template_id, document):
        try:
            result = self._template(template_id).clm_validate_docx_editor_bytes(document.read())
        except (AccessError, MissingError) as error:
            return self._error(error, 403)
        except UserError as error:
            return self._error(error, 400, errors=[self._message(error)], warnings=[])
        return request.make_json_response(result)

    @http.route('/clm_docx_editor/template/<int:template_id>/save', type='http',
                auth='user', methods=['POST'])
    def template_save(self, template_id, document, mode='inplace', version_token=None,
                      change_summary=None):
        try:
            template = self._template(template_id)
            version, created_new, warnings = template._clm_save_from_docx_editor(
                document.read(), mode=mode, version_token=version_token or None,
                change_summary=change_summary,
            )
        except (AccessError, MissingError) as error:
            request.env.cr.rollback()
            return self._error(error, 403)
        except DocxEditorConflict as error:
            request.env.cr.rollback()
            return self._error(error, 409, code='conflict')
        except UserError as error:
            request.env.cr.rollback()
            return self._error(error, 400, errors=self._message(error).split('\n'), warnings=[])
        return request.make_json_response({
            'template_id': template_id,
            'version_id': version.id,
            'version_label': version.name,
            'version_token': template._clm_docx_editor_version_token(),
            'version_count': len(template.base_docx_version_ids),
            'created_new_version': created_new,
            'active_is_immutable': bool(version.is_original or version.is_used),
            'warnings': warnings,
        })
