# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo.tests.common import TransactionCase
from odoo.exceptions import AccessError, UserError


class TestClauseLibrary(TransactionCase):

    def setUp(self):
        super().setUp()
        self.clause = self.env['clm.clause'].create({
            'name': 'Standard Confidentiality',
            'category': 'confidentiality',
            'standard_wording': '<p>Each party agrees to maintain confidentiality.</p>',
            'risk_level': 'high',
        })
        self.user = self.env['res.users'].create({
            'name': 'Test User',
            'login': 'test_user_lib',
            'group_ids': [(4, self.env.ref('clm.group_clm_user').id)]
        })

    def test_lifecycle_progression(self):
        self.assertEqual(self.clause.state, 'draft')
        self.clause.action_submit_review()
        self.assertEqual(self.clause.state, 'under_review')
        self.clause.action_approve()
        self.assertEqual(self.clause.state, 'approved')
        self.assertEqual(self.clause.approved_by_id.id, self.env.uid)
        self.clause.action_publish()
        self.assertEqual(self.clause.state, 'published')

    def test_lifecycle_blocks_invalid_transitions(self):
        with self.assertRaises(UserError):
            self.clause.action_approve()  # Can't approve a draft directly

    def test_version_increments_on_approve(self):
        initial_version = self.clause.version
        self.clause.action_submit_review()
        self.clause.action_approve()
        self.assertEqual(self.clause.version, initial_version + 1)

    def test_locked_clause_blocks_write(self):
        self.clause.editability = 'locked'
        with self.assertRaises(AccessError):
            self.clause.with_user(self.user).write({'name': 'Hacked Name'})

    def test_unlock_allows_edit(self):
        self.clause.editability = 'locked'
        self.clause.action_unlock()
        self.assertEqual(self.clause.editability, 'editable')
        self.clause.write({'name': 'Updated Confidentiality'})
        self.assertEqual(self.clause.name, 'Updated Confidentiality')


class TestTemplateLibrary(TransactionCase):

    def setUp(self):
        super().setUp()
        self.clause = self.env['clm.clause'].create({
            'name': 'Governing Law',
            'category': 'governing_law',
        })
        self.template = self.env['clm.template'].create({
            'name': 'Standard NDA',
            'contract_type_id': self.env.ref('clm.contract_type_nda').id,
        })

    def test_clause_line_ordering(self):
        self.env['clm.template.clause.line'].create([
            {'template_id': self.template.id, 'clause_id': self.clause.id, 'sequence': 10, 'section': 'Governing Law'},
        ])
        lines = self.template.clause_line_ids
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].sequence, 10)
        self.assertEqual(lines[0].section, 'Governing Law')

    def test_published_template_blocks_write_for_standard_user(self):
        self.template.action_approve()
        self.template.action_publish()
        # Standard user (no legal admin) should be blocked
        standard_user = self.env['res.users'].create({
            'name': 'Standard User',
            'login': 'std_user_lib_test',
        })
        with self.assertRaises(AccessError):
            self.template.with_user(standard_user).write({'name': 'Hijacked Template'})
