# Part of NextContract. See LICENSE file for full copyright and licensing details.
"""
Word editor: opening a template's Document Template file in the bundled
docunext editor and saving edits onto the active Template Version or as a new one.

The original (and any used) Document Template File must stay untouched —
an in-place save on one of them creates a new Template Version instead.
"""

import base64
import io
import json
import re
import zipfile

from odoo import http
from odoo.exceptions import UserError
from odoo.tests.common import HttpCase, new_test_user, tagged
from odoo.tools.misc import file_open

from odoo.addons.clm.models.clm_template_docx_editor import DocxEditorConflict

EDIT_MARK = 'Clause added in the Word editor'


def _minimal_docx_bytes(*paragraphs, header=None, cell=None):
    from docx import Document
    document = Document()
    if not paragraphs:
        paragraphs = ('',)
    first, *rest = paragraphs
    if document.paragraphs:
        document.paragraphs[0].text = first
    else:
        document.add_paragraph(first)
    for text in rest:
        document.add_paragraph(text)
    if header is not None:
        document.sections[0].header.paragraphs[0].text = header
    if cell is not None:
        document.add_table(rows=1, cols=1).cell(0, 0).text = cell
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


@tagged('post_install', '-at_install')
class TestDocxEditor(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids |= cls.env.ref('clm.group_clm_legal_admin')
        cls.contract_type = cls.env.ref('clm.contract_type_services')
        cls.template = cls.env['clm.template'].create({
            'name': 'IT Services Agreement',
            'contract_type_id': cls.contract_type.id,
        })
        cls.docx_bytes = _minimal_docx_bytes(
            'IT SERVICES AGREEMENT', 'Providing {{notice_period_days}} days written notice.',
        )
        cls.template.write({
            'base_docx_upload': base64.b64encode(cls.docx_bytes),
            'base_docx_upload_filename': 'IT Services Agreement.docx',
        })
        cls.template.action_approve()
        cls.template.action_publish()

        cls.legal = new_test_user(
            cls.env, login='clm_docx_editor_legal', password='clm_docx_editor_legal',
            groups='base.group_user,clm.group_clm_legal_admin,clm.group_clm_user',
        )

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    @staticmethod
    def _edited(docx, text=EDIT_MARK):
        """The DOCX with one paragraph added, as the editor would export it."""
        source = zipfile.ZipFile(io.BytesIO(docx))
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as target:
            for item in source.infolist():
                data = source.read(item.filename)
                if item.filename == 'word/document.xml':
                    data = data.decode('utf-8').replace(
                        '<w:body>',
                        '<w:body><w:p><w:r><w:t>%s</w:t></w:r></w:p>' % text, 1,
                    ).encode('utf-8')
                target.writestr(item, data)
        return output.getvalue()

    @staticmethod
    def _docx_xml(data):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            return archive.read('word/document.xml').decode('utf-8')

    def _current_edit(self, text=EDIT_MARK):
        return self._edited(self.template._clm_docx_editor_bytes(), text)

    def _save(self, data, mode='inplace', **kwargs):
        return self.template.with_user(self.legal)._clm_save_from_docx_editor(
            data, mode=mode, **kwargs,
        )

    # -------------------------------------------------------------------------
    # Opening
    # -------------------------------------------------------------------------

    def test_editor_opens_the_active_document_template(self):
        data = self.template._clm_docx_editor_bytes()
        text = ' '.join(re.sub(r'<[^>]+>', ' ', self._docx_xml(data)).split())
        self.assertIn('Providing', text)

        info = self.template.with_user(self.legal).get_docx_editor_info()
        self.assertTrue(info['can_edit'])
        self.assertTrue(info['filename'].endswith('.docx'))
        self.assertTrue(info['editor_url'].startswith('/clm/static/editor/index.html?v='))
        self.assertEqual(info['version_token'], self.template._clm_docx_editor_version_token())
        self.assertTrue(info['active_is_immutable'], 'the only version is the original')

        keys = {tag['key'] for tag in info['tags']}
        self.assertIn('contract_name', keys)
        self.assertIsInstance(info['clause_set_compact_lines'], list)

    def test_without_a_document_template_the_editor_is_read_only(self):
        empty = self.env['clm.template'].create({
            'name': 'Empty Template',
            'contract_type_id': self.contract_type.id,
        })
        info = empty.with_user(self.legal).get_docx_editor_info()
        self.assertFalse(info['can_edit'])
        self.assertIn('Document Template', info['readonly_reason'])
        with self.assertRaises(UserError):
            empty.with_user(self.legal)._clm_save_from_docx_editor(self.docx_bytes)

    def test_published_template_is_read_only_for_non_admins(self):
        member = new_test_user(
            self.env, login='clm_docx_editor_member', password='clm_docx_editor_member',
            groups='base.group_user,clm.group_clm_user',
        )
        info = self.template.with_user(member).get_docx_editor_info()
        self.assertFalse(info['can_edit'])
        with self.assertRaises(UserError):
            self.template.with_user(member)._clm_save_from_docx_editor(self._current_edit())

    # -------------------------------------------------------------------------
    # Saving
    # -------------------------------------------------------------------------

    def test_save_on_the_original_creates_a_new_version_and_keeps_the_original(self):
        original_document = self.template.base_document_id

        version, created_new, _warnings = self._save(self._current_edit(), change_summary='Added a clause')

        self.assertTrue(created_new)
        self.assertFalse(version.is_original)
        self.assertEqual(version.source, 'editor')
        self.assertEqual(self.template.base_document_id, version.document_id)
        self.assertIn(EDIT_MARK, self._docx_xml(self.template._clm_docx_editor_bytes()))

        original_raw = base64.b64decode(original_document.attachment_id.with_context(bin_size=False).datas)
        self.assertNotIn(EDIT_MARK, self._docx_xml(original_raw))

    def test_save_overwrites_an_editable_active_version(self):
        v2, _created, _warnings = self._save(self._current_edit(), mode='new')
        self.assertEqual(len(self.template.base_docx_version_ids), 2)

        version, created_new, _warnings = self._save(
            self._current_edit('Second edit'),
            version_token=self.template._clm_docx_editor_version_token(),
        )

        self.assertFalse(created_new)
        self.assertEqual(version, v2)
        self.assertEqual(len(self.template.base_docx_version_ids), 2)
        self.assertIn('Second edit', self._docx_xml(self.template._clm_docx_editor_bytes()))

    def test_save_as_new_version_always_adds_one(self):
        v2, _created, _warnings = self._save(self._current_edit(), mode='new')
        v3, created_new, _warnings = self._save(
            self._current_edit('Another'), mode='new',
            version_token=self.template._clm_docx_editor_version_token(),
        )
        self.assertTrue(created_new)
        self.assertNotEqual(v2, v3)
        self.assertEqual(v3.source, 'editor')
        self.assertEqual(len(self.template.base_docx_version_ids), 3)

    def test_stale_save_is_refused_and_stores_nothing(self):
        opened = self.template._clm_docx_editor_version_token()
        self._save(self._current_edit(), mode='new', version_token=opened)
        with self.assertRaises(DocxEditorConflict):
            self._save(self._current_edit('Stale edit'), version_token=opened)
        self.assertEqual(len(self.template.base_docx_version_ids), 2)
        self.assertNotIn('Stale edit', self._docx_xml(self.template._clm_docx_editor_bytes()))

    def test_in_place_save_elsewhere_makes_the_open_copy_stale(self):
        self._save(self._current_edit(), mode='new')
        opened = self.template._clm_docx_editor_version_token()
        # Same active version, new content: e.g. saved from another tab.
        self._save(self._current_edit('Saved elsewhere'), version_token=opened)
        self.assertEqual(len(self.template.base_docx_version_ids), 2)
        with self.assertRaises(DocxEditorConflict):
            self._save(self._current_edit('Stale edit'), version_token=opened)
        self.assertNotIn('Stale edit', self._docx_xml(self.template._clm_docx_editor_bytes()))

    def test_save_rejects_anything_but_a_word_document(self):
        for data in (b'', b'not a zip', self._zip_without_document()):
            with self.assertRaises(UserError):
                self.template._clm_save_from_docx_editor(data)
        self.assertEqual(len(self.template.base_docx_version_ids), 1)

    def test_save_rejects_an_unknown_placeholder(self):
        with self.assertRaises(UserError):
            self._save(self._current_edit('{{ not_a_real_tag }}'))
        self.assertEqual(len(self.template.base_docx_version_ids), 1)

    def test_unknown_placeholders_in_tables_and_headers_are_caught(self):
        for docx in (
            _minimal_docx_bytes('Body', cell='{{ not_a_real_tag }}'),
            _minimal_docx_bytes('Body', header='{{ not_a_real_tag }}'),
        ):
            result = self.template.clm_validate_docx_editor_bytes(docx)
            self.assertTrue(result['errors'])
            with self.assertRaises(UserError):
                self._save(docx)
        self.assertEqual(len(self.template.base_docx_version_ids), 1)

    def test_save_accepts_a_known_placeholder(self):
        version, _created, _warnings = self._save(self._current_edit('{{ partner_name }}'))
        self.assertEqual(len(self.template.base_docx_version_ids), 2)
        self.assertIn('{{ partner_name }}', self._docx_xml(self.template._clm_docx_editor_bytes()))
        self.assertEqual(self.template.base_document_id, version.document_id)

    @staticmethod
    def _zip_without_document():
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as archive:
            archive.writestr('[Content_Types].xml', '<Types/>')
        return output.getvalue()

    # -------------------------------------------------------------------------
    # HTTP routes and the served editor
    # -------------------------------------------------------------------------

    def test_routes_load_validate_and_save_as_the_user(self):
        self.authenticate('clm_docx_editor_legal', 'clm_docx_editor_legal')
        base = '/clm_docx_editor/template/%s' % self.template.id
        csrf = http.Request.csrf_token(self)

        response = self.url_open(base + '/document')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content[:2], b'PK')
        current = response.content

        response = self.url_open(
            base + '/validate', data={'csrf_token': csrf},
            files={'document': ('template.docx', self._edited(current, '{{ bogus_tag }}'))},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(json.loads(response.text)['errors'])
        self.assertEqual(len(self.template.base_docx_version_ids), 1)

        opened = self.template._clm_docx_editor_version_token()
        response = self.url_open(
            base + '/save',
            data={'csrf_token': csrf, 'mode': 'inplace', 'version_token': opened,
                  'change_summary': 'Edited online'},
            files={'document': ('template.docx', self._edited(current))},
        )
        self.assertEqual(response.status_code, 200, response.text)
        result = json.loads(response.text)
        self.assertEqual(result['template_id'], self.template.id)
        self.assertEqual(result['version_count'], 2)
        self.assertTrue(result['created_new_version'], 'the original is never overwritten')

        # The editor still holds the original: saving it again conflicts.
        response = self.url_open(
            base + '/save',
            data={'csrf_token': csrf, 'mode': 'inplace', 'version_token': opened},
            files={'document': ('template.docx', self._edited(current, 'Stale'))},
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(json.loads(response.text)['code'], 'conflict')

    def test_bundled_editor_is_served_and_can_be_framed(self):
        response = self.url_open('/clm/static/editor/index.html')
        self.assertEqual(response.status_code, 200, 'static/editor/ has no editor build')
        self.assertNotEqual(response.headers.get('X-Frame-Options', '').upper(), 'DENY')

    def test_bundled_editor_ships_its_third_party_notices(self):
        with file_open('clm/static/editor/THIRD_PARTY_NOTICES.txt') as notices:
            text = notices.read()
        for component in ('@casualoffice/docs', '@schnsrw/core', 'react ', 'SIL Open Font License'):
            self.assertIn(component, text)

    def test_editor_opens_without_its_own_title_bar(self):
        """The host bar replaces the editor's title bar, File tab and quick tools."""
        with file_open('clm/static/src/docx_editor/docx_editor.js') as host:
            self.assertIn('url.searchParams.set("chrome", "compact")', host.read())
        with file_open('clm/static/editor/index.html') as index:
            entry = re.search(r'<script[^>]+src="\./([^"]+)"', index.read()).group(1)
        with file_open('clm/static/editor/' + entry) as script:
            self.assertIn('is-compact', script.read())

    def test_bundled_editor_speaks_the_host_protocol(self):
        """A rebuilt editor on another protocol would silently ignore every host message."""
        with file_open('clm/static/src/docx_editor/docx_editor.js') as host:
            protocol = re.search(r'SUPPORTED_PROTOCOL = "([^"]+)"', host.read()).group(1)
        with file_open('clm/static/editor/index.html') as index:
            entry = re.search(r'<script[^>]+src="\./([^"]+)"', index.read()).group(1)
        with file_open('clm/static/editor/' + entry) as script:
            self.assertIn(protocol, script.read())
