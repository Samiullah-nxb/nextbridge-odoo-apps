# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo.tests.common import TransactionCase


class TestModuleInstallation(TransactionCase):
    """Verify that all groups and models resolve correctly."""

    def test_security_groups_exist(self):
        """All groups referenced in ACLs across modules must exist."""
        groups = [
            'nextcontract.group_nextcontract_read_only',
            'nextcontract.group_nextcontract_user',
            'nextcontract.group_nextcontract_legal',
            'nextcontract.group_nextcontract_manager',
            'nextcontract.group_nextcontract_legal_admin',
        ]
        for xmlid in groups:
            group = self.env.ref(xmlid, raise_if_not_found=False)
            self.assertTrue(
                group,
                f"Security group '{xmlid}' must exist for dependent modules."
            )

    def test_contract_type_seed_data_loaded(self):
        """All 13 contract types from PRD §23 must be available."""
        types = self.env['nextcontract.contract.type'].search([])
        self.assertGreaterEqual(len(types), 13)
        # Verify key codes
        codes = types.mapped('code')
        self.assertIn('nda', codes)
        self.assertIn('services', codes)
        self.assertIn('supply', codes)

    def test_sequence_exists(self):
        """Contract ID sequence must be configured."""
        seq = self.env['ir.sequence'].search([('code', '=', 'nextcontract.contract')])
        self.assertTrue(seq)
        self.assertIn('CNT-', seq.prefix)

    def test_group_hierarchy(self):
        """Legal Admin should imply Manager (which implies Legal, User, Read-Only)."""
        admin_group = self.env.ref('nextcontract.group_nextcontract_legal_admin')
        manager_group = self.env.ref('nextcontract.group_nextcontract_manager')
        # Direct implication: admin → manager
        self.assertIn(manager_group, admin_group.implied_ids)
