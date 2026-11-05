# Part of NextContract. See LICENSE file for full copyright and licensing details.
"""
Word editor for the Document Template tab.

The editor itself is the compiled docunext Word editor, bundled in this
module's ``static/editor/`` and loaded in an iframe from
``/clm/static/editor/``. This module hosts it for Library
templates: it hands the template's active Document Template
file to the editor and stores what comes back, either onto the active
Template Version ("Save") or as a new one ("Save as New Version"). The
original Template Version and any version used by a contract are never
overwritten: an in-place save on one of them becomes a new version instead
(see clm_library's ``_persist_base_docx_bytes``).

Placeholders are checked with the same validation as the Document Template
tab's own editor, over the body (including tables), headers and footers,
since docxtpl renders tags in all of them.
"""

import functools
import hashlib
import io
import zipfile

from lxml import etree

from odoo import models
from odoo.exceptions import AccessError, UserError
from odoo.tools.misc import file_path

DOCX_MIMETYPE = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
# Same ceiling as portal uploads.
MAX_DOCX_SIZE = 50 * 1024 * 1024
MAX_DOCX_UNCOMPRESSED = 500 * 1024 * 1024
_XML_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True)

# The compiled docunext editor, bundled in this module's static/editor/.
EDITOR_INDEX = '/clm/static/editor/index.html'

_W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
_MC_FALLBACK = '{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback'
SAVE_MODES = ('inplace', 'new')


class DocxEditorConflict(UserError):
    """The active Template Version changed after the editor opened it."""


@functools.cache
def _editor_build_id():
    # index.html names the build's content-hashed assets, so its digest changes
    # with every rebuild, even when the module version is not bumped.
    with open(file_path(EDITOR_INDEX.lstrip('/')), 'rb') as index:
        return hashlib.sha256(index.read()).hexdigest()[:12]


def _is_text_part(name):
    if name == 'word/document.xml':
        return True
    return name.startswith(('word/header', 'word/footer')) and name.endswith('.xml')


class ClmTemplate(models.Model):
    _inherit = 'clm.template'

    # -------------------------------------------------------------------------
    # Opening
    # -------------------------------------------------------------------------

    def action_open_docx_editor(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.client',
            'tag': 'clm_docx_editor',
            'name': self.env._('Word Editor · %s', self.name),
            # active_id survives a browser reload; params do not.
            'context': {'active_id': self.id},
            'params': {'template_id': self.id},
        }

    def _clm_docx_editor_edit_block(self):
        """Why this user cannot save edits of this template, or '' if they can."""
        self.ensure_one()
        if not self.base_document_id:
            return self.env._('Set a Document Template first.')
        try:
            self._check_template_edit_permission()
        except AccessError as error:
            return str(error)
        try:
            self.check_access('write')
        except AccessError:
            return self.env._(
                'You can read this template but are not allowed to edit it.'
            )
        return ''

    def _clm_docx_editor_url(self):
        """The editor page, versioned so a new editor build busts the browser cache."""
        return '%s?v=%s' % (EDITOR_INDEX, _editor_build_id())

    def _clm_docx_editor_version_token(self):
        """Identifies the file the editor opened: active version + its content.

        The checksum catches in-place saves of the same version made elsewhere
        (another tab, or the Document Template tab's own editor).
        """
        self.ensure_one()
        active_version = self._active_base_docx_version()
        attachment = self.sudo().base_document_id.attachment_id
        return '%s:%s' % (active_version.id or 0, attachment.checksum or '')

    def get_docx_editor_info(self):
        """Everything the editor client action needs before loading the file."""
        self.ensure_one()
        self.check_access('read')
        block = self._clm_docx_editor_edit_block()
        active_version = self._active_base_docx_version()
        # Once the user may read the template, its linked Documents file is
        # read as superuser: Documents access rules are independent of
        # template access and may restrict a file the user is otherwise
        # allowed to open here (same precedent as _clm_docx_editor_bytes).
        document = self.base_document_id.sudo()
        return {
            'template_id': self.id,
            'version_label': active_version.name if active_version else self.name,
            'version_token': self._clm_docx_editor_version_token(),
            # Save on this version will create a new one instead of overwriting it.
            'active_is_immutable': bool(
                active_version and (active_version.is_original or active_version.is_used)
            ),
            'template_name': self.name,
            'filename': document.name or '%s.docx' % (self.name or 'Document Template'),
            'can_edit': not block,
            'readonly_reason': block,
            'editor_url': self._clm_docx_editor_url(),
            # Same tag/clause catalog as the Document Template tab's own
            # editor. The plugin cannot insert tags itself, so the host shows
            # them in a side panel to copy from.
            'tags': self._clm_placeholder_catalog(),
            'clause_set_compact_lines': self._clm_compact_clause_insert_lines(),
        }

    def _clm_docx_editor_bytes(self):
        """The Word file currently active for this template's Document Template."""
        self.ensure_one()
        self.check_access('read')
        # As for Preview and Download: once the user may read the template,
        # the Documents record and file behind it are read as superuser,
        # since they may be restricted independently of template access.
        raw, _attachment = self.sudo()._get_base_docx_bytes()
        return raw

    # -------------------------------------------------------------------------
    # Validation
    # -------------------------------------------------------------------------

    def _clm_check_docx_bytes(self, data):
        if not data:
            raise UserError(self.env._('The editor sent an empty document. Nothing was saved.'))
        if len(data) > MAX_DOCX_SIZE:
            raise UserError(self.env._('The document exceeds the maximum size of 50 MB.'))
        invalid = self.env._('The editor did not send a valid Word document. Nothing was saved.')
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                parts = archive.infolist()
                names = {part.filename for part in parts}
                if not {'[Content_Types].xml', 'word/document.xml'} <= names:
                    raise UserError(invalid)
                if sum(part.file_size for part in parts) > MAX_DOCX_UNCOMPRESSED:
                    raise UserError(self.env._('The document is too large once uncompressed.'))
                # Word refuses a file with any malformed part, so never store one.
                for part in parts:
                    if part.filename.endswith(('.xml', '.rels')):
                        try:
                            etree.fromstring(archive.read(part), parser=_XML_PARSER)
                        except etree.XMLSyntaxError as error:
                            raise UserError(self.env._(
                                'The edited document is damaged (%(part)s: %(error)s). Nothing was saved.',
                                part=part.filename, error=error,
                            )) from error
        except zipfile.BadZipFile as error:
            raise UserError(invalid) from error

    @staticmethod
    def _clm_docx_validation_texts(data):
        """Paragraph texts of the body (tables included), headers and footers.

        Body first, so clause loops read in document order. Text boxes are
        stored twice (drawing + VML fallback); the fallback copy is skipped
        so a ``{% for %}`` inside one is not counted twice.
        """
        texts = []
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = sorted(
                (name for name in archive.namelist() if _is_text_part(name)),
                key=lambda name: (name != 'word/document.xml', name),
            )
            for name in names:
                root = etree.fromstring(archive.read(name), parser=_XML_PARSER)
                for paragraph in root.iter(_W + 'p'):
                    if any(parent.tag == _MC_FALLBACK for parent in paragraph.iterancestors()):
                        continue
                    texts.append(''.join(node.text or '' for node in paragraph.iter(_W + 't')))
        return texts

    def clm_validate_docx_editor_bytes(self, data):
        """Placeholder errors and warnings for an edited file; stores nothing."""
        self.ensure_one()
        self.check_access('read')
        self._clm_check_docx_bytes(data)
        errors, warnings, _issue_spans = self._clm_validate_docx_texts(
            self._clm_docx_validation_texts(data),
        )
        return {'errors': errors, 'warnings': warnings}

    # -------------------------------------------------------------------------
    # Saving
    # -------------------------------------------------------------------------

    def _clm_save_from_docx_editor(self, data, mode='inplace', version_token=None,
                                   change_summary=None):
        """Store edited DOCX bytes and return ``(active_version, created_new, warnings)``.

        ``mode='inplace'`` overwrites the active Template Version, unless it is
        the original or used by a contract, in which case a new one is
        created. ``mode='new'`` always creates a new Template Version.
        ``version_token`` identifies the file the editor opened; if another
        save changed the active version or its content since, nothing is
        stored.
        """
        self.ensure_one()
        if mode not in SAVE_MODES:
            raise UserError(self.env._('Unknown save mode: %s', mode))
        block = self._clm_docx_editor_edit_block()
        if block:
            raise UserError(block)
        active_before = self._active_base_docx_version()
        if version_token and version_token != self._clm_docx_editor_version_token():
            raise DocxEditorConflict(self.env._(
                'Someone saved a newer Template Version (%s) while you were editing. '
                'Download your copy, then reload the latest version.',
                active_before.name or self.name,
            ))
        result = self.clm_validate_docx_editor_bytes(data)
        if result['errors']:
            raise UserError('\n'.join(result['errors']))
        note = (change_summary or '').strip() or self.env._('Edited in the Word editor')
        # _clm_docx_editor_edit_block() already authorized this user as the
        # real (non-sudo) user above; persisting itself reads/creates
        # Documents records, whose access rules are independent of template
        # access (same Documents precedent as the read side, above).
        self.sudo()._persist_base_docx_bytes(data, mode=mode, note=note)
        active = self._active_base_docx_version()
        return active, active != active_before, result['warnings']
