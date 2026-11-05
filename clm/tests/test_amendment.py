# Part of NextContract. See LICENSE file for full copyright and licensing details.
"""
Signed-contract protection and amendments.

Once a version is signed, it, the draft it was signed from and the contract's
working copy are locked. A signed contract changes only through an amendment,
which produces new versions and leaves the signed one exactly as it was.
"""

import base64
import datetime

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, new_test_user

from odoo.addons.clm.services import FinalContractBuilder

from .test_final_contract import _CONTRACT_TEXT, _SIGNED_PDF

_TERMINATION = (
    '<p>Either party may terminate this agreement by providing '
    '{{notice_period_days}} days written notice.</p>'
)


class TestAmendment(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids |= cls.env.ref('clm.group_clm_legal_admin')
        cls.contract_type = cls.env.ref('clm.contract_type_services')
        cls.partner = cls.env['res.partner'].create({
            'name': 'Acme Corp', 'email': 'legal@acme.example.com',
        })
        cls.legal = new_test_user(
            cls.env, login='clm_amendment_legal',
            groups='base.group_user,clm.group_clm_legal,clm.group_clm_user',
        )
        cls.requester = new_test_user(
            cls.env, login='clm_amendment_requester',
            groups='base.group_user,clm.group_clm_user',
        )

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _contract(self, notice=20):
        contract = self.env['clm.contract'].create({
            'name': 'IT Services Agreement - Acme Corp',
            'contract_type_id': self.contract_type.id,
            'partner_id': self.partner.id,
            'notice_period_days': notice,
        })
        self.env['clm.contract.clause'].create({
            'contract_id': contract.id,
            'section': 'Termination',
            'title': 'Termination',
            'content': _TERMINATION,
        })
        return contract

    def _signed_document(self):
        attachment = self.env['ir.attachment'].create({
            'name': 'signed_contract.pdf',
            'type': 'binary',
            'datas': base64.b64encode(_SIGNED_PDF),
            'mimetype': 'application/pdf',
        })
        return self.env['documents.document'].create({
            'name': 'signed_contract.pdf',
            'attachment_id': attachment.id,
        })

    def _sign(self, contract, version):
        contract.with_context(migration_mode=True).write({'state': 'pending_signature'})
        return contract._process_contract_execution(
            source_version_id=version.id,
            execution_method='manual',
            execution_date=datetime.date(2026, 9, 10),
            executed_document_id=self._signed_document().id,
            signatory='Jordan Blake',
        )

    def _signed_contract(self):
        contract = self._contract()
        draft = contract._clm_create_content_version(
            version_type='signature_draft', change_summary='Agreed wording',
        )
        return contract, draft, self._sign(contract, draft)

    @staticmethod
    def _text(contract, version):
        content = contract._build_final_contract_content(version=version, kind='signed')
        return ' '.join(FinalContractBuilder.content_lines(content))

    # -------------------------------------------------------------------------
    # The signed version
    # -------------------------------------------------------------------------

    def test_executed_version_carries_the_signed_content(self):
        contract, draft, executed = self._signed_contract()
        self.assertEqual(executed.content_snapshot, draft.content_snapshot)
        self.assertEqual(executed.parent_version_id, draft)
        self.assertTrue(executed.is_signed_version)
        self.assertTrue(draft.is_signed_version, 'The draft that was signed is locked too')
        self.assertEqual(contract.final_version_id, executed)
        self.assertIn('providing 20 days written notice', self._text(contract, executed))

    def test_signed_version_cannot_be_edited(self):
        contract, draft, executed = self._signed_contract()
        self.assertTrue(contract.content_locked)

        with self.assertRaises(UserError):
            executed.with_user(self.legal).write({'change_summary': 'Tampered'})
        with self.assertRaises(UserError):
            executed.sudo().write({'content_snapshot': {'title': 'Tampered'}})
        with self.assertRaises(UserError):
            draft.with_user(self.legal).write({'document_id': self._signed_document().id})
        with self.assertRaises(UserError):
            contract.clause_ids.with_user(self.legal).write({'content': '<p>60 days</p>'})
        with self.assertRaises(UserError):
            contract.with_user(self.legal).action_open_new_version_wizard()
        with self.assertRaises(UserError):
            contract._clm_create_content_version(change_summary='Silent change')

        # Editing the contract record after signing cannot alter what v2 says.
        contract.with_context(migration_mode=True).write({'notice_period_days': 90})
        self.assertIn('providing 20 days written notice', self._text(contract, executed))

    def test_signed_contract_cannot_be_reopened_without_an_amendment(self):
        contract, _draft, _executed = self._signed_contract()
        with self.assertRaises(UserError):
            contract.write({'state': 'draft'})
        with self.assertRaises(UserError):
            contract.with_user(self.requester).with_context(clm_amendment=True).write({'state': 'draft'})
        with self.assertRaises(AccessError):
            contract.with_user(self.requester).action_create_amendment()

    def test_only_signed_contracts_can_be_amended(self):
        contract = self._contract()
        with self.assertRaises(UserError):
            contract.action_create_amendment()

    # -------------------------------------------------------------------------
    # Amendment
    # -------------------------------------------------------------------------

    def test_amendment_creates_new_versions_and_keeps_the_signed_one(self):
        contract, _draft, executed = self._signed_contract()
        contract.write({'state': 'active'})
        signed_snapshot = dict(executed.content_snapshot)

        action = contract.with_user(self.legal).action_create_amendment()
        self.assertEqual(action['res_model'], 'clm.contract')
        self.assertEqual(contract.state, 'draft')
        self.assertFalse(contract.content_locked)
        self.assertEqual(contract.clause_ids.mapped('title'), ['Termination'])
        self.assertIn('{{notice_period_days}}', contract.clause_ids.content,
                      'The amendment keeps the placeholders of the signed wording')
        self.assertTrue(self.env['clm.audit.event'].search([
            ('contract_id', '=', contract.id), ('event_type', '=', 'amendment'),
        ]))

        contract.notice_period_days = 30
        v3 = contract._clm_create_content_version(change_summary='Notice period 30 days')
        self.assertEqual(v3.parent_version_id, executed)
        self.assertEqual(v3.content_snapshot['variables']['notice_period_days'], '30')
        self.assertEqual(executed.content_snapshot, signed_snapshot, 'The signed version is untouched')
        self.assertEqual(contract.final_version_id, executed,
                         'Until the amendment is signed, the signed version stays final')

    def test_amended_contract_is_signed_as_a_new_version(self):
        contract, _draft, executed = self._signed_contract()
        contract.action_create_amendment()
        contract.notice_period_days = 30
        v3 = contract._clm_create_content_version(
            version_type='signature_draft', change_summary='Notice period 30 days',
        )
        v4 = self._sign(contract, v3)

        self.assertEqual(contract.final_version_id, v4)
        self.assertEqual(v4.source_version_id, v3)
        self.assertIn('providing 30 days written notice', self._text(contract, v4))
        self.assertIn('providing 20 days written notice', self._text(contract, executed))

        v4_pdf = contract._get_final_contract_attachment('pdf')
        self.assertIn('_V4_FINAL.pdf', v4_pdf.name)

    def test_amending_a_signed_version_from_before_snapshots_freezes_it_first(self):
        contract = self.env['clm.contract'].create({
            'name': 'Legacy Services Agreement',
            'contract_type_id': self.contract_type.id,
            'partner_id': self.partner.id,
        })
        draft = self.env['clm.contract.version'].create({
            'contract_id': contract.id, 'version_type': 'signature_draft', 'status': 'active',
        })
        draft.sudo().write({'extracted_text': _CONTRACT_TEXT, 'text_layer_status': 'available'})
        executed = self._sign(contract, draft)
        self.assertFalse(executed.content_snapshot)

        contract.action_create_amendment()
        self.assertTrue(executed.content_snapshot)
        self.assertEqual(executed.content_snapshot['captured_from'], 'signed_version')
        self.assertIn('1. Definitions', contract.clause_ids.mapped('title'))
        with self.assertRaises(UserError):
            executed.sudo().write({'content_snapshot': {}})

    # -------------------------------------------------------------------------
    # Signing a version with frozen content
    # -------------------------------------------------------------------------

    def test_version_without_a_file_is_signed_as_its_generated_pdf(self):
        contract = self._contract()
        version = contract._clm_create_content_version(
            version_type='signature_draft', change_summary='For signature',
        )
        version.action_activate()
        contract.with_context(migration_mode=True).write({'state': 'approved'})

        contract.action_send_for_signature()
        self.assertEqual(contract.state, 'pending_signature')
        self.assertEqual(contract.sign_request_ids.clm_source_version_id, version)
        self.assertTrue(version.generated_pdf_id)
        self.assertTrue(base64.b64decode(version.generated_pdf_id.datas).startswith(b'%PDF'))
        self.assertIn('_V1_FINAL', version.generated_pdf_id.name)
        with self.assertRaises(UserError, msg='The document out for signature is locked'):
            version.with_user(self.legal).write({'document_id': self._signed_document().id})
