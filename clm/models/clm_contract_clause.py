# Part of NextContract. See LICENSE file for full copyright and licensing details.
"""
Contract clauses — the wording of one contract.

A template lists the library clauses a contract of its type starts from. When
the contract is drafted, those clauses are copied here and become the
contract's own wording: Legal can change a clause, add one from the library or
write a custom one, without touching the clause library or the template.

A contract version freezes this wording (clm.contract.version.content_snapshot)
at the moment it is created, so editing a clause never changes an existing
version. Once the contract is out for signature or signed, the clauses are
locked; changing them takes an amendment.
"""

from markupsafe import Markup

from odoo import api, fields, models
from odoo.exceptions import AccessError


def normalise_wording(html):
    """Visible wording only — formatting and whitespace do not count as a change."""
    return ' '.join(Markup(str(html or '')).striptags().split())


class ClmContractClause(models.Model):
    _name = 'clm.contract.clause'
    _description = 'Contract Clause'
    _order = 'contract_id, sequence, id'

    contract_id = fields.Many2one(
        'clm.contract', string='Contract', required=True, ondelete='cascade', index=True
    )
    company_id = fields.Many2one(
        related='contract_id.company_id', store=True, index=True
    )
    sequence = fields.Integer(string='Sequence', default=10)
    section = fields.Char(string='Section')
    title = fields.Char(string='Clause Title', required=True)
    content = fields.Html(
        string='Wording',
        help='Use placeholders such as {{notice_period_days}} or {{partner_name}} '
             'so the wording always carries the contract\'s own values.'
    )
    clause_id = fields.Many2one(
        'clm.clause', string='Library Clause', ondelete='set null', index=True,
        domain="[('state', '=', 'published')]",
        help='The library clause this wording started from, if any.'
    )
    clause_version = fields.Integer(string='Library Version')
    source = fields.Selection([
        ('template', 'Template'),
        ('library', 'Clause Library'),
        ('custom', 'Custom Wording'),
        ('document', 'Contract Document'),
    ], string='Source', required=True, default='custom')
    required = fields.Boolean(string='Required', default=True)
    editability = fields.Selection(
        related='clause_id.editability', string='Library Editability'
    )
    is_modified = fields.Boolean(
        string='Modified', compute='_compute_is_modified', compute_sudo=True,
        help='The wording differs from the library clause it came from.'
    )

    @api.depends('content', 'clause_id', 'clause_id.standard_wording')
    def _compute_is_modified(self):
        for line in self:
            line.is_modified = bool(line.clause_id) and (
                normalise_wording(line.content) != normalise_wording(line.clause_id.standard_wording)
            )

    @api.onchange('clause_id')
    def _onchange_clause_id(self):
        clause = self.clause_id
        if not clause:
            return
        self.title = clause.name
        self.content = clause.standard_wording
        self.clause_version = clause.version
        if not self.section:
            self.section = dict(
                clause._fields['category']._description_selection(self.env)
            ).get(clause.category, False)
        if self.source == 'custom':
            self.source = 'library'

    # -------------------------------------------------------------------------
    # Locking
    # -------------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        contract_ids = {vals['contract_id'] for vals in vals_list if vals.get('contract_id')}
        self.env['clm.contract'].browse(contract_ids)._clm_check_content_editable()
        Clause = self.env['clm.clause'].sudo()
        for vals in vals_list:
            if not vals.get('clause_id'):
                continue
            if vals.get('source', 'custom') == 'custom':
                vals['source'] = 'library'
            if not vals.get('clause_version'):
                vals['clause_version'] = Clause.browse(vals['clause_id']).version
        return super().create(vals_list)

    def write(self, vals):
        self.contract_id._clm_check_content_editable()
        if vals.get('contract_id'):
            self.env['clm.contract'].browse(vals['contract_id'])._clm_check_content_editable()
        if 'content' in vals:
            self._check_protected_wording(vals['content'])
        return super().write(vals)

    def unlink(self):
        self.contract_id._clm_check_content_editable()
        return super().unlink()

    def _check_protected_wording(self, new_content):
        """Locked and restricted library clauses keep their wording unless Legal Admin changes it."""
        if self.env.su or self.env.user.has_group('clm.group_clm_legal_admin'):
            return
        for line in self.sudo():
            editability = line.clause_id.editability
            if editability not in ('locked', 'restricted'):
                continue
            if normalise_wording(new_content) == normalise_wording(line.content):
                continue
            raise AccessError(
                "Clause '%s' comes from a %s library clause. Only Legal Administrators "
                "can change its wording." % (line.title, editability)
            )
