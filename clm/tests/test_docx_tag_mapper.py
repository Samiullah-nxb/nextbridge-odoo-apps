# Part of NextContract. See LICENSE file for full copyright and licensing details.

import base64
import io

from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError

from odoo.addons.clm.services import docx_tag_editor as editor


def _minimal_docx_bytes(*paragraphs):
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
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


class TestDocxTagEditorService(TransactionCase):

    def test_extract_and_apply_round_trip(self):
        raw = _minimal_docx_bytes('Hello party.', 'Second line.')
        rows = editor.extract_paragraphs(raw)
        self.assertGreaterEqual(len(rows), 2)
        self.assertIn('html', rows[0])
        self.assertIn('style_css', rows[0])
        texts = {row['index']: row['text'] for row in rows}
        target = min(i for i, t in texts.items() if t.strip())
        texts[target] = editor.append_tag_to_text(texts[target], '{{ partner_name }}')
        updated = editor.apply_paragraph_texts(raw, texts)
        again = editor.extract_paragraphs(updated)
        joined = '\n'.join(r['text'] for r in again)
        self.assertIn('{{ partner_name }}', joined)

    def test_extract_preserves_bold_in_html(self):
        from docx import Document
        from docx.shared import Pt, RGBColor
        document = Document()
        paragraph = document.add_paragraph()
        run = paragraph.add_run('SERVICE CONTRACT')
        run.bold = True
        run.font.size = Pt(18)
        run.font.color.rgb = RGBColor(0x1A, 0x56, 0xDB)
        paragraph.alignment = 1  # center
        buf = io.BytesIO()
        document.save(buf)
        rows = editor.extract_paragraphs(buf.getvalue())
        self.assertTrue(rows)
        html = rows[0]['html']
        self.assertIn('SERVICE CONTRACT', html)
        self.assertIn('font-weight:700', html)
        self.assertIn('18.0pt', html)
        self.assertIn('text-align:center', rows[0]['style_css'])

    def test_near_white_run_color_omitted_from_html(self):
        """White / near-white DOCX colours vanish on paper — do not emit them."""
        from docx import Document
        from docx.shared import RGBColor
        document = Document()
        paragraph = document.add_paragraph()
        run = paragraph.add_run('Heading text')
        run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        buf = io.BytesIO()
        document.save(buf)
        rows = editor.extract_paragraphs(buf.getvalue())
        self.assertTrue(rows)
        html = rows[0]['html']
        self.assertIn('Heading text', html)
        self.assertNotIn('color:#FFFFFF', html.upper().replace(' ', ''))
        self.assertNotIn('color:#FFF', html.upper().replace(' ', ''))
        self.assertIsNone(editor._css_color_hex('#ffffff'))
        self.assertIsNone(editor._css_color_hex('rgb(250, 250, 250)'))
        self.assertEqual(editor._css_color_hex('#1a56db'), '#1a56db')

    def test_extract_includes_inline_image(self):
        from docx import Document
        from docx.shared import Inches
        from PIL import Image as PILImage

        img_buf = io.BytesIO()
        PILImage.new('RGB', (40, 20), color=(20, 80, 200)).save(img_buf, format='PNG')
        img_buf.seek(0)

        document = Document()
        paragraph = document.add_paragraph()
        paragraph.add_run().add_picture(img_buf, width=Inches(1.5))
        paragraph.add_run(' Company Ltd')
        buf = io.BytesIO()
        document.save(buf)

        preview = editor.extract_document_preview(buf.getvalue())
        self.assertTrue(preview['paragraphs'])
        html = preview['paragraphs'][0]['html']
        self.assertIn('o_clm_docx_img', html)
        self.assertIn('data:image/png;base64,', html)
        self.assertIn('Company Ltd', html)

    def test_replace_body_reembeds_data_uri_logo(self):
        """Save rebuilds the body from HTML — data-URI logos must be re-embedded."""
        from docx import Document
        from PIL import Image as PILImage

        img_buf = io.BytesIO()
        PILImage.new('RGB', (32, 16), color=(200, 40, 40)).save(img_buf, format='PNG')
        raw_png = img_buf.getvalue()
        data_uri = 'data:image/png;base64,' + base64.b64encode(raw_png).decode('ascii')

        source = Document()
        source.add_paragraph('LEGAL CONSULTING AGREEMENT')
        src_buf = io.BytesIO()
        source.save(src_buf)

        html = (
            '<p><img class="o_clm_docx_img" src="%s" '
            'style="max-width:144px;"/></p>'
            '<p><strong>LEGAL CONSULTING AGREEMENT</strong></p>'
            '<p>{{ contract_name }}</p>'
        ) % data_uri
        payloads = editor.payloads_from_editor_html(html)
        updated = editor.replace_body_from_payloads(src_buf.getvalue(), payloads)
        again = editor.extract_document_preview(updated)
        joined_html = '\n'.join(p['html'] for p in again['paragraphs'])
        self.assertIn('o_clm_docx_img', joined_html)
        self.assertIn('data:image/png;base64,', joined_html)
        self.assertIn('LEGAL CONSULTING AGREEMENT', joined_html)
        self.assertIn('{{ contract_name }}', joined_html)

        # Round-trip: image bytes survive embed → extract
        self.assertIn(base64.b64encode(raw_png).decode('ascii')[:32], joined_html)

    def test_replace_body_keeps_logo_when_html_drops_img(self):
        """Editor often strips <img> / leaves blob: URLs — body drawings must survive."""
        from docx import Document
        from docx.shared import Inches
        from PIL import Image as PILImage

        img_buf = io.BytesIO()
        PILImage.new('RGB', (48, 24), color=(10, 120, 60)).save(img_buf, format='PNG')
        img_buf.seek(0)

        document = Document()
        logo_para = document.add_paragraph()
        logo_para.add_run().add_picture(img_buf, width=Inches(1.2))
        document.add_paragraph('{{ contract_name }}')
        document.add_paragraph('Agreement body text.')
        buf = io.BytesIO()
        document.save(buf)
        raw = buf.getvalue()

        before = editor.extract_document_preview(raw)
        self.assertTrue(before['paragraphs'][0]['has_image'])

        # Simulate Wysiwyg save that lost the logo <img> (blank first paragraph).
        html = (
            '<p><br/></p>'
            '<p>{{ contract_name }}</p>'
            '<p>Agreement body text.</p>'
        )
        payloads = editor.payloads_from_editor_html(html)
        updated = editor.replace_body_from_payloads(raw, payloads)
        after = editor.extract_document_preview(updated)
        self.assertTrue(
            after['paragraphs'][0]['has_image'],
            'Logo must survive save when HTML drops the <img> node',
        )
        self.assertIn('data:image', after['paragraphs'][0]['html'])
        self.assertIn('{{ contract_name }}', after['paragraphs'][1]['text'])

    def test_replace_body_keeps_logo_with_blob_src_and_original_attr(self):
        """blob: src + data-clm-original-src must re-embed on save."""
        from docx import Document
        from docx.shared import Inches
        from PIL import Image as PILImage

        img_buf = io.BytesIO()
        PILImage.new('RGB', (20, 10), color=(90, 20, 180)).save(img_buf, format='PNG')
        raw_png = img_buf.getvalue()
        data_uri = 'data:image/png;base64,' + base64.b64encode(raw_png).decode('ascii')

        document = Document()
        document.add_paragraph('Title')
        buf = io.BytesIO()
        document.save(buf)

        html = (
            '<p><img class="o_clm_docx_img" src="blob:http://localhost/fake-id" '
            'data-clm-original-src="%s" style="max-width:120px;"/></p>'
            '<p>Title</p>'
        ) % data_uri
        payloads = editor.payloads_from_editor_html(html)
        updated = editor.replace_body_from_payloads(buf.getvalue(), payloads)
        again = editor.extract_document_preview(updated)
        joined = '\n'.join(p['html'] for p in again['paragraphs'])
        self.assertIn('data:image/png;base64,', joined)
        self.assertIn(base64.b64encode(raw_png).decode('ascii')[:24], joined)

    def test_replace_body_preserves_header_logo(self):
        """Body rebuild must not wipe logos stored in the Word header."""
        from docx import Document
        from docx.shared import Inches
        from PIL import Image as PILImage

        img_buf = io.BytesIO()
        PILImage.new('RGB', (30, 15), color=(0, 0, 0)).save(img_buf, format='PNG')
        img_buf.seek(0)

        document = Document()
        header = document.sections[0].header
        header.paragraphs[0].add_run().add_picture(img_buf, width=Inches(1.0))
        document.add_paragraph('{{ contract_name }}')
        buf = io.BytesIO()
        document.save(buf)
        raw = buf.getvalue()

        before = editor.extract_document_preview(raw)
        self.assertTrue(before['headers'])
        self.assertIn('data:image', before['headers'][0]['html'])

        payloads = editor.payloads_from_editor_html(
            '<p>{{ contract_name }}</p><p>New body line</p>'
        )
        updated = editor.replace_body_from_payloads(raw, payloads)
        after = editor.extract_document_preview(updated)
        self.assertTrue(after['headers'])
        self.assertIn('data:image', after['headers'][0]['html'])

    def test_html_formatting_round_trip(self):
        from docx import Document
        document = Document()
        document.add_paragraph('old text')
        buf = io.BytesIO()
        document.save(buf)
        updated = editor.apply_paragraph_payloads(buf.getvalue(), {
            0: {
                'text': 'Hello world',
                'html': '<span style="font-weight:700;font-size:18pt">Hello</span> world',
                'style_css': 'text-align:center',
            },
        })
        rows = editor.extract_paragraphs(updated)
        self.assertEqual(rows[0]['text'], 'Hello world')
        self.assertIn('font-weight:700', rows[0]['html'])
        self.assertIn('18.0pt', rows[0]['html'])
        self.assertIn('text-align:center', rows[0]['style_css'])

    def test_validate_unknown_placeholder(self):
        errors, _warnings, _spans = editor.validate_paragraphs(
            ['Hello {{ unknown_field }}'],
            require_clause_loop=False,
        )
        self.assertTrue(any('unknown_field' in e for e in errors))

    def test_validate_known_placeholder(self):
        errors, warnings, spans = editor.validate_paragraphs(
            ['Counterparty: {{ partner_name }}'],
            require_clause_loop=False,
        )
        self.assertFalse(errors)
        self.assertFalse(warnings)
        self.assertFalse(spans)

    def test_validate_malformed_braces_that_balance_by_count(self):
        """Broken tags can cancel out in a raw {{ / }} count — still invalid."""
        errors, _warnings, spans = editor.validate_paragraphs(
            [
                'Template: {{ template_name }} (v{{ template_version }})',
                '{ partner_name }}',
                '{{ contract_name }',
                'partner_address }}',
                '{{ partner_address',
            ],
            require_clause_loop=False,
        )
        self.assertTrue(errors, "malformed braces must fail validation")
        kinds = {s.get('kind') for s in spans}
        self.assertTrue(
            kinds & {'single_open', 'single_close', 'orphan_close', 'incomplete_open'},
            kinds,
        )
        # Intact tags in the first line must not be flagged unknown here.
        self.assertFalse(any('template_name' in (e or '') and 'Unknown' in (e or '') for e in errors))

    def test_validate_single_close_brace(self):
        errors, _warnings, spans = editor.validate_paragraphs(
            ['{{ partner_name }'],
            require_clause_loop=False,
        )
        self.assertTrue(any('Malformed' in e or 'Incomplete' in e for e in errors))
        self.assertTrue(any(s.get('kind') == 'single_close' for s in spans))

    def test_ensure_clause_loop(self):
        texts = ['Title page']
        with_loop = editor.ensure_clause_loop(texts)
        self.assertIn('{% for section in sections %}', '\n'.join(with_loop))
        again = editor.ensure_clause_loop(with_loop)
        self.assertEqual(
            '\n'.join(with_loop).count('{% for section in sections %}'),
            1,
        )
        self.assertEqual(len(again), len(with_loop))


class TestDocxPreviewEditor(TransactionCase):

    def setUp(self):
        super().setUp()
        self.contract_type = self.env.ref('clm.contract_type_nda')
        raw = _minimal_docx_bytes(
            'This Agreement is between the Company and the Counterparty.',
            'Notice period applies.',
        )
        attachment = self.env['ir.attachment'].create({
            'name': 'base_nda.docx',
            'datas': base64.b64encode(raw),
            'mimetype': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        })
        document = self.env['documents.document'].create({
            'name': 'base_nda.docx',
            'attachment_id': attachment.id,
        })
        self.template = self.env['clm.template'].create({
            'name': 'NDA with Base DOCX',
            'contract_type_id': self.contract_type.id,
            'base_document_id': document.id,
            'state': 'draft',
        })

    def test_has_base_document_flag(self):
        self.assertTrue(self.template.has_base_document)
        bare = self.env['clm.template'].create({
            'name': 'HTML only',
            'contract_type_id': self.contract_type.id,
        })
        self.assertFalse(bare.has_base_document)

    def test_direct_docx_upload(self):
        raw = _minimal_docx_bytes('Direct upload body.')
        tmpl = self.env['clm.template'].create({
            'name': 'Upload Template',
            'contract_type_id': self.contract_type.id,
            'base_docx_upload': base64.b64encode(raw),
            'base_docx_upload_filename': 'My Upload.docx',
            'state': 'draft',
        })
        self.assertTrue(tmpl.base_document_id)
        # Original upload keeps the uploaded filename (no — v1 suffix).
        self.assertEqual(tmpl.base_document_id.name, 'My Upload.docx')
        self.assertTrue(tmpl.base_docx_version_ids.filtered('is_original'))
        self.assertFalse(tmpl.base_docx_upload)

        # Second upload becomes a new version (original untouched). Both the
        # version row and its Documents file take the Document Template
        # identity (``{Name} — vN``), so Preview / Download match the list.
        original = tmpl.base_document_original_id
        raw2 = _minimal_docx_bytes('Second upload body.')
        tmpl.write({
            'base_docx_upload': base64.b64encode(raw2),
            'base_docx_upload_filename': 'Another.docx',
        })
        self.assertEqual(len(tmpl.base_docx_version_ids), 2)
        self.assertEqual(tmpl.base_document_original_id, original)
        self.assertNotEqual(tmpl.base_document_id, original)
        self.assertEqual(tmpl.base_document_id.name, 'Upload Template — v1.docx')
        latest = tmpl._active_base_docx_version()
        self.assertEqual(latest.name, 'Upload Template — v1')
        again = editor.bytes_from_b64(
            tmpl.base_document_id.attachment_id.with_context(bin_size=False).datas
        )
        texts = '\n'.join(r['text'] for r in editor.extract_paragraphs(again))
        self.assertIn('Second upload body', texts)

        # Original version label is the Document Template name, not Contract Type.
        orig_row = tmpl.base_docx_version_ids.filtered('is_original')[:1]
        self.assertEqual(orig_row.name, 'Upload Template')
        self.assertNotEqual(orig_row.name, tmpl.contract_type_id.name)

    def test_get_docx_editor_data(self):
        data = self.template.get_docx_editor_data()
        self.assertTrue(data['paragraphs'])
        self.assertTrue(any(t['key'] == 'partner_name' for t in data['tags']))
        self.assertTrue(any(t['key'] == 'partner_address' for t in data['tags']))
        self.assertTrue(any(t['key'] == 'today' for t in data['tags']))
        self.assertTrue(any(t['key'] == 'company_signatory' for t in data['tags']))
        self.assertTrue(any(t['key'] == 'partner_signatory' for t in data['tags']))
        self.assertTrue(any(t['key'] == 'signature_block' for t in data['tags']))
        self.assertTrue(any(t['key'] == 'section.title' for t in data['tags']))
        self.assertTrue(any(t['key'] == 'clause_loop' for t in data['tags']))
        self.assertGreaterEqual(len(data['tags']), 55)
        self.assertTrue(any('Counterparty' in (p['text'] or '') for p in data['paragraphs']))

    def test_save_docx_paragraphs_round_trip(self):
        data = self.template.get_docx_editor_data()
        original_doc = self.template.base_document_id
        original_datas = original_doc.attachment_id.datas
        paragraphs = data['paragraphs']
        target = next(p for p in paragraphs if 'Counterparty' in (p['text'] or ''))
        target['text'] = editor.append_tag_to_text(target['text'], '{{ partner_name }}')
        saved = self.template.save_docx_paragraphs(paragraphs)
        joined = '\n'.join(p['text'] for p in saved['paragraphs'])
        self.assertIn('{{ partner_name }}', joined)

        # Original Documents file must stay unchanged.
        self.assertEqual(original_doc.attachment_id.datas, original_datas)
        self.assertTrue(self.template.base_docx_version_ids.filtered('is_original'))
        self.assertGreaterEqual(len(self.template.base_docx_version_ids), 2)
        self.assertNotEqual(self.template.base_document_id, original_doc)

        raw = editor.bytes_from_b64(
            self.template.base_document_id.attachment_id.datas
        )
        again = '\n'.join(r['text'] for r in editor.extract_paragraphs(raw))
        self.assertIn('{{ partner_name }}', again)

        # Original preview still has no tag.
        orig_raw = editor.bytes_from_b64(original_datas)
        orig_text = '\n'.join(r['text'] for r in editor.extract_paragraphs(orig_raw))
        self.assertNotIn('{{ partner_name }}', orig_text)

        # In-place save updates the active (non-original) file only.
        active_doc = self.template.base_document_id
        active_before = active_doc.attachment_id.with_context(bin_size=False).datas
        version_count = len(self.template.base_docx_version_ids)
        data2 = self.template.get_docx_editor_data()
        self.assertTrue(data2.get('can_save_inplace'))
        paragraphs2 = data2['paragraphs']
        target2 = next(p for p in paragraphs2 if '{{ partner_name }}' in (p['text'] or ''))
        target2['text'] = editor.append_tag_to_text(target2['text'], '{{ company_name }}')
        saved_inplace = self.template.save_docx_paragraphs(paragraphs2, mode='inplace')
        self.assertEqual(len(self.template.base_docx_version_ids), version_count)
        self.assertEqual(self.template.base_document_id, active_doc)
        self.assertNotEqual(
            active_doc.attachment_id.with_context(bin_size=False).datas,
            active_before,
        )
        joined2 = '\n'.join(p['text'] for p in saved_inplace['paragraphs'])
        self.assertIn('{{ company_name }}', joined2)

        # Original cannot be overwritten in place — Save as New Version instead.
        orig_row = self.template.base_docx_version_ids.filtered('is_original')[:1]
        orig_row.action_set_active()
        data3 = self.template.get_docx_editor_data()
        self.assertFalse(data3.get('can_save_inplace'))
        count_before = len(self.template.base_docx_version_ids)
        self.template.save_docx_paragraphs(paragraphs2, mode='inplace')
        self.assertGreater(len(self.template.base_docx_version_ids), count_before)
        self.assertNotEqual(self.template.base_document_id, orig_row.document_id)
        # Original file bytes remain untouched.
        self.assertEqual(original_doc.attachment_id.datas, original_datas)

    def test_save_rejects_unknown(self):
        with self.assertRaises(UserError):
            self.template.save_docx_paragraphs([
                {'index': 0, 'text': 'Bad {{ totally_unknown_xyz }}'},
            ])

    def test_save_document_html_keeps_body_logo_on_new_version(self):
        """Save / Save as New Version must keep logos for every template."""
        from docx import Document
        from docx.shared import Inches
        from PIL import Image as PILImage

        img_buf = io.BytesIO()
        PILImage.new('RGB', (40, 20), color=(30, 90, 200)).save(img_buf, format='PNG')
        img_buf.seek(0)
        document = Document()
        logo = document.add_paragraph()
        logo.add_run().add_picture(img_buf, width=Inches(1.0))
        document.add_paragraph('This Agreement is between the Company and the Counterparty.')
        document.add_paragraph('{% for section in sections %}')
        document.add_paragraph('{{ section.title }}')
        document.add_paragraph('{% for clause in section.clauses %}')
        document.add_paragraph('{{ clause.title }}')
        document.add_paragraph('{{ clause.content }}')
        document.add_paragraph('{% endfor %}')
        document.add_paragraph('{% endfor %}')
        buf = io.BytesIO()
        document.save(buf)

        template = self.env['clm.template'].create({
            'name': 'Logo Persist Template',
            'contract_type_id': self.contract_type.id,
            'base_docx_upload': base64.b64encode(buf.getvalue()),
            'base_docx_upload_filename': 'logo_persist.docx',
            'state': 'draft',
        })
        data = template.get_docx_editor_data()
        self.assertTrue(data['paragraphs'][0].get('has_image'))

        # HTML without <img> (editor drop) — server must restore from original.
        html = (
            '<p><br/></p>'
            '<p>This Agreement is between the Company and the Counterparty.</p>'
            '<p>{% for section in sections %}</p>'
            '<p>{{ section.title }}</p>'
            '<p>{% for clause in section.clauses %}</p>'
            '<p>{{ clause.title }}</p>'
            '<p>{{ clause.content }}</p>'
            '<p>{% endfor %}</p>'
            '<p>{% endfor %}</p>'
        )
        saved = template.save_docx_document_html(html, mode='new')
        self.assertTrue(
            saved['paragraphs'][0].get('has_image'),
            'Logo must remain after Save as New Version',
        )
        raw = editor.bytes_from_b64(
            template.base_document_id.attachment_id.with_context(bin_size=False).datas
        )
        preview = editor.extract_document_preview(raw)
        self.assertTrue(preview['paragraphs'][0]['has_image'])
