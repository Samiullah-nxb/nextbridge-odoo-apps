# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError

class TestContractRequest(TransactionCase):
    def test_request_workflow(self):
        req = self.env['clm.contract.request'].create({'name': 'Need NDA'})
        self.assertEqual(req.state, 'new')
        self.assertEqual(req.requester_id, self.env.user)
        req.action_submit()
        self.assertEqual(req.state, 'submitted')

    def test_request_write_restriction(self):
        # Run as a plain Business Requester: OdooBot may itself be a Legal Admin
        # (who is allowed to edit submitted requests) depending on the database.
        requester = self.env['res.users'].create({
            'name': 'Plain Requester', 'login': 'plain_requester_wr',
            'email': 'plain_requester_wr@example.com',
            'group_ids': [(4, self.env.ref('base.group_user').id),
                          (4, self.env.ref('clm.group_clm_user').id)],
        })
        req = self.env['clm.contract.request'].with_user(requester).create({'name': 'Need MSA'})
        req.action_submit()
        with self.assertRaises(UserError):
            req.write({'name': 'Updated Title'})
