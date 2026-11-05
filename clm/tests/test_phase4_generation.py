# Part of NextContract. See LICENSE file for full copyright and licensing details.
"""
Tests for Phase 4 draft generation:
- Conditional section evaluation
- Generation audit (SHA-256, version snapshot, input snapshot)
- Plain-text renderer still works via _generate_draft(renderer='plain_text')
- Renderer dispatching
"""

import json

from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError


class TestPhase4DraftGeneration(TransactionCase):

    def setUp(self):
        super().setUp()
        # NDA clause (unconditional)
        self.clause_nda = self.env['clm.clause'].create({
            'name': 'NDA Confidentiality',
            'category': 'confidentiality',
            'standard_wording': '<p>Each party agrees to keep information confidential for {{partner_name}}.</p>',
            'risk_level': 'high',
        })
        self.clause_nda.action_submit_review()
        self.clause_nda.action_approve()
        self.clause_nda.action_publish()

        # Services-only clause (conditional on contract_type)
        self.clause_services = self.env['clm.clause'].create({
            'name': 'Service Level',
            'category': 'service_levels',
            'standard_wording': '<p>Provider guarantees 99.9% uptime.</p>',
        })
        self.clause_services.action_submit_review()
        self.clause_services.action_approve()
        self.clause_services.action_publish()

        self.contract_type_nda = self.env.ref('clm.contract_type_nda')
        self.contract_type_services = self.env.ref('clm.contract_type_services')

        # Template with both clauses; services clause is conditional
        self.template = self.env['clm.template'].create({
            'name': 'Phase 4 NDA',
            'contract_type_id': self.contract_type_nda.id,
            'content': '<p>This NDA is entered into by {{partner_name}}.</p>',
        })
        self.env['clm.template.clause.line'].create([
            {
                'template_id': self.template.id,
                'clause_id': self.clause_nda.id,
                'sequence': 10,
                'section': 'Confidentiality',
                'required': True,
                # No condition — always included
            },
            {
                'template_id': self.template.id,
                'clause_id': self.clause_services.id,
                'sequence': 20,
                'section': 'Service Levels',
                'required': False,
                # Only include for services contracts
                'condition_enabled': True,
                'condition_domain': "[('contract_type_id.code', '=', 'services')]",
            },
        ])
        self.template.action_approve()
        self.template.action_publish()

        self.partner = self.env['res.partner'].create({'name': 'Test Partner'})
        self.contract = self.env['clm.contract'].create({
            'name': 'Phase 4 Test NDA',
            'contract_type_id': self.contract_type_nda.id,
            'partner_id': self.partner.id,
            'template_id': self.template.id,
        })

    def test_plain_text_still_works(self):
        request = self.contract._generate_draft(renderer='plain_text')
        self.assertEqual(request.state, 'done')
        self.assertIn('Test Partner', request.result_preview)

    def test_conditional_section_excluded_for_nda(self):
        """Services-only clause should NOT appear in an NDA contract."""
        request = self.contract._generate_draft(renderer='plain_text')
        self.assertNotIn('Service Level', request.result_preview)

    def test_conditional_section_included_for_services(self):
        """Services-only clause SHOULD appear in a services contract."""
        self.contract.contract_type_id = self.contract_type_services.id
        request = self.contract._generate_draft(renderer='plain_text')
        self.assertIn('Service Level', request.result_preview)

    def test_generation_audit_fields_populated(self):
        request = self.contract._generate_draft(renderer='plain_text')
        self.assertTrue(request.output_hash, "SHA-256 hash must be populated")
        self.assertEqual(len(request.output_hash), 64, "SHA-256 is 64 hex chars")
        self.assertTrue(request.input_snapshot)
        snapshot = json.loads(request.input_snapshot)
        self.assertIn('partner_name', snapshot)

    def test_clause_versions_snapshot_captured(self):
        request = self.contract._generate_draft(renderer='plain_text')
        self.assertTrue(request.clause_versions_snapshot)
        versions = json.loads(request.clause_versions_snapshot)
        # NDA clause should be in snapshot (services clause excluded by condition)
        self.assertIn(str(self.clause_nda.id), versions)
        self.assertNotIn(str(self.clause_services.id), versions)

    def test_template_version_snapshot_captured(self):
        request = self.contract._generate_draft(renderer='plain_text')
        self.assertEqual(request.template_version_snapshot, self.template.version_number)

    def test_contract_version_record_created(self):
        request = self.contract._generate_draft(renderer='plain_text')
        self.assertTrue(request.result_version_id)
        self.assertEqual(request.result_version_id.version_type, 'internal_draft')
        self.assertEqual(request.result_version_id.contract_id.id, self.contract.id)

    def test_unknown_renderer_raises(self):
        with self.assertRaises(UserError):
            self.contract._generate_draft(renderer='word_perfect')

    def test_requires_published_template(self):
        draft_tmpl = self.env['clm.template'].create({
            'name': 'Not Published',
            'contract_type_id': self.contract_type_nda.id,
        })
        self.contract.template_id = draft_tmpl
        with self.assertRaises(UserError):
            self.contract._generate_draft()
