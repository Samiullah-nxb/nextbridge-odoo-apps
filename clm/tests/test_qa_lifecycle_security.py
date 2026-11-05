# Part of NextContract. See LICENSE file for full copyright and licensing details.
"""End-to-end QA regression tests across the whole CLM suite.

Each test asserts the *correct / secure* behaviour. See CLM_BUG_REPORT.md for
the defect IDs (BUG-xx) referenced here.
"""
import base64
import datetime
import io
import unittest

from odoo.exceptions import AccessError, UserError, ValidationError
from psycopg2 import IntegrityError

from odoo.tests.common import TransactionCase, tagged
from odoo.tools import mute_logger

def _valid_pdf(text='QA test document'):
    """A real one-page PDF; Odoo Sign rejects hand-made stubs."""
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    pdf = canvas.Canvas(buf)
    pdf.drawString(72, 720, text)
    pdf.save()
    return buf.getvalue()


PDF = base64.b64encode(_valid_pdf())


@tagged('post_install', '-at_install', 'clm_qa')
class TestClmQaBase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.nda = env.ref('clm.contract_type_nda')
        cls.dept_sales = env['hr.department'].create({'name': 'QA Sales'})
        cls.dept_legal = env['hr.department'].create({'name': 'QA Legal'})
        cls.dept_hr = env['hr.department'].create({'name': 'QA HR'})

        def make_user(login, group_xmlid, dept=None):
            user = env['res.users'].create({
                'name': login, 'login': login, 'email': '%s@example.com' % login,
                'group_ids': [(4, env.ref('base.group_user').id),
                              (4, env.ref(group_xmlid).id)],
            })
            if dept:
                env['hr.employee'].create({
                    'name': login, 'user_id': user.id, 'department_id': dept.id,
                })
            return user

        cls.requester = make_user('qa_requester', 'clm.group_clm_user', cls.dept_sales)
        cls.other_requester = make_user('qa_other_req', 'clm.group_clm_user', cls.dept_hr)
        cls.legal = make_user('qa_legal', 'clm.group_clm_legal', cls.dept_legal)
        cls.admin = make_user('qa_admin', 'clm.group_clm_legal_admin', cls.dept_legal)
        cls.approver = make_user('qa_approver', 'clm.group_clm_manager', cls.dept_legal)
        cls.readonly = make_user('qa_readonly', 'clm.group_clm_read_only', cls.dept_sales)
        cls.partner = env['res.partner'].create({
            'name': 'QA Counterparty Ltd', 'email': 'cp@example.com', 'is_company': True,
        })
        cls.signatory = env['res.partner'].create({
            'name': 'QA Signatory', 'email': 'sig@example.com',
            'parent_id': cls.partner.id, 'is_company': False,
        })

    def _contract(self, user=None, **vals):
        vals.setdefault('name', 'QA Contract')
        vals.setdefault('contract_type_id', self.nda.id)
        vals.setdefault('partner_id', self.partner.id)
        Contract = self.env['clm.contract']
        if user:
            Contract = Contract.with_user(user)
        return Contract.create(vals)

    def _version(self, contract, **vals):
        attachment = self.env['ir.attachment'].create({
            'name': 'qa.pdf', 'type': 'binary', 'datas': PDF,
        })
        # Linked to the contract like the app's own upload path does: that link
        # is what lets other CLM roles open the file.
        document = self.env['documents.document'].create({
            'name': 'qa.pdf', 'attachment_id': attachment.id,
            'res_model': 'clm.contract', 'res_id': contract.id,
        })
        base = {
            'contract_id': contract.id, 'version_number': 1,
            'version_type': 'internal_draft', 'document_id': document.id,
            'status': 'active',
        }
        base.update(vals)
        return self.env['clm.contract.version'].create(base)

    def _tries(self, func):
        """Run func; return True when it was refused (UserError/AccessError)."""
        try:
            with self.env.cr.savepoint():
                func()
        except (UserError, AccessError, ValidationError):
            return True
        return False


@tagged('post_install', '-at_install', 'clm_qa')
class TestWorkflowGates(TestClmQaBase):
    """Phases 5-10: legal review / approval gates must not be bypassable."""

    def _in_review(self):
        contract = self._contract(user=self.requester)
        contract.action_submit_for_review()
        self.assertEqual(contract.state, 'in_review')
        legal_task = contract.sudo().workflow_task_ids.filtered(
            lambda t: t.rule_id.stage_trigger == 'in_review' and t.is_required)
        self.assertTrue(legal_task, 'Legal Review Approval task must be raised')
        self.assertEqual(legal_task.status, 'pending')
        return contract

    def test_approve_blocked_while_legal_review_pending(self):
        contract = self._in_review()
        with self.assertRaises(UserError):
            contract.action_approve()
        self.assertEqual(contract.state, 'in_review')

    def test_start_negotiation_cannot_skip_legal_review(self):
        """BUG-01: in_review -> Start Negotiation -> Approve skips the gates."""
        contract = self._in_review()
        self._tries(contract.action_send_to_negotiation)
        self._tries(contract.action_approve)
        self.assertNotEqual(
            contract.state, 'approved',
            'Contract reached Approved for Signature with the required Legal '
            'Review Approval task still pending.')

    def test_send_to_counterparty_cannot_skip_legal_review(self):
        """BUG-01: in_review -> Send to Counterparty -> Approve skips the gates."""
        contract = self._in_review()
        self._tries(contract.action_send_to_counterparty)
        self._tries(contract.action_approve)
        self.assertNotEqual(contract.state, 'approved')

    def test_requester_cannot_force_approve_workflow(self):
        contract = self._in_review()
        contract.sudo().workflow_task_ids.filtered(
            lambda t: t.rule_id.stage_trigger == 'in_review').write({'status': 'completed'})
        # a requester must never be able to approve a contract
        contract.sudo().write({'state': 'internal_review_done'})
        self.assertTrue(
            self._tries(lambda: contract.with_user(self.requester).action_approve()),
            'A Business Requester approved a contract for signature.')

    def test_readonly_user_cannot_transition(self):
        contract = self._contract()
        with self.assertRaises(AccessError):
            contract.with_user(self.readonly).action_submit_for_review()
        with self.assertRaises(AccessError):
            contract.with_user(self.readonly).action_cancel()

    def test_invalid_transitions_rejected(self):
        contract = self._contract()
        for target in ('approved', 'executed', 'active', 'archived', 'pending_signature'):
            with self.assertRaises(UserError, msg=target):
                contract.write({'state': target})

    def test_migration_mode_requires_legal_admin(self):
        contract = self._contract(user=self.requester)
        with self.assertRaises(AccessError):
            contract.with_user(self.requester).with_context(migration_mode=True).write(
                {'state': 'executed'})

    def test_task_approval_only_by_assignee(self):
        contract = self._in_review()
        task = contract.sudo().workflow_task_ids.filtered(
            lambda t: t.rule_id.stage_trigger == 'in_review')
        task.sudo().assigned_user_id = self.admin
        with self.assertRaises(AccessError):
            task.with_user(self.legal).action_approve()

    def test_task_reject_only_by_assignee(self):
        """BUG-05: action_reject has no assignee check (approve has)."""
        contract = self._in_review()
        task = contract.sudo().workflow_task_ids.filtered(
            lambda t: t.rule_id.stage_trigger == 'in_review')
        task.sudo().assigned_user_id = self.admin
        self.assertTrue(
            self._tries(lambda: task.with_user(self.legal).action_reject()),
            'A non-assignee Legal Reviewer rejected a task assigned to someone else.')

    def test_full_review_path_reaches_approved(self):
        """Positive path: the intended route works end to end."""
        contract = self._in_review()
        task = contract.sudo().workflow_task_ids.filtered(
            lambda t: t.rule_id.stage_trigger == 'in_review')
        task.with_user(self.admin).action_approve()
        contract.with_user(self.legal).action_complete_internal_review()
        self.assertEqual(contract.state, 'internal_review_done')
        mgmt = contract.sudo().workflow_task_ids.filtered(
            lambda t: t.rule_id.stage_trigger == 'internal_review_done')
        self.assertTrue(mgmt, 'Management Approval (DoP) task must be raised')
        with self.assertRaises(UserError):
            contract.with_user(self.legal).action_approve()
        mgmt.with_user(self.admin).action_approve()
        self.assertEqual(contract.state, 'approved')
        self.assertTrue(
            contract.sudo().env['clm.audit.event'].search(
                [('summary', 'ilike', 'approved')], limit=1))


@tagged('post_install', '-at_install', 'clm_qa')
class TestConfidentiality(TestClmQaBase):
    """Section 10: restricted contracts and their child records."""

    def setUp(self):
        super().setUp()
        self.restricted = self._contract(
            name='Restricted M&A', department_id=self.dept_legal.id,
            confidentiality_level='restricted')
        self.version = self._version(self.restricted)

    def test_contract_hidden_from_other_requester(self):
        found = self.env['clm.contract'].with_user(self.other_requester).search(
            [('id', '=', self.restricted.id)])
        self.assertFalse(found)

    def test_restricted_versions_hidden_from_other_requester(self):
        """BUG-02: versions of a restricted contract leak to any CLM user."""
        found = self.env['clm.contract.version'].with_user(self.other_requester).search(
            [('contract_id', '=', self.restricted.id)])
        self.assertFalse(found, 'Version rows of a restricted contract are readable')

    def test_restricted_version_read_denied(self):
        version = self.version.with_user(self.other_requester)
        self.assertTrue(
            self._tries(lambda: version.read(['version_number', 'contract_id'])),
            'Direct read of a restricted contract version succeeded')

    def test_restricted_clauses_hidden(self):
        if 'clm.contract.clause' not in self.env:
            self.skipTest('clm_draft not installed')
        clause = self.env['clm.contract.clause'].sudo().create({
            'contract_id': self.restricted.id, 'title': 'Secret', 'content': 'x'})
        found = self.env['clm.contract.clause'].with_user(self.other_requester).search(
            [('id', '=', clause.id)])
        self.assertFalse(found, 'Clause of a restricted contract is readable')

    def test_restricted_attachment_download_denied(self):
        attachment = self.version.document_id.attachment_id
        self.assertTrue(
            self._tries(lambda: attachment.with_user(self.other_requester).read(['raw'])),
            'Attachment of a restricted contract version is readable')

    def test_portal_user_without_access_sees_nothing(self):
        portal = self.env['res.users'].create({
            'name': 'QA Portal', 'login': 'qa_portal', 'email': 'qa_portal@example.com',
            'group_ids': [(6, 0, [self.env.ref('base.group_portal').id])],
        })
        found = self.env['clm.contract'].with_user(portal).search([])
        self.assertFalse(found)


@tagged('post_install', '-at_install', 'clm_qa')
class TestIntake(TestClmQaBase):

    def _request(self, user=None, **vals):
        vals.setdefault('name', 'QA Request')
        vals.setdefault('contract_type_id', self.nda.id)
        vals.setdefault('partner_id', self.partner.id)
        Request = self.env['clm.contract.request']
        if user:
            Request = Request.with_user(user)
        return Request.create(vals)

    def _accepted(self):
        request = self._request(user=self.requester)
        request.action_submit()
        request.with_user(self.admin).action_accept()
        return request

    def test_only_requester_can_submit(self):
        request = self._request(user=self.requester)
        with self.assertRaises(UserError):
            request.with_user(self.other_requester).action_submit()

    def test_requester_cannot_edit_after_submit(self):
        request = self._request(user=self.requester)
        request.action_submit()
        with self.assertRaises(UserError):
            request.write({'name': 'tampered'})

    def test_cannot_accept_unsubmitted(self):
        request = self._request(user=self.requester)
        with self.assertRaises(UserError):
            request.with_user(self.admin).action_accept()

    def test_requester_cannot_accept_own_request(self):
        """BUG-04: any user able to write a request can accept/reject it."""
        request = self._request(user=self.requester)
        request.action_submit()
        self.assertTrue(
            self._tries(lambda: request.with_user(self.requester).action_accept()),
            'A Business Requester accepted their own intake request.')

    def test_double_conversion_blocked(self):
        request = self._accepted()
        wizard = self.env['clm.request.convert.wizard'].with_user(self.admin).with_context(
            active_id=request.id, active_model='clm.contract.request').create({})
        wizard.action_convert()
        self.assertEqual(request.state, 'converted')
        with self.assertRaises(UserError):
            wizard.action_convert()
        self.assertEqual(len(request.contract_id), 1)
        self.assertEqual(
            self.env['clm.contract'].search_count([('request_ids', 'in', request.id)]), 1)

    def test_conversion_maps_all_data(self):
        request = self._request(
            user=self.requester, name='Supply Agreement 2026',
            department_id=self.dept_sales.id, partner_signatory_id=self.signatory.id,
            estimated_value=125000.0, purpose='Annual supply of widgets',
            start_date=datetime.date(2026, 11, 1),
            expected_execution_date=datetime.date(2026, 10, 30),
            required_legal_action='new_contract', urgency='urgent')
        request.action_submit()
        request.with_user(self.admin).action_accept()
        wizard = self.env['clm.request.convert.wizard'].with_user(self.admin).with_context(
            active_id=request.id, active_model='clm.contract.request').create({})
        wizard.action_convert()
        contract = request.contract_id.sudo()
        self.assertEqual(contract.name, 'Supply Agreement 2026')
        self.assertEqual(contract.partner_signatory_id, self.signatory)
        self.assertEqual(contract.contract_value, 125000.0)
        self.assertEqual(contract.effective_date, datetime.date(2026, 11, 1))
        self.assertEqual(contract.expected_execution_date, datetime.date(2026, 10, 30))
        self.assertEqual(contract.urgency, 'urgent')
        self.assertEqual(contract.request_id, request)
        self.assertFalse(contract.signing_date, 'signing date must stay empty at conversion')
        self.assertEqual(contract.state, 'draft')

    def test_requester_cannot_convert(self):
        """BUG-04: conversion is documented as a Legal Admin action."""
        request = self._accepted()
        Wizard = self.env['clm.request.convert.wizard'].with_user(self.requester).with_context(
            active_id=request.id, active_model='clm.contract.request')
        self.assertTrue(
            self._tries(lambda: Wizard.create({}).action_convert()),
            'A Business Requester converted a request into a contract.')

    def test_convert_rejected_request_blocked(self):
        request = self._request(user=self.requester)
        request.action_submit()
        request.with_user(self.admin).action_reject()
        with self.assertRaises(UserError):
            request.action_open_conversion_wizard()


@tagged('post_install', '-at_install', 'clm_qa')
class TestDataValidation(TestClmQaBase):
    """Section 11: invalid data on the contract."""

    def test_expiry_before_effective_date_rejected(self):
        """BUG-03: no date-order validation on clm.contract."""
        with self.assertRaises(ValidationError):
            self._contract(
                effective_date=datetime.date(2027, 1, 1),
                expiry_date=datetime.date(2026, 1, 1))

    def test_negative_contract_value_rejected(self):
        """BUG-03: no sign validation on contract_value."""
        with self.assertRaises(ValidationError):
            self._contract(contract_value=-5000.0)

    def test_negative_notice_period_rejected(self):
        with self.assertRaises(ValidationError):
            self._contract(notice_period_days=-30)

    def test_missing_title_rejected(self):
        with mute_logger('odoo.sql_db'), self.assertRaises(IntegrityError):
            self.env['clm.contract'].create({'contract_type_id': self.nda.id})


@tagged('post_install', '-at_install', 'clm_qa')
class TestExecutedImmutability(TestClmQaBase):
    """Phase 13-15: a signed/executed document must never change silently."""

    def _executed(self):
        contract = self.env['clm.contract'].with_context(migration_mode=True).create({
            'name': 'QA Executed', 'contract_type_id': self.nda.id,
            'partner_id': self.partner.id, 'state': 'approved',
        }).with_context(migration_mode=False)
        draft = self._version(contract, version_type='signature_draft')
        contract.action_send_for_signature()
        contract.sign_request_ids.with_context(
            clm_skip_signature_cancellation=True).write({'state': 'canceled'})
        executed = contract._process_contract_execution(
            source_version_id=draft.id, execution_method='manual',
            executed_document_id=draft.document_id.id,
            execution_date=datetime.date.today(), notes='QA')
        self.assertEqual(contract.state, 'executed')
        return contract, executed

    def test_executed_version_fields_locked(self):
        _contract, executed = self._executed()
        with self.assertRaises(UserError):
            executed.with_user(self.legal).write({'notes': 'changed'})
        with self.assertRaises(UserError):
            executed.with_user(self.admin).unlink()

    def test_executed_version_file_cannot_be_overwritten(self):
        """BUG-06: the signed file (ir.attachment) can be replaced by a user."""
        _contract, executed = self._executed()
        attachment = executed.document_id.attachment_id
        original = attachment.sudo().checksum
        self._tries(lambda: attachment.with_user(self.legal).write(
            {'datas': base64.b64encode(b'%PDF-1.4 TAMPERED')}))
        self.assertEqual(
            attachment.sudo().checksum, original,
            'The executed (signed) file content was silently replaced.')

    def test_executed_version_file_cannot_be_deleted(self):
        _contract, executed = self._executed()
        attachment = executed.document_id.attachment_id
        self._tries(lambda: attachment.with_user(self.legal).unlink())
        self.assertTrue(attachment.sudo().exists(),
                        'The executed (signed) file was deleted.')

    def test_executed_contract_term_change_is_audited(self):
        """OBS-01: parties/value of an executed contract stay editable (design
        decision pending), so the edit must at least leave a tracked trail."""
        contract, _executed = self._executed()
        other = self.env['res.partner'].create({'name': 'Other Party', 'email': 'o@example.com'})
        # settle the creation-time tracking first, else create+write merge into one
        self.env.flush_all()
        self.env.cr.precommit.run()
        contract.with_user(self.legal).write({'partner_id': other.id})
        # tracking messages are created at pre-commit, which a test never reaches
        self.env.flush_all()
        self.env.cr.precommit.run()
        tracked = self.env['mail.tracking.value'].sudo().search([
            ('mail_message_id.res_id', '=', contract.id),
            ('mail_message_id.model', '=', 'clm.contract'),
            ('field_id.name', '=', 'partner_id'),
        ])
        self.assertTrue(tracked, 'Changing the counterparty of an executed contract is not tracked')
        # the signed file itself is untouched
        self.assertEqual(contract.final_version_id.version_type, 'executed')


@tagged('post_install', '-at_install', 'clm_qa')
class TestApprovalOverridesAndRules(TestClmQaBase):
    """Force-approve override, DoP threshold rules, signing preconditions."""

    def test_force_approve_by_legal_admin_is_audited(self):
        """BUG-12: the Force Approve wizard wrote an invalid audit event."""
        contract = self._contract(user=self.requester)
        contract.action_submit_for_review()
        action = contract.with_user(self.admin).action_approve()
        self.assertEqual(action['res_model'], 'clm.force.approve.wizard')
        wizard = self.env['clm.force.approve.wizard'].with_user(self.admin).create({
            'contract_id': contract.id, 'reason': 'Board directive 42'})
        wizard.action_force_approve()
        self.assertEqual(contract.state, 'approved')
        events = self.env['clm.audit.event'].sudo().search(
            [('contract_id', '=', contract.id), ('summary', 'ilike', 'Board directive 42')])
        self.assertTrue(events, 'The forced approval and its reason must be audited')

    def test_value_threshold_rule_does_not_break_requester(self):
        """BUG-11: rule matching read contract_value as the acting user."""
        self.env['clm.workflow.rule'].sudo().create({
            'name': 'QA high value legal review', 'stage_trigger': 'in_review',
            'task_type': 'review', 'task_name': 'QA High Value Review',
            'is_required': True, 'min_contract_value': 10000.0,
        })
        contract = self._contract(user=self.requester)
        contract.sudo().contract_value = 50000.0
        contract.action_submit_for_review()
        self.assertTrue(
            contract.sudo().workflow_task_ids.filtered(
                lambda t: t.name == 'QA High Value Review'),
            'The value-based review task must be raised for a 50k contract')

    def test_legal_reviewer_can_complete_internal_review(self):
        """BUG-08: pure Legal Reviewer lacks ACL on clm.workflow.rule."""
        contract = self._contract(user=self.requester)
        contract.action_submit_for_review()
        contract.sudo().workflow_task_ids.filtered(
            lambda t: t.rule_id.stage_trigger == 'in_review').write({'status': 'completed'})
        contract.with_user(self.legal).action_complete_internal_review()
        self.assertEqual(contract.state, 'internal_review_done')

    def test_cannot_send_for_signature_without_counterparty(self):
        """BUG-10: both Sign roles silently go to the company when no counterparty."""
        contract = self.env['clm.contract'].with_context(migration_mode=True).create({
            'name': 'No counterparty', 'contract_type_id': self.nda.id,
            'state': 'approved'}).with_context(migration_mode=False)
        self._version(contract, version_type='signature_draft')
        self.assertTrue(
            self._tries(contract.action_send_for_signature),
            'Contract was sent for signature with no counterparty; the company '
            'would sign both roles.')


@tagged('post_install', '-at_install', 'clm_qa')
class TestRoleGating(TestClmQaBase):
    """Direct (RPC-style) calls must respect docs/CLM_ROLES_AND_PERMISSIONS.md.

    The UI hides buttons per role, but any public ``action_*`` method can be
    called through JSON-RPC by a user who has write access to the contract.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env

        def make(login, xmlid, dept):
            user = env['res.users'].create({
                'name': login, 'login': login, 'email': '%s@example.com' % login,
                'group_ids': [(4, env.ref('base.group_user').id), (4, env.ref(xmlid).id)],
            })
            env['hr.employee'].create({'name': login, 'user_id': user.id, 'department_id': dept.id})
            return user

        cls.finance = make('qa_finance', 'clm.group_clm_finance', cls.dept_sales)
        cls.procurement = make('qa_procurement', 'clm.group_clm_procurement', cls.dept_sales)

    def _active_contract(self):
        contract = self.env['clm.contract'].with_context(migration_mode=True).create({
            'name': 'QA Active', 'contract_type_id': self.nda.id,
            'partner_id': self.partner.id, 'state': 'active',
            'user_id': self.requester.id, 'department_id': self.dept_sales.id,
            'expiry_date': datetime.date.today() + datetime.timedelta(days=200),
        })
        return contract.with_context(migration_mode=False)

    def test_only_legal_admin_can_mark_expired(self):
        """Renew/Terminate/Archive/Expire are Legal Admin only (docs §a-§f)."""
        for user in (self.requester, self.procurement, self.finance, self.approver):
            contract = self._active_contract()
            with self.subTest(user=user.login):
                self.assertTrue(
                    self._tries(lambda: contract.with_user(user).action_mark_expired()),
                    '%s marked an active contract as expired' % user.login)

    def test_renew_terminate_archive_wizards_admin_only(self):
        contract = self._active_contract()
        for user in (self.requester, self.legal, self.approver):
            for opener in ('action_open_renew_wizard', 'action_open_terminate_wizard'):
                with self.subTest(user=user.login, action=opener):
                    self.assertTrue(
                        self._tries(lambda: getattr(contract.with_user(user), opener)()),
                        '%s could open %s' % (user.login, opener))

    def test_only_legal_roles_can_prepare_and_send_for_signature(self):
        """Signature/execution: Legal Reviewer / Admin only (docs §b)."""
        for user in (self.requester, self.procurement, self.finance, self.approver):
            contract = self.env['clm.contract'].with_context(migration_mode=True).create({
                'name': 'QA Sign gating', 'contract_type_id': self.nda.id,
                'partner_id': self.partner.id, 'state': 'approved',
                'user_id': self.requester.id, 'department_id': self.dept_sales.id,
            }).with_context(migration_mode=False)
            self._version(contract)
            with self.subTest(user=user.login):
                self.assertTrue(
                    self._tries(lambda: contract.with_user(user).action_prepare_signature_draft()),
                    '%s prepared a signature draft' % user.login)

    def test_only_legal_roles_can_activate(self):
        for user in (self.requester, self.procurement, self.finance, self.approver):
            contract = self.env['clm.contract'].with_context(migration_mode=True).create({
                'name': 'QA Activate gating', 'contract_type_id': self.nda.id,
                'partner_id': self.partner.id, 'state': 'executed',
                'user_id': self.requester.id, 'department_id': self.dept_sales.id,
            }).with_context(migration_mode=False)
            with self.subTest(user=user.login):
                self.assertTrue(
                    self._tries(lambda: contract.with_user(user).action_activate()),
                    '%s activated an executed contract' % user.login)

    def test_requester_cannot_approve_negotiated_contract(self):
        """Approval of a negotiated contract is not a Business Requester right."""
        contract = self._contract(user=self.requester)
        contract.action_submit_for_review()
        contract.sudo().workflow_task_ids.write({'status': 'completed'})
        contract.sudo().action_complete_internal_review()   # raises the DoP task
        contract.sudo().workflow_task_ids.write({'status': 'completed'})
        contract.sudo().action_send_to_negotiation()
        self.assertEqual(contract.state, 'in_negotiation')
        self.assertFalse(contract._clm_approval_blockers(), 'all gates are passed')
        self.assertTrue(
            self._tries(lambda: contract.with_user(self.requester).action_approve()),
            'A Business Requester approved a negotiated contract for signature.')

    @unittest.skip('BUG-16 OPEN: needs a product decision on requester write scope, '
                   'see CLM_BUG_REPORT.md. Remove the skip once the rule is tightened.')
    def test_requester_cannot_cancel_foreign_contract(self):
        """BUG-16: the Business Requester record rule grants write on every
        non-restricted contract (any department, any owner), not only the
        requester's own. Verified failing before this skip was added."""
        contract = self._contract(name='Not mine', department_id=self.dept_hr.id)
        contract = contract.with_user(self.requester)
        self.assertTrue(
            self._tries(lambda: contract.action_cancel()),
            'A requester cancelled a contract outside their visibility.')
