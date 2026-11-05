# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError


class TestContractVersion(TransactionCase):

    def setUp(self):
        super().setUp()
        self.contract_type_nda = self.env.ref('clm.contract_type_nda')
        self.user = self.env['res.users'].create({
            'name': 'Test User',
            'login': 'test_user_doc',
            'group_ids': [(4, self.env.ref('clm.group_clm_user').id)]
        })
        self.contract = self.env['clm.contract'].create({
            'name': 'Version Test Contract',
            'contract_type_id': self.contract_type_nda.id,
        })

    def test_auto_version_number(self):
        v1 = self.env['clm.contract.version'].create({
            'contract_id': self.contract.id,
            'version_type': 'internal_draft',
        })
        v2 = self.env['clm.contract.version'].create({
            'contract_id': self.contract.id,
            'version_type': 'legal_draft',
        })
        self.assertEqual(v1.version_number, 1)
        self.assertEqual(v2.version_number, 2)

    def test_executed_version_blocks_write(self):
        version = self.env['clm.contract.version'].create({
            'contract_id': self.contract.id,
            'version_type': 'executed',
        })
        with self.assertRaises(UserError):
            version.with_user(self.user).write({'change_summary': 'Tamper attempt'})

    def test_executed_version_blocks_unlink(self):
        version = self.env['clm.contract.version'].create({
            'contract_id': self.contract.id,
            'version_type': 'executed',
        })
        with self.assertRaises(UserError):
            version.with_user(self.user).unlink()

    def test_non_executed_version_can_be_modified(self):
        version = self.env['clm.contract.version'].create({
            'contract_id': self.contract.id,
            'version_type': 'internal_draft',
        })
        version.write({'change_summary': 'Updated summary'})
        self.assertEqual(version.change_summary, 'Updated summary')

    def test_form_default_does_not_defeat_auto_numbering(self):
        """The form sends an empty version number; each line must still be numbered."""
        Version = self.env['clm.contract.version']
        first = Version.create({'contract_id': self.contract.id, 'version_number': 0})
        second = Version.create({'contract_id': self.contract.id, 'version_number': 0})
        self.assertEqual((first.version_number, second.version_number), (1, 2))

    def test_activation_supersedes_older_working_versions(self):
        Version = self.env['clm.contract.version']
        v1 = Version.create({'contract_id': self.contract.id})
        v1.action_activate()
        executed = Version.create({
            'contract_id': self.contract.id, 'version_type': 'executed', 'status': 'active',
        })
        v3 = Version.create({'contract_id': self.contract.id})
        v3.action_activate()
        self.assertEqual(v1.status, 'superseded')
        self.assertEqual(v3.status, 'active')
        self.assertEqual(executed.status, 'active', 'Executed versions are never touched')

    def test_upload_file_is_stored_in_documents(self):
        version = self.env['clm.contract.version'].create({
            'contract_id': self.contract.id,
            'document_file': 'JVBERi0xLjAK',
            'document_filename': 'counter_signed.pdf',
        })
        self.assertTrue(version.document_id)
        self.assertEqual(version.document_id.name, 'counter_signed.pdf')
        self.assertEqual(version.document_id.res_model, 'clm.contract')
        self.assertEqual(version.document_id.res_id, self.contract.id)
