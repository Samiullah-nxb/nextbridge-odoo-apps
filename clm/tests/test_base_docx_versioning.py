# Part of NextContract. See LICENSE file for full copyright and licensing details.
"""Base DOCX Template Version pinning, immutability, and soft-delete rules."""

import base64
import io

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase

from odoo.addons.clm.services import docx_tag_editor as editor
from odoo.addons.clm.services.docx_renderer import DocxRenderer


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


class TestBaseDocxVersionLifecycle(TransactionCase):

    def setUp(self):
        super().setUp()
        self.contract_type = self.env.ref('clm.contract_type_nda')
        self.clause = self.env['clm.clause'].create({
            'name': 'Confidentiality',
            'category': 'confidentiality',
            'standard_wording': '<p>Confidential for {{partner_name}}.</p>',
            'risk_level': 'medium',
        })
        self.clause.action_submit_review()
        self.clause.action_approve()
        self.clause.action_publish()

        raw_v1 = _minimal_docx_bytes('BASE DOCX VERSION ONE', 'Body {{partner_name}}.')
        self.template = self.env['clm.template'].create({
            'name': 'NDA Base DOCX Template',
            'contract_type_id': self.contract_type.id,
            'base_docx_upload': base64.b64encode(raw_v1),
            'base_docx_upload_filename': 'nda_v_original.docx',
            'state': 'draft',
        })
        self.env['clm.template.clause.line'].create({
            'template_id': self.template.id,
            'clause_id': self.clause.id,
            'sequence': 10,
            'section': 'Confidentiality',
            'required': True,
        })
        # Create a non-original active version so inplace / used rules are testable.
        raw_v2 = _minimal_docx_bytes('BASE DOCX VERSION TWO', 'Active {{partner_name}}.')
        self.template.write({
            'base_docx_upload': base64.b64encode(raw_v2),
            'base_docx_upload_filename': 'nda_v2.docx',
        })
        self.template.action_approve()
        self.template.action_publish()

        self.partner = self.env['res.partner'].create({'name': 'Acme Corp'})
        self.original = self.template.base_docx_version_ids.filtered('is_original')[:1]
        self.v2 = self.template._active_base_docx_version()
        self.assertTrue(self.original)
        self.assertTrue(self.v2)
        self.assertNotEqual(self.original, self.v2)
        self.assertTrue(self.v2.is_active)

    def _new_contract(self, name='Test NDA'):
        return self.env['clm.contract'].create({
            'name': name,
            'contract_type_id': self.contract_type.id,
            'partner_id': self.partner.id,
            'template_id': self.template.id,
        })

    def test_new_contract_uses_active_version(self):
        contract = self._new_contract('Contract on v2')
        self.assertEqual(contract.base_docx_version_id, self.v2)
        self.assertEqual(contract._clm_document_generation_format(), 'base_docx')
        request = contract._generate_draft(renderer='plain_text')
        self.assertEqual(request.base_docx_version_id, self.v2)
        self.assertEqual(request.result_version_id.base_docx_version_id, self.v2)
        snap = request.result_version_id.content_snapshot or {}
        self.assertEqual(
            (snap.get('template') or {}).get('base_docx_version_id'),
            self.v2.id,
        )
        self.assertTrue(self.v2.is_used)

    def test_lock_captured_at_create_before_first_generate(self):
        """Active may change between create and first generate — lock stays."""
        contract = self._new_contract('Locked at create')
        self.assertEqual(contract.base_docx_version_id, self.v2)

        raw_v3 = _minimal_docx_bytes('BASE DOCX VERSION THREE')
        self.template.write({
            'base_docx_upload': base64.b64encode(raw_v3),
            'base_docx_upload_filename': 'nda_v3.docx',
        })
        v3 = self.template._active_base_docx_version()
        self.assertNotEqual(v3, self.v2)
        self.assertEqual(contract.base_docx_version_id, self.v2)

        request = contract._generate_draft(renderer='plain_text')
        self.assertEqual(request.base_docx_version_id, self.v2)
        self.assertEqual(request.result_version_id.base_docx_version_id, self.v2)

    def test_existing_contract_keeps_old_version_after_activation(self):
        contract = self._new_contract('Pinned on v2')
        request = contract._generate_draft(renderer='plain_text')
        pinned = request.base_docx_version_id
        self.assertEqual(pinned, self.v2)
        self.assertEqual(contract.base_docx_version_id, self.v2)

        # Create and activate a newer Template Version.
        raw_v3 = _minimal_docx_bytes('BASE DOCX VERSION THREE')
        self.template.write({
            'base_docx_upload': base64.b64encode(raw_v3),
            'base_docx_upload_filename': 'nda_v3.docx',
        })
        v3 = self.template._active_base_docx_version()
        self.assertNotEqual(v3, pinned)
        self.assertTrue(v3.is_active)
        # Contract lock must not move with the template Active switch.
        self.assertEqual(contract.base_docx_version_id, pinned)

        # Existing contract still resolves / regenerates against the pin.
        self.assertEqual(
            contract._clm_base_docx_version_for_render(
                version=request.result_version_id
            ),
            pinned,
        )
        again = contract._generate_draft(renderer='plain_text')
        self.assertEqual(again.base_docx_version_id, pinned)
        self.assertEqual(again.result_version_id.base_docx_version_id, pinned)

        # Brand-new contract picks up the newly Active version.
        fresh = self._new_contract('Contract on v3')
        self.assertEqual(fresh.base_docx_version_id, v3)
        fresh_req = fresh._generate_draft(renderer='plain_text')
        self.assertEqual(fresh_req.base_docx_version_id, v3)

    def test_contract_lock_cannot_be_rewritten(self):
        contract = self._new_contract('Immutable lock')
        self.assertEqual(contract.base_docx_version_id, self.v2)
        with self.assertRaises(UserError):
            contract.write({'base_docx_version_id': self.original.id})
        self.assertEqual(contract.base_docx_version_id, self.v2)

    def test_used_version_cannot_be_modified_or_deleted(self):
        contract = self._new_contract('Locks v2')
        contract._generate_draft(renderer='plain_text')
        self.assertTrue(self.v2.is_used)

        with self.assertRaises(UserError):
            self.v2._check_docx_mutable()

        with self.assertRaises(UserError):
            self.v2.unlink()

        with self.assertRaises(UserError):
            self.v2.write({'document_id': self.original.document_id.id})

        # Editor inplace on a used active version must create a NEW version.
        version_count = len(self.template.base_docx_version_ids)
        data = self.template.get_docx_editor_data()
        self.assertFalse(data.get('can_save_inplace'))
        paragraphs = data['paragraphs']
        if paragraphs:
            paragraphs[0]['text'] = (paragraphs[0].get('text') or '') + ' X'
        saved = self.template.save_docx_paragraphs(paragraphs, mode='inplace')
        self.assertGreater(len(self.template.base_docx_version_ids), version_count)
        self.assertNotEqual(self.template._active_base_docx_version(), self.v2)
        self.assertTrue(self.v2.document_id.exists())

    def test_unused_version_can_be_deleted(self):
        # Create unused v3, activate it, then soft-delete previous unused
        # non-original that was never used.
        self.assertFalse(self.v2.is_used)
        raw_v3 = _minimal_docx_bytes('UNUSED THEN ACTIVE')
        self.template.write({
            'base_docx_upload': base64.b64encode(raw_v3),
            'base_docx_upload_filename': 'nda_unused.docx',
        })
        v3 = self.template._active_base_docx_version()
        self.assertTrue(v3.is_active)
        self.assertFalse(self.v2.is_active)
        self.assertFalse(self.v2.is_used)
        v2_id = self.v2.id
        self.v2.action_delete()
        deleted = self.env['clm.template.base.docx.version'].browse(v2_id)
        self.assertTrue(deleted.exists())
        self.assertFalse(deleted.active)
        # Soft-deleted rows are hidden from the default version list.
        self.assertNotIn(
            v2_id,
            self.template.base_docx_version_ids.ids,
        )

    def test_active_version_cannot_be_deleted(self):
        with self.assertRaises(UserError):
            self.v2.action_delete()
        with self.assertRaises(UserError):
            self.v2.unlink()

    def test_updating_used_version_creates_new_version(self):
        contract = self._new_contract('Force new on save')
        contract._generate_draft(renderer='plain_text')
        used = self.template._active_base_docx_version()
        self.assertTrue(used.is_used)
        used_doc = used.document_id
        used_datas = used_doc.attachment_id.with_context(bin_size=False).datas

        before = len(self.template.base_docx_version_ids)
        raw = _minimal_docx_bytes('NEW VERSION AFTER USE')
        # Simulate editor save-as-new (and inplace auto-upgrade).
        result = self.template._persist_base_docx_bytes(raw, mode='inplace')
        after_active = self.template._active_base_docx_version()
        self.assertGreater(len(self.template.base_docx_version_ids), before)
        self.assertNotEqual(after_active, used)
        self.assertEqual(
            used_doc.attachment_id.with_context(bin_size=False).datas,
            used_datas,
        )
        self.assertTrue(result.get('version_number'))

        # Render helpers must still read the pinned (used) bytes for old contract.
        pinned_bytes = DocxRenderer._base_docx_bytes(
            self.template, base_docx_version=used,
        )
        self.assertTrue(pinned_bytes)
        texts = '\n'.join(
            r['text'] for r in editor.extract_paragraphs(pinned_bytes)
        )
        self.assertIn('VERSION TWO', texts)
        self.assertNotIn('NEW VERSION AFTER USE', texts)

    def test_used_version_cannot_be_soft_deleted(self):
        contract = self._new_contract('No soft delete when used')
        contract._generate_draft(renderer='plain_text')
        used = self.v2
        self.assertTrue(used.is_used)

        # Activate a newer version so used is no longer active.
        raw_v3 = _minimal_docx_bytes('ACTIVE AFTER USE')
        self.template.write({
            'base_docx_upload': base64.b64encode(raw_v3),
            'base_docx_upload_filename': 'nda_v3_soft.docx',
        })
        self.assertFalse(used.is_active)

        with self.assertRaises(UserError):
            used.action_delete()
        self.assertTrue(used.active)

        with self.assertRaises(UserError):
            used.unlink()
        self.assertTrue(used.exists())
        self.assertTrue(used.active)

    def test_three_contracts_lock_independent_active_versions(self):
        """Mandatory matrix: A→V1/V2, B→V2→stay, C→V3 after Active changes."""
        # At setUp Active = v2 (original is v1-ish). Capture Contract A on v2.
        contract_a = self._new_contract('Contract A')
        self.assertEqual(contract_a.base_docx_version_id, self.v2)
        contract_a._generate_draft(renderer='plain_text')

        raw_v3 = _minimal_docx_bytes('VERSION THREE FOR B')
        self.template.write({
            'base_docx_upload': base64.b64encode(raw_v3),
            'base_docx_upload_filename': 'nda_v3_matrix.docx',
        })
        v3 = self.template._active_base_docx_version()
        self.assertNotEqual(v3, self.v2)

        contract_b = self._new_contract('Contract B')
        self.assertEqual(contract_b.base_docx_version_id, v3)

        raw_v4 = _minimal_docx_bytes('VERSION FOUR FOR C')
        self.template.write({
            'base_docx_upload': base64.b64encode(raw_v4),
            'base_docx_upload_filename': 'nda_v4_matrix.docx',
        })
        v4 = self.template._active_base_docx_version()
        self.assertNotEqual(v4, v3)

        # A still on v2, B still on v3 after Active moved to v4.
        again_a = contract_a._generate_draft(renderer='plain_text')
        self.assertEqual(again_a.base_docx_version_id, self.v2)
        self.assertEqual(contract_a.base_docx_version_id, self.v2)

        again_b = contract_b._generate_draft(renderer='plain_text')
        self.assertEqual(again_b.base_docx_version_id, v3)
        self.assertEqual(contract_b.base_docx_version_id, v3)

        contract_c = self._new_contract('Contract C')
        self.assertEqual(contract_c.base_docx_version_id, v4)
        req_c = contract_c._generate_draft(renderer='plain_text')
        self.assertEqual(req_c.base_docx_version_id, v4)
