# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models, fields, api


class NextcontractContract(models.Model):
    """Extends nextcontract.contract with document version relation."""
    _inherit = 'nextcontract.contract'

    version_ids = fields.One2many(
        'nextcontract.contract.version', 'contract_id', string='Document Versions'
    )
    version_count = fields.Integer(
        string='Version Count', compute='_compute_version_count'
    )

    @api.depends('version_ids')
    def _compute_version_count(self):
        # Read through sudo: how many versions a contract has is a property of
        # the contract, and this count is shown wherever the contract is. Only
        # NextContract Business Requesters, Legal Reviewers and Legal Admins have read
        # access to nextcontract.contract.version itself, so without this the field
        # raises AccessError for the Read-Only, Approver, Finance and
        # Procurement roles that can otherwise read the contract.
        for rec in self:
            rec.version_count = len(rec.sudo().version_ids)
