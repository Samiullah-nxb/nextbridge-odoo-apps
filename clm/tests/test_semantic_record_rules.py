# Part of NextContract. See LICENSE file for full copyright and licensing details.
"""Behavioural oracle for record-rule semantics.

Authored on Odoo 16 to capture present behaviour, then re-run on Odoo 19.
A diff between the two runs is a silent semantic change — the class of
migration defect that a green install never reveals.

These assert what the rules DO, not what they arguably should do.
"""

from odoo.tests.common import TransactionCase


class TestSemanticRecordRules(TransactionCase):

    def setUp(self):
        super().setUp()
        self.company_a = self.env['res.company'].create({'name': 'Semantic Co A'})
        self.company_b = self.env['res.company'].create({'name': 'Semantic Co B'})
        self.type_nda = self.env.ref('clm.contract_type_nda')
        # The seed record for contract_type_nda has no explicit company_id in
        # its XML, so clm.contract.type's `default=lambda self: self.env.company`
        # stamps it with whatever company was active at install time. That
        # collides with _check_company_auto on clm.contract for any company
        # other than the install-time one. Force it global (company_id=False,
        # its documented "shared across all companies" meaning) for this
        # transaction so multi-company fixtures below don't trip on an
        # unrelated company mismatch. See "Pre-existing bugs" in
        # docs/superpowers/migration/odoo-19-findings.md.
        self.type_nda.sudo().write({'company_id': False})

        self.dept_legal = self.env['hr.department'].create({'name': 'Legal Dept'})
        self.dept_sales = self.env['hr.department'].create({'name': 'Sales Dept'})

        # CI_LESSONS_LEARNED 1.1/1.2: email + base.group_user are mandatory.
        self.owner = self._user('sem_owner', self.company_a, 'clm.group_clm_user')
        self.other = self._user('sem_other', self.company_a, 'clm.group_clm_user')
        self.legal = self._user('sem_legal', self.company_a, 'clm.group_clm_legal')
        self.foreign = self._user('sem_foreign', self.company_b, 'clm.group_clm_user')

        # company_id must be set explicitly: hr.employee.company_id defaults
        # to env.company at creation time (the main company in setUp), not
        # to self.other's company. res.users.employee_id is company-scoped
        # (hr/models/res_users.py _employee_ids_domain filters employee_ids
        # by env.company), so without this the rule's
        # `user.employee_id.department_id` resolves to an empty recordset
        # for self.other and the department-match branch of
        # rule_clm_contract_user silently never matches. Observed on Odoo 16
        # via test_department_match_grants_access_to_restricted failing with
        # the department fixture otherwise identical to the brief.
        self.env['hr.employee'].create({
            'name': 'Other Employee',
            'user_id': self.other.id,
            'company_id': self.company_a.id,
            'department_id': self.dept_sales.id,
        })

    def _user(self, login, company, group_xmlid):
        return self.env['res.users'].create({
            'name': login,
            'login': login,
            'email': '%s@example.com' % login,
            'company_id': company.id,
            'company_ids': [(6, 0, [company.id])],
            'group_ids': [
                (4, self.env.ref('base.group_user').id),
                (4, self.env.ref(group_xmlid).id),
            ],
        })

    def _contract(self, name, **overrides):
        values = {
            'name': name,
            'contract_type_id': self.type_nda.id,
            'company_id': self.company_a.id,
            'user_id': self.owner.id,
            'confidentiality_level': 'internal',
        }
        values.update(overrides)
        return self.env['clm.contract'].create(values)

    def _visible_to(self, user, contract):
        return bool(
            self.env['clm.contract'].with_user(user).search(
                [('id', '=', contract.id)]))

    # ─── Ownership and delegation ────────────────────────────────────────

    def test_owner_sees_own_restricted_contract(self):
        contract = self._contract('Owned Restricted',
                                  confidentiality_level='restricted')
        self.assertTrue(self._visible_to(self.owner, contract))

    def test_legal_owner_sees_restricted_contract(self):
        contract = self._contract('Legal-Owned Restricted',
                                  user_id=self.other.id,
                                  legal_owner_id=self.owner.id,
                                  confidentiality_level='restricted')
        self.assertTrue(self._visible_to(self.owner, contract))

    def test_unrelated_user_cannot_see_restricted_contract(self):
        """The one combination the user rule actually excludes."""
        contract = self._contract('Someone Elses Restricted',
                                  user_id=self.owner.id,
                                  confidentiality_level='restricted')
        self.assertFalse(self._visible_to(self.other, contract))

    # ─── Department ──────────────────────────────────────────────────────

    def test_department_match_grants_access_to_restricted(self):
        contract = self._contract('Sales Restricted',
                                  user_id=self.owner.id,
                                  department_id=self.dept_sales.id,
                                  confidentiality_level='restricted')
        self.assertTrue(self._visible_to(self.other, contract))

    def test_department_mismatch_denies_restricted(self):
        contract = self._contract('Legal Dept Restricted',
                                  user_id=self.owner.id,
                                  department_id=self.dept_legal.id,
                                  confidentiality_level='restricted')
        self.assertFalse(self._visible_to(self.other, contract))

    # ─── Confidentiality ─────────────────────────────────────────────────

    def test_non_restricted_contract_is_visible_to_any_clm_user(self):
        """Present semantics: the rule ORs confidentiality_level != restricted.

        Ownership and department are irrelevant for non-restricted contracts.
        Pinned as-is; changing it is not this workstream's business.
        """
        contract = self._contract('Internal Contract',
                                  user_id=self.owner.id,
                                  department_id=self.dept_legal.id,
                                  confidentiality_level='internal')
        self.assertTrue(self._visible_to(self.other, contract))

    def test_public_internal_is_visible_to_any_clm_user(self):
        contract = self._contract('Public Contract',
                                  user_id=self.owner.id,
                                  confidentiality_level='public')
        self.assertTrue(self._visible_to(self.other, contract))

    # ─── Multi-company ───────────────────────────────────────────────────

    def test_company_mismatch_denies_even_non_restricted(self):
        """The global multi-company rule ANDs with the group rule."""
        contract = self._contract('Company A Internal',
                                  confidentiality_level='internal')
        self.assertFalse(self._visible_to(self.foreign, contract))

    def test_company_mismatch_denies_even_the_owner(self):
        contract = self._contract('Company A Owned By Foreign',
                                  user_id=self.foreign.id)
        self.assertFalse(self._visible_to(self.foreign, contract))

    # ─── Group escalation ────────────────────────────────────────────────

    def test_legal_sees_restricted_contracts_they_do_not_own(self):
        contract = self._contract('Restricted For Legal',
                                  user_id=self.owner.id,
                                  department_id=self.dept_legal.id,
                                  confidentiality_level='restricted')
        self.assertTrue(self._visible_to(self.legal, contract))

    def test_legal_is_still_bound_by_company_scope(self):
        """Group escalation must not defeat the global company rule."""
        foreign_legal = self._user(
            'sem_foreign_legal', self.company_b, 'clm.group_clm_legal')
        contract = self._contract('Company A Restricted',
                                  confidentiality_level='restricted')
        self.assertFalse(self._visible_to(foreign_legal, contract))

    # ─── Field-level group restriction ───────────────────────────────────

    def test_contract_value_hidden_from_standard_user(self):
        """contract_value carries groups=legal_admin,manager."""
        readable = self.env['clm.contract'].with_user(
            self.other).fields_get().keys()
        self.assertNotIn('contract_value', readable)

    def test_contract_value_visible_to_manager(self):
        manager = self._user(
            'sem_manager', self.company_a, 'clm.group_clm_manager')
        readable = self.env['clm.contract'].with_user(
            manager).fields_get().keys()
        self.assertIn('contract_value', readable)
