# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError, AccessError
import base64
import datetime
import io


def _valid_pdf_b64():
    """A real one-page PDF: Odoo Sign refuses a bare '%PDF-1.0' stub."""
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    pdf = canvas.Canvas(buf)
    pdf.drawString(72, 720, 'CLM test contract')
    pdf.save()
    return base64.b64encode(buf.getvalue())


class TestExecution(TransactionCase):

    def setUp(self):
        super().setUp()
        self.contract_type_nda = self.env.ref('clm.contract_type_nda')
        contract = self.env['clm.contract'].with_context(migration_mode=True).create({
            'name': 'Test Execution Contract',
            'contract_type_id': self.contract_type_nda.id,
            'partner_id': self.env['res.partner'].create({
                'name': 'Execution Counterparty', 'email': 'counterparty@example.com',
            }).id,
            'state': 'approved'
        })
        self.contract = contract.with_context(migration_mode=False)

        self.attachment = self.env['ir.attachment'].create({
            'name': 'test_contract.pdf',
            'type': 'binary',
            'datas': _valid_pdf_b64(),
            'res_model': 'clm.contract.version',
            'res_id': 0,
        })
        self.document = self.env['documents.document'].create({
            'name': 'test_contract.pdf',
            'attachment_id': self.attachment.id,
        })
        self.draft_version = self.env['clm.contract.version'].create({
            'contract_id': self.contract.id,
            'version_type': 'signature_draft',
            'version_number': 1,
            'document_id': self.document.id,
            'status': 'active'
        })
        
        self.risk_factor = self.env['clm.risk.factor'].create({
            'name': 'High Value Contract',
            'source_type': 'manual',
            'weight': 60
        })

    def _complete_all_signers(self, sign_request):
        """Mark every signer complete so CLM will accept full execution."""
        for item in sign_request.request_item_ids:
            # Bypass Sign fill workflow; we only need completed state for the gate.
            self.env.cr.execute(
                "UPDATE sign_request_item SET state = 'completed' WHERE id = %s",
                (item.id,),
            )
        sign_request.invalidate_recordset()
        sign_request.request_item_ids.invalidate_recordset()

    def test_manual_execution(self):
        self.contract.action_send_for_signature()
        # Cancel open Sign request (as the manual wizard does) before executing.
        self.contract.sign_request_ids.with_context(
            clm_skip_signature_cancellation=True
        ).write({'state': 'canceled'})
        executed_version = self.contract._process_contract_execution(
            source_version_id=self.draft_version.id,
            execution_method='manual', executed_document_id=self.document.id,
            execution_date=datetime.date.today(),
            notes='Test Manual Execution'
        )
        
        self.assertEqual(self.contract.state, 'executed')
        self.assertEqual(self.contract.signature_status, 'signed')
        self.assertEqual(executed_version.version_type, 'executed')
        self.assertEqual(executed_version.execution_method, 'manual')
        self.assertEqual(executed_version.source_version_id, self.draft_version)

    def test_cannot_execute_while_sign_request_incomplete(self):
        self.contract.action_send_for_signature()
        with self.assertRaises(UserError):
            self.contract._process_contract_execution(
                source_version_id=self.draft_version.id,
                execution_method='manual',
                executed_document_id=self.document.id,
                execution_date=datetime.date.today(),
                notes='Should block',
            )
        self.assertEqual(self.contract.state, 'pending_signature')

    def test_cannot_activate_until_fully_signed(self):
        self.contract.with_context(migration_mode=True).write({'state': 'executed'})
        self.contract.signature_status = 'pending'
        with self.assertRaises(UserError):
            self.contract._clm_assert_can_start()

    def test_risk_assessment_recalculate(self):
        assessment = self.env['clm.risk.assessment'].create({
            'contract_id': self.contract.id
        })
        self.env['clm.risk.assessment.item'].create({
            'assessment_id': assessment.id,
            'factor_id': self.risk_factor.id
        })
        
        assessment.action_recalculate()
        
        self.assertEqual(assessment.calculated_score, 60)
        self.assertEqual(assessment.calculated_rating, 'high')
        self.assertEqual(assessment.final_rating, 'high')
        
        # Test override
        assessment.write({'override_rating': 'medium', 'override_reason': 'Approved by CFO'})
        # recompute triggered
        self.assertEqual(assessment.final_rating, 'medium')
        # Calculated rating should be preserved
        self.assertEqual(assessment.calculated_rating, 'high')

    def test_obligation_completion(self):
        obligation = self.env['clm.obligation'].create({
            'contract_id': self.contract.id,
            'name': 'Deliver Initial Report',
            'due_date': datetime.date.today()
        })
        self.assertEqual(obligation.status, 'pending')
        
        obligation.action_mark_completed()
        self.assertEqual(obligation.status, 'completed')
        self.assertTrue(obligation.completed_date)

    def test_execution_payload(self):
        # Trigger sending for signature
        self.contract.action_send_for_signature()
        
        # Verify a sign request was created and linked to the contract
        sign_request = self.env['sign.request'].search([('clm_contract_id', '=', self.contract.id)])
        self.assertEqual(len(sign_request), 1)
        self.assertEqual(self.contract.state, 'pending_signature')

    def test_source_integrity(self):
        self.contract.action_send_for_signature()
        sign_request = self.env['sign.request'].search([('clm_contract_id', '=', self.contract.id)])
        
        # Assert the sign request accurately locks to the signature_draft version
        self.assertEqual(sign_request.clm_source_version_id, self.draft_version)

    def test_execution_lifecycle(self):
        self.contract.action_send_for_signature()
        sign_request = self.env['sign.request'].search([('clm_contract_id', '=', self.contract.id)])
        self._complete_all_signers(sign_request)

        # Trigger signature completion callback (write 'state': 'done' or 'signed')
        sign_request.write({'state': 'signed'})
        
        # Check an immutable executed version is created
        executed_version = self.env['clm.contract.version'].search([
            ('contract_id', '=', self.contract.id),
            ('version_type', '=', 'executed')
        ])
        
        self.assertEqual(len(executed_version), 1)
        self.assertEqual(executed_version.source_version_id, self.draft_version)
        self.assertEqual(executed_version.execution_method, 'odoo_sign')
        self.assertEqual(self.contract.state, 'executed')

    def test_duplicate_callback_protection(self):
        self.contract.action_send_for_signature()
        sign_request = self.env['sign.request'].search([('clm_contract_id', '=', self.contract.id)])
        self._complete_all_signers(sign_request)

        # Trigger completion once
        sign_request.write({'state': 'signed'})
        
        # Trigger completion again
        sign_request.write({'state': 'signed'})
        
        # Assert only one executed version exists
        executed_versions = self.env['clm.contract.version'].search([
            ('contract_id', '=', self.contract.id),
            ('version_type', '=', 'executed')
        ])
        self.assertEqual(len(executed_versions), 1)

    def test_partial_signature_does_not_execute(self):
        """One party signing must not move the contract to Executed."""
        self.contract.action_send_for_signature()
        sign_request = self.env['sign.request'].search([('clm_contract_id', '=', self.contract.id)])
        items = sign_request.request_item_ids
        self.assertTrue(len(items) >= 1)
        # Only complete the first signer.
        self.env.cr.execute(
            "UPDATE sign_request_item SET state = 'completed' WHERE id = %s",
            (items[0].id,),
        )
        items.invalidate_recordset()
        # Even if something tries to mark the request signed early, CLM must refuse.
        sign_request.write({'state': 'signed'})
        self.assertEqual(self.contract.state, 'pending_signature')
        self.assertFalse(self.contract._clm_signatures_fully_complete())

    def test_source_hijack_defense(self):
        self.contract.action_send_for_signature()
        sign_request = self.env['sign.request'].search([('clm_contract_id', '=', self.contract.id)])
        # Clear the open request so the dual-party gate does not mask the hijack check.
        sign_request.with_context(clm_skip_signature_cancellation=True).write(
            {'state': 'canceled'}
        )

        # Create a hijacked version from a different contract
        hijacked_contract = self.env['clm.contract'].create({
            'name': 'Hijacked Contract',
            'contract_type_id': self.contract_type_nda.id,
            'state': 'approved'
        })
        hijacked_version = self.env['clm.contract.version'].create({
            'contract_id': hijacked_contract.id,
            'version_type': 'signature_draft',
            'version_number': 1
        })
        
        # Simulate completion callback but with a hijacked source version
        with self.assertRaises(UserError):
            self.contract._process_contract_execution(
                source_version_id=hijacked_version.id,
                execution_method='odoo_sign',
                execution_date=datetime.date.today(),
            )

    def test_signature_cancellation(self):
        """Test that canceling a sign request reverts contract to approved."""
        self.contract.action_send_for_signature()
        sign_request = self.env['sign.request'].search([
            ('clm_contract_id', '=', self.contract.id)
        ])
        self.assertEqual(self.contract.state, 'pending_signature')
        self.assertEqual(self.contract.signature_status, 'pending')

        # Simulate signer refusing/canceling
        sign_request.write({'state': 'canceled'})

        # Contract should revert to approved
        self.assertEqual(self.contract.state, 'approved')
        self.assertEqual(self.contract.signature_status, 'cancelled')

    def test_resend_after_cancellation(self):
        """Test that a contract can be re-sent for signature after cancellation."""
        # First attempt
        self.contract.action_send_for_signature()
        sign_request_1 = self.env['sign.request'].search([
            ('clm_contract_id', '=', self.contract.id)
        ])
        self.assertEqual(len(sign_request_1), 1)

        # Cancel
        sign_request_1.write({'state': 'canceled'})
        self.assertEqual(self.contract.state, 'approved')

        # Re-send
        self.contract.action_send_for_signature()
        sign_requests = self.env['sign.request'].search([
            ('clm_contract_id', '=', self.contract.id)
        ])
        self.assertEqual(len(sign_requests), 2)
        self.assertEqual(self.contract.state, 'pending_signature')
        self.assertEqual(self.contract.signature_status, 'pending')

        # Complete the second request
        new_request = sign_requests.filtered(lambda r: r.state != 'canceled')
        self._complete_all_signers(new_request)
        new_request.write({'state': 'signed'})
        self.assertEqual(self.contract.state, 'executed')

    def test_cancellation_after_execution_ignored(self):
        """Test that a late cancellation callback on an already-executed contract is silently ignored."""
        self.contract.action_send_for_signature()
        sign_request = self.env['sign.request'].search([
            ('clm_contract_id', '=', self.contract.id)
        ])

        # Complete the signature first
        self._complete_all_signers(sign_request)
        sign_request.write({'state': 'signed'})
        self.assertEqual(self.contract.state, 'executed')

        # Now a stale cancellation callback arrives (should be ignored)
        sign_request.write({'state': 'canceled'})

        # Contract must remain executed
        self.assertEqual(self.contract.state, 'executed')
        self.assertEqual(self.contract.signature_status, 'signed')

    def test_unlink_permissions(self):
        """Verify that standard NextContract user cannot unlink an obligation (raises AccessError), but manager can."""
        standard_user = self.env['res.users'].create({
            'name': 'Standard NextContract User',
            'login': 'std_clm_user_unlink',
            'email': 'std_clm_user_unlink@example.com',
            'group_ids': [
                (4, self.env.ref('base.group_user').id),
                (4, self.env.ref('clm.group_clm_user').id),
            ],
        })
        obligation = self.env['clm.obligation'].create({
            'contract_id': self.contract.id,
            'name': 'Deliver Initial Report',
            'due_date': datetime.date.today(),
        })
        with self.assertRaises(AccessError):
            obligation.with_user(standard_user).unlink()

        # Only a Legal Admin may delete (Approver / Management has no delete
        # right, see docs/CLM_ROLES_AND_PERMISSIONS.md).
        manager_user = self.env['res.users'].create({
            'name': 'NextContract Legal Admin',
            'login': 'clm_admin_unlink',
            'email': 'clm_admin_unlink@example.com',
            'group_ids': [
                (4, self.env.ref('base.group_user').id),
                (4, self.env.ref('clm.group_clm_legal_admin').id),
            ],
        })
        obligation.with_user(manager_user).unlink()
        self.assertFalse(obligation.exists())

    def test_record_rules_execution_objects(self):
        """Verify record rules restrict access to obligations and risk assessments for restricted contracts."""
        standard_user = self.env['res.users'].create({
            'name': 'Standard NextContract User',
            'login': 'std_clm_user_rr',
            'email': 'std_clm_user_rr@example.com',
            'group_ids': [
                (4, self.env.ref('base.group_user').id),
                (4, self.env.ref('clm.group_clm_user').id),
            ],
        })

        # Create a restricted contract owned by admin
        restricted_contract = self.env['clm.contract'].create({
            'name': 'Restricted Acquisition Contract',
            'contract_type_id': self.contract_type_nda.id,
            'confidentiality_level': 'restricted',
            'user_id': self.env.user.id,
        })

        # Create obligation, risk assessment, and item under the restricted contract
        obligation = self.env['clm.obligation'].create({
            'contract_id': restricted_contract.id,
            'name': 'Restricted Deliverable',
            'due_date': datetime.date.today(),
            'owner_id': self.env.user.id,
        })
        assessment = self.env['clm.risk.assessment'].create({
            'contract_id': restricted_contract.id,
        })
        item = self.env['clm.risk.assessment.item'].create({
            'assessment_id': assessment.id,
            'factor_id': self.risk_factor.id,
        })

        # 1. Standard user cannot search or read obligation on restricted contract
        obligations = self.env['clm.obligation'].with_user(standard_user).search([('id', '=', obligation.id)])
        self.assertNotIn(obligation, obligations)
        with self.assertRaises(AccessError):
            obligation.with_user(standard_user).read(['name'])

        # 2. Standard user cannot search or read risk assessment or item on restricted contract
        assessments = self.env['clm.risk.assessment'].with_user(standard_user).search([('id', '=', assessment.id)])
        self.assertNotIn(assessment, assessments)
        with self.assertRaises(AccessError):
            assessment.with_user(standard_user).read(['calculated_score'])

        # Risk-assessment items are not granted to Business Requesters at all
        # (no ACL), so even listing them is refused.
        with self.assertRaises(AccessError):
            self.env['clm.risk.assessment.item'].with_user(standard_user).search([('id', '=', item.id)])
        with self.assertRaises(AccessError):
            item.with_user(standard_user).read(['factor_id'])

        # 3. Legal reviewer has full access to the obligation and risk assessment
        legal_user = self.env['res.users'].create({
            'name': 'Legal Reviewer',
            'login': 'legal_user_exec_rr',
            'email': 'legal_user_exec_rr@example.com',
            'group_ids': [
                (4, self.env.ref('base.group_user').id),
                (4, self.env.ref('clm.group_clm_legal').id),
            ],
        })
        legal_obligations = self.env['clm.obligation'].with_user(legal_user).search([('id', '=', obligation.id)])
        self.assertIn(obligation, legal_obligations)
        res = obligation.with_user(legal_user).read(['name'])
        self.assertEqual(res[0]['name'], 'Restricted Deliverable')

        legal_assessments = self.env['clm.risk.assessment'].with_user(legal_user).search([('id', '=', assessment.id)])
        self.assertIn(assessment, legal_assessments)
        res_assessment = assessment.with_user(legal_user).read(['contract_id'])
        self.assertEqual(res_assessment[0]['contract_id'][0], restricted_contract.id)


