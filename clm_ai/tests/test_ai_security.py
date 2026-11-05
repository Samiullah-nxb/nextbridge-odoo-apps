# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError


class TestAiSecurity(TransactionCase):

    def setUp(self):
        super().setUp()
        self.contract_type_nda = self.env.ref('clm.contract_type_nda')
        self.contract = self.env['clm.contract'].create({
            'name': 'Security Test Contract',
            'contract_type_id': self.contract_type_nda.id,
        })
        # Note: In a real system, version creation is more complex, but we mock enough for testing.
        self.version = self.env['clm.contract.version'].create({
            'contract_id': self.contract.id,
            'version_type': 'internal_draft',
        })
        self.job = self.env['clm.ai.job'].create({
            'contract_version_id': self.version.id,
        })
        
        self.standard_user = self.env['res.users'].create({
            'name': 'Standard User',
            'login': 'std_user',
            'group_ids': [(4, self.env.ref('clm.group_clm_user').id)],
        })

    def _create_suggestion(self, field_name, value):
        return self.env['clm.ai.field.suggestion'].create({
            'job_id': self.job.id,
            'field_name': field_name,
            'suggested_value': value,
        })

    def test_approve_allowed_field_updates_contract(self):
        suggestion = self._create_suggestion(field_name='effective_date', value='2027-01-01')
        suggestion.action_approve()
        self.assertEqual(str(self.contract.effective_date), '2027-01-01')
        self.assertEqual(suggestion.status, 'approved')

    def test_approve_state_field_blocked(self):
        suggestion = self._create_suggestion(field_name='state', value='active')
        with self.assertRaisesRegex(UserError, "AI is not permitted to modify"):
            suggestion.action_approve()

    def test_approve_user_id_field_blocked(self):
        suggestion = self._create_suggestion(field_name='user_id', value='1')
        with self.assertRaisesRegex(UserError, "AI is not permitted to modify"):
            suggestion.action_approve()

    def test_approve_confidentiality_field_blocked(self):
        suggestion = self._create_suggestion(field_name='confidentiality_level', value='public')
        with self.assertRaisesRegex(UserError, "AI is not permitted to modify"):
            suggestion.action_approve()

    def test_unauthorized_approval(self):
        from odoo.exceptions import AccessError
        suggestion = self._create_suggestion(field_name='effective_date', value='2027-01-01')
        
        # Switch to standard user
        suggestion_as_user = suggestion.with_user(self.standard_user)
        
        # Assert standard group_clm_user cannot approve AI suggestions
        with self.assertRaisesRegex(AccessError, "Only Legal Reviewers can approve or reject AI suggestions."):
            suggestion_as_user.action_approve()
            
    def test_record_rule_fencing(self):
        from odoo.exceptions import AccessError
        # Create a restricted contract that the standard user does not own
        restricted_contract = self.env['clm.contract'].create({
            'name': 'Restricted Contract',
            'contract_type_id': self.contract_type_nda.id,
            'confidentiality_level': 'restricted',
            'user_id': self.env.user.id, # Admin owns it
        })
        restricted_version = self.env['clm.contract.version'].create({
            'contract_id': restricted_contract.id,
            'version_type': 'internal_draft',
        })
        restricted_job = self.env['clm.ai.job'].create({
            'contract_version_id': restricted_version.id,
        })
        restricted_suggestion = self.env['clm.ai.field.suggestion'].create({
            'job_id': restricted_job.id,
            'field_name': 'effective_date',
            'suggested_value': '2028-01-01',
        })
        
        # Standard user tries to read the AI job for the restricted contract
        with self.assertRaises(AccessError):
            restricted_job.with_user(self.standard_user).read(['name'])
            
        # Standard user tries to read the AI suggestion for the restricted contract
        with self.assertRaises(AccessError):
            restricted_suggestion.with_user(self.standard_user).read(['field_name'])

    def test_relational_write(self):
        # Create a suggestion that maps partner_id to a known integer
        test_partner = self.env['res.partner'].create({'name': 'Test Partner Co'})
        suggestion = self.env['clm.ai.field.suggestion'].create({
            'job_id': self.job.id,
            'field_name': 'partner_id',
            'suggested_value': 'Test Partner Co',
            'resolved_record_id': test_partner.id,
            'resolved_model': 'res.partner',
        })
        suggestion.action_approve()
        
        self.assertEqual(suggestion.status, 'approved')
        self.assertEqual(self.contract.partner_id.id, test_partner.id)
        
    def test_needs_mapping_wizard_integration(self):
        # Create a suggestion that needs mapping
        suggestion = self.env['clm.ai.field.suggestion'].create({
            'job_id': self.job.id,
            'field_name': 'partner_id',
            'suggested_value': 'Unknown Partner Co',
            'status': 'needs_mapping'
        })
        
        test_partner = self.env['res.partner'].create({'name': 'Known Partner Co'})
        
        # Instantiate wizard
        wizard = self.env['clm.ai.mapping.wizard'].create({
            'suggestion_id': suggestion.id,
            'partner_id': test_partner.id
        })
        
        # Confirm mapping
        wizard.action_confirm_mapping()
        
        # Assert transition
        self.assertEqual(suggestion.status, 'approved')
        self.assertEqual(suggestion.resolved_record_id, test_partner.id)
        self.assertEqual(self.contract.partner_id.id, test_partner.id)

    def test_unknown_fields(self):
        # Allowlist bypass check for unknown fields
        # Wait, the ALLOWED_AI_FIELDS allows some fields but what if we force a field not on the model?
        # Actually, ALLOWED_AI_FIELDS is checked first. We need a field in ALLOWED_AI_FIELDS that doesn't exist?
        # No, all fields in ALLOWED_AI_FIELDS exist.
        # But let's temporarily mock ALLOWED_AI_FIELDS or test directly what the requirement asks: 
        # "Assert attempting to approve an AI field suggestion for a field that doesn't exist on the schema raises a UserError."
        # If we put a bogus field, it raises UserError because of ALLOWED_AI_FIELDS. 
        # To strictly test "does not exist on the schema", we can add a non-existent field to ALLOWED_AI_FIELDS temporarily.
        suggestion = self.env['clm.ai.field.suggestion'].create({
            'job_id': self.job.id,
            'field_name': 'bogus_field_not_on_model',
            'suggested_value': 'value',
        })
        # Monkey patch ALLOWED_AI_FIELDS for this test
        original_allowed = type(self.env['clm.ai.field.suggestion']).ALLOWED_AI_FIELDS
        
        from unittest.mock import patch
        with patch.object(type(self.env['clm.ai.field.suggestion']), 'ALLOWED_AI_FIELDS', original_allowed | frozenset({'bogus_field_not_on_model'})):
            with self.assertRaisesRegex(UserError, "does not exist on contract model"):
                suggestion.action_approve()
