# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError


class TestDraftAssembly(TransactionCase):

    def setUp(self):
        super().setUp()
        # Build: published clause → published template → contract
        self.clause = self.env['clm.clause'].create({
            'name': 'Standard Confidentiality',
            'category': 'confidentiality',
            'standard_wording': '<p>Each party agrees to maintain confidentiality for {{partner_name}}.</p>',
            'risk_level': 'high',
        })
        self.clause.action_submit_review()
        self.clause.action_approve()
        self.clause.action_publish()

        self.template = self.env['clm.template'].create({
            'name': 'NDA Template',
            'contract_type_id': self.env.ref('clm.contract_type_nda').id,
            'content': '<p>This NDA is entered into by {{partner_name}}.</p>',
        })
        self.env['clm.template.clause.line'].create({
            'template_id': self.template.id,
            'clause_id': self.clause.id,
            'sequence': 10,
            'section': 'Confidentiality',
            'required': True,
        })
        self.template.action_approve()
        self.template.action_publish()

        self.partner = self.env['res.partner'].create({'name': 'Acme Corp'})
        self.contract_type_nda = self.env.ref('clm.contract_type_nda')
        self.contract = self.env['clm.contract'].create({
            'name': 'Test NDA',
            'contract_type_id': self.contract_type_nda.id,
            'partner_id': self.partner.id,
            'template_id': self.template.id,
        })

    def test_generate_draft_produces_preview(self):
        request = self.contract._generate_draft()
        self.assertEqual(request.state, 'done')
        self.assertIn('Acme Corp', request.result_preview)
        self.assertIn('Confidentiality', request.result_preview)
        self.assertIn('NDA Template', request.result_preview)

    def test_generate_draft_creates_version(self):
        request = self.contract._generate_draft()
        self.assertTrue(request.result_version_id)
        self.assertEqual(request.result_version_id.version_type, 'internal_draft')

    def test_generate_draft_requires_template(self):
        self.contract.template_id = False
        with self.assertRaises(UserError):
            self.contract._generate_draft()

    def test_generate_draft_requires_published_template(self):
        draft_tmpl = self.env['clm.template'].create({
            'name': 'Unpublished Template',
            'contract_type_id': self.env.ref('clm.contract_type_nda').id,
        })
        self.contract.template_id = draft_tmpl
        with self.assertRaises(UserError):
            self.contract._generate_draft()

    def test_variable_substitution(self):
        request = self.contract._generate_draft()
        # partner_name variable should be substituted
        self.assertIn('Acme Corp', request.result_preview)
        # Raw placeholder should NOT remain
        self.assertNotIn('{{partner_name}}', request.result_preview)

    def test_format_template_body(self):
        self.assertEqual(self.contract._clm_document_generation_format(), 'template_body')
        self.assertFalse(self.contract.base_docx_version_id)

    def test_format_no_template(self):
        bare = self.env['clm.contract'].create({
            'name': 'No Template Contract',
            'contract_type_id': self.contract_type_nda.id,
            'partner_id': self.partner.id,
        })
        self.assertEqual(bare._clm_document_generation_format(), 'default_no_template')
        self.assertFalse(bare.base_docx_version_id)

    def test_format_template_without_body_or_base(self):
        empty_tmpl = self.env['clm.template'].create({
            'name': 'Empty Shell Template',
            'contract_type_id': self.contract_type_nda.id,
            'content': False,
        })
        self.env['clm.template.clause.line'].create({
            'template_id': empty_tmpl.id,
            'clause_id': self.clause.id,
            'sequence': 10,
            'section': 'Confidentiality',
            'required': True,
        })
        empty_tmpl.action_approve()
        empty_tmpl.action_publish()
        contract = self.env['clm.contract'].create({
            'name': 'Shell Template Contract',
            'contract_type_id': self.contract_type_nda.id,
            'partner_id': self.partner.id,
            'template_id': empty_tmpl.id,
        })
        self.assertEqual(contract._clm_document_generation_format(), 'default_template')
        self.assertFalse(contract.base_docx_version_id)
        request = contract._generate_draft(renderer='plain_text')
        self.assertEqual(request.state, 'done')
        self.assertTrue(request.result_version_id)

    def test_party_and_term_placeholders_resolved(self):
        from odoo.addons.clm.services.assembly_engine import DraftAssemblyEngine

        self.partner.write({
            'street': '1 Market St',
            'city': 'San Francisco',
            'zip': '94105',
            'email': 'legal@acme.test',
            'vat': 'US-ACME-1',
        })
        self.contract.write({
            'notice_period_days': 30,
            'auto_renewal': True,
            'risk_rating': 'medium',
            'confidentiality_level': 'restricted',
        })
        variables = DraftAssemblyEngine(self.template, self.contract)._resolve_variables()
        self.assertEqual(variables['partner_name'], 'Acme Corp')
        self.assertIn('1 Market St', variables['partner_address'])
        self.assertEqual(variables['partner_city'], 'San Francisco')
        self.assertEqual(variables['partner_email'], 'legal@acme.test')
        self.assertEqual(variables['partner_vat'], 'US-ACME-1')
        self.assertEqual(variables['notice_period'], '30 days')
        self.assertEqual(variables['auto_renewal'], 'Yes')
        self.assertTrue(variables['today'])
        self.assertEqual(variables['risk_rating'], 'Medium')
        self.assertEqual(variables['confidentiality_level'], 'Restricted Legal')
        self.assertIn('company_name', variables)
        self.assertIn('company_address', variables)
