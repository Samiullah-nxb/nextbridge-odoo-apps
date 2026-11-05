# Part of NextContract. See LICENSE file for full copyright and licensing details.

import datetime

from odoo.tests.common import TransactionCase


class TestRequestConversion(TransactionCase):
    """Intake → Contract conversion: every applicable field must be carried over."""

    # request field -> contract field it must land on
    CONVERSION_MAP = {
        'name': 'name',
        'partner_id': 'partner_id',
        'partner_signatory_id': 'partner_signatory_id',
        'contract_type_id': 'contract_type_id',
        'department_id': 'department_id',
        'business_owner_id': 'user_id',
        'company_id': 'company_id',
        'currency_id': 'currency_id',
        'estimated_value': 'contract_value',
        'purpose': 'remarks',
        'start_date': 'effective_date',
        'expected_execution_date': 'expected_execution_date',
        'required_legal_action': 'required_legal_action',
        'urgency': 'urgency',
        'template_id': 'template_id',
        'procurement_owner_id': 'procurement_owner_id',
        'assigned_reviewer_id': 'legal_owner_id',
    }

    MAGIC_FIELDS = {
        'id', 'create_uid', 'create_date', 'write_uid', 'write_date', 'display_name',
    }

    # Request-only data. It stays readable through the link on the contract
    # (contract.request_id) and has no counterpart of its own on the contract.
    INTAKE_ONLY_FIELDS = {
        'request_ref',              # intake sequence, not the contract ref
        'state',                    # intake workflow state
        'contract_id',              # the link itself
        'requester_id',             # who raised the request
        'rejection_reason',         # only ever set on rejected requests
        'internal_approvals',
        'reference',
        'supporting_document_ids',  # copied onto the contract as attachments
    }

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Conversion is a Legal Admin action (see the form button + the write
        # guard on clm.contract.request), so run it as one.
        cls.legal_admin = cls.env['res.users'].create({
            'name': 'Legal Admin', 'login': 'intake_legal_admin',
            'email': 'legal.admin@example.com',
            'group_ids': [(4, cls.env.ref('base.group_user').id),
                          (4, cls.env.ref('clm.group_clm_legal_admin').id)],
        })

        cls.company = cls.env.company
        cls.partner = cls.env['res.partner'].create({
            'name': 'Acme Counterparty', 'company_id': False,
        })
        cls.signatory = cls.env['res.partner'].create({
            'name': 'Dana Signer', 'parent_id': cls.partner.id, 'company_id': False,
        })
        cls.contract_type = cls.env['clm.contract.type'].create({
            'name': 'Intake NDA', 'code': 'INTAKE_NDA',
        })
        cls.department = cls.env['hr.department'].create({'name': 'Intake Dept'})
        cls.currency = cls.env.ref('base.EUR')
        cls.business_owner = cls.env['res.users'].create({
            'name': 'Biz Owner', 'login': 'intake_biz_owner',
            'email': 'biz.owner@example.com',
        })
        cls.procurement_owner = cls.env['res.users'].create({
            'name': 'Proc Owner', 'login': 'intake_proc_owner',
            'email': 'proc.owner@example.com',
        })
        cls.reviewer = cls.env['res.users'].create({
            'name': 'Legal Reviewer', 'login': 'intake_reviewer',
            'email': 'reviewer@example.com',
        })
        cls.template = cls.env['clm.template'].create({
            'name': 'Standard NDA Template',
            'contract_type_id': cls.contract_type.id,
            'state': 'published',
        })
        cls.start_date = datetime.date(2026, 10, 1)
        cls.expected_execution_date = datetime.date(2026, 9, 25)

    def _new_request(self, **overrides):
        vals = {
            'name': 'Master Services Agreement with Acme',
            'partner_id': self.partner.id,
            'partner_signatory_id': self.signatory.id,
            'contract_type_id': self.contract_type.id,
            'template_id': self.template.id,
            'department_id': self.department.id,
            'company_id': self.company.id,
            'business_owner_id': self.business_owner.id,
            'procurement_owner_id': self.procurement_owner.id,
            'assigned_reviewer_id': self.reviewer.id,
            'currency_id': self.currency.id,
            'estimated_value': 125000.0,
            'purpose': 'Engage Acme to deliver managed hosting for the EU region.',
            'start_date': self.start_date,
            'expected_execution_date': self.expected_execution_date,
            'required_legal_action': 'new_contract',
            'urgency': 'urgent',
            'internal_approvals': 'CFO sign-off required',
            'reference': 'PR-2026-0042',
        }
        vals.update(overrides)
        request = self.env['clm.contract.request'].create(vals)
        attachment = self.env['ir.attachment'].create({
            'name': 'supporting.txt', 'raw': b'supporting evidence',
            'res_model': 'clm.contract.request', 'res_id': request.id,
        })
        request.supporting_document_ids = [(6, 0, attachment.ids)]
        return request

    def _convert(self, request):
        """Run the real conversion wizard, as Legal Admin, and return the contract."""
        request = request.with_user(self.legal_admin)
        request.action_submit()
        request.action_accept()
        wizard = self.env['clm.request.convert.wizard'].with_user(
            self.legal_admin,
        ).with_context(
            active_id=request.id, active_model='clm.contract.request',
        ).create({})
        action = wizard.action_convert()
        # Read back as superuser: this suite asserts on the mapping, not on ACLs.
        return self.env['clm.contract'].browse(action['res_id'])

    # ─── Conversion itself ───────────────────────────────────────────────────
    def test_convert_request_to_contract(self):
        request = self._new_request()
        contract = self._convert(request)

        self.assertTrue(contract.exists())
        self.assertEqual(request.state, 'converted')
        self.assertEqual(contract.source, 'intake')
        self.assertEqual(contract.name, 'Master Services Agreement with Acme')
        self.assertEqual(contract.state, 'draft')

    def test_originating_request_stays_linked(self):
        request = self._new_request()
        contract = self._convert(request)

        self.assertEqual(request.contract_id, contract)
        self.assertEqual(contract.request_id, request,
                         'Contract must keep a reference to its originating intake request.')
        self.assertIn(request, contract.request_ids)

    # ─── Field-by-field mapping ──────────────────────────────────────────────
    def test_estimated_value_preserved(self):
        request = self._new_request()
        contract = self._convert(request)
        self.assertEqual(contract.contract_value, 125000.0)

    def test_currency_preserved(self):
        request = self._new_request()
        contract = self._convert(request)
        self.assertEqual(contract.currency_id, self.currency)

    def test_expected_start_date_maps_to_effective_date(self):
        request = self._new_request()
        contract = self._convert(request)
        self.assertEqual(contract.effective_date, self.start_date)

    def test_expected_execution_date_does_not_populate_actual_dates(self):
        request = self._new_request()
        contract = self._convert(request)

        self.assertEqual(contract.expected_execution_date, self.expected_execution_date)
        self.assertFalse(
            contract.signing_date,
            'Signing date must stay empty until the contract is actually signed.')
        if 'execution_date' in contract._fields:
            self.assertFalse(
                contract.execution_date,
                'Execution date must stay empty until execution actually happens.')

    def test_purpose_preserved(self):
        request = self._new_request()
        contract = self._convert(request)
        self.assertEqual(contract.remarks, request.purpose)

    def test_parties_and_classification_preserved(self):
        request = self._new_request()
        contract = self._convert(request)

        self.assertEqual(contract.partner_id, self.partner)
        self.assertEqual(contract.department_id, self.department)
        self.assertEqual(contract.company_id, self.company)
        self.assertEqual(contract.contract_type_id, self.contract_type)
        self.assertEqual(contract.user_id, self.business_owner)

    def test_template_preserved(self):
        request = self._new_request()
        contract = self._convert(request)
        if 'template_id' not in contract._fields:
            self.skipTest('clm_draft is not installed; contracts carry no template.')
        self.assertEqual(contract.template_id, self.template)

    def test_intake_metadata_preserved(self):
        request = self._new_request()
        contract = self._convert(request)

        self.assertEqual(contract.required_legal_action, 'new_contract')
        self.assertEqual(contract.urgency, 'urgent')
        self.assertEqual(contract.procurement_owner_id, self.procurement_owner)
        self.assertEqual(contract.legal_owner_id, self.reviewer)

    def test_supporting_documents_copied(self):
        request = self._new_request()
        contract = self._convert(request)

        copied = self.env['ir.attachment'].search([
            ('res_model', '=', 'clm.contract'), ('res_id', '=', contract.id),
        ])
        self.assertIn('supporting.txt', copied.mapped('name'))

    def test_business_owner_falls_back_to_requester(self):
        request = self._new_request(business_owner_id=False)
        contract = self._convert(request)
        self.assertEqual(contract.user_id, request.requester_id)

    # ─── Completeness ────────────────────────────────────────────────────────
    def test_no_intake_data_silently_lost(self):
        """Every intake field is either mapped onto the contract or deliberately
        request-only. A new request field fails here until it is classified."""
        request = self._new_request()
        contract = self._convert(request)
        classified = set(self.CONVERSION_MAP) | self.INTAKE_ONLY_FIELDS

        for fname, field in self.env['clm.contract.request']._fields.items():
            if 'clm' not in field._modules or fname in self.MAGIC_FIELDS:
                continue  # mixin fields (mail.thread, …) and magic columns
            if field.compute or field.related:
                continue
            self.assertIn(
                fname, classified,
                "Intake field '%s' is neither carried over to the contract nor "
                "declared request-only; it would be silently lost on conversion."
                % fname,
            )

        for request_field, contract_field in self.CONVERSION_MAP.items():
            if contract_field not in contract._fields:
                continue  # field provided by a module that is not installed
            self.assertTrue(
                request[request_field],
                "Test data must set '%s' for this check to mean anything." % request_field)
            self.assertEqual(
                contract[contract_field], request[request_field],
                "Intake '%s' was not carried over to contract '%s'."
                % (request_field, contract_field),
            )
