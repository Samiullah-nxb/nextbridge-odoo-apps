# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models, fields, api
from odoo.exceptions import AccessError, UserError

_CLAUSE_CATEGORIES = [
    ('confidentiality', 'Confidentiality'),
    ('data_protection', 'Data Protection'),
    ('intellectual_property', 'Intellectual Property'),
    ('indemnity', 'Indemnity'),
    ('limitation_of_liability', 'Limitation of Liability'),
    ('termination', 'Termination'),
    ('force_majeure', 'Force Majeure'),
    ('governing_law', 'Governing Law'),
    ('dispute_resolution', 'Dispute Resolution'),
    ('compliance', 'Compliance'),
    ('sanctions', 'Sanctions'),
    ('export_control', 'Export Control'),
    ('anti_bribery', 'Anti-Bribery'),
    ('anti_corruption', 'Anti-Corruption'),
    ('audit_rights', 'Audit Rights'),
    ('assignment', 'Assignment'),
    ('subcontracting', 'Subcontracting'),
    ('taxes', 'Taxes'),
    ('payment', 'Payment'),
    ('service_levels', 'Service Levels'),
    ('warranty', 'Warranty'),
    ('renewal', 'Renewal'),
    ('survival', 'Survival'),
    ('regulatory_compliance', 'Regulatory Compliance'),
]

_EDITABLE_STATES = ('draft', 'under_review')

# Fields that may change while a clause is Locked (lifecycle / unlock / lock).
# Content wording and classification stay protected until Unlock succeeds.
_LOCK_BYPASS_FIELDS = frozenset({
    'editability',
    'state',
    'approved_by_id',
    'approval_date',
    'version',
    'last_updated',
    'message_main_attachment_id',
})


class ClmClause(models.Model):
    """
    Standard legal clause library (PRD §26).

    Lifecycle: draft → under_review → approved → published → superseded / retired

    Editability levels (PRD §32):
      editable   — any authorized user can modify
      restricted — Legal Admin only
      locked     — requires explicit unlock action + audit event
    """
    _name = 'clm.clause'
    _description = 'Clause Library'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    # -------------------------------------------------------------------------
    # Core fields (PRD §26)
    # -------------------------------------------------------------------------
    name = fields.Char(string='Clause Title', required=True, tracking=True)
    code = fields.Char(
        string='Clause Key',
        index=True,
        copy=False,
        tracking=True,
        help='Stable technical key for templates '
             '(e.g. limitation_of_liability). Used as '
             '{{ clause_set.clauses.<key>.title }}.',
    )
    category = fields.Selection(
        _CLAUSE_CATEGORIES, string='Category', required=True, tracking=True
    )
    description = fields.Text(
        string='Description',
        help='Available as {{ clause_set.clauses.<key>.description }}.',
    )
    standard_wording = fields.Html(string='Standard Wording')
    alternate_wording = fields.Html(string='Alternate Wording')
    risk_level = fields.Selection([
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
        ('critical', 'Critical'),
    ], string='Risk Level', tracking=True)
    owner_id = fields.Many2one('res.users', string='Owner', tracking=True)
    approved_by_id = fields.Many2one('res.users', string='Approved By', tracking=True)
    approval_date = fields.Date(string='Approval Date', tracking=True)
    last_updated = fields.Datetime(string='Last Updated', readonly=True)
    applicable_contract_type_ids = fields.Many2many(
        'clm.contract.type', string='Applicable Contract Types',
        help='Leave empty for all types'
    )
    jurisdiction = fields.Char(string='Jurisdiction')
    version = fields.Integer(string='Version', default=1, readonly=True)

    # -------------------------------------------------------------------------
    # Lifecycle (PRD §28)
    # -------------------------------------------------------------------------
    state = fields.Selection([
        ('draft', 'Draft'),
        ('under_review', 'Under Review'),
        ('approved', 'Approved'),
        ('published', 'Published'),
        ('superseded', 'Superseded'),
        ('retired', 'Retired'),
    ], string='Status', default='draft', tracking=True)

    # -------------------------------------------------------------------------
    # Editability / locking (PRD §32)
    # -------------------------------------------------------------------------
    editability = fields.Selection([
        ('editable', 'Editable'),
        ('restricted', 'Restricted'),
        ('locked', 'Locked'),
    ], string='Editability', default='editable', tracking=True)
    is_locked = fields.Boolean(
        string='Currently Locked', compute='_compute_is_locked', store=True
    )

    # -------------------------------------------------------------------------
    # Computed
    # -------------------------------------------------------------------------
    @api.depends('editability')
    def _compute_is_locked(self):
        for clause in self:
            clause.is_locked = clause.editability == 'locked'

    # -------------------------------------------------------------------------
    # Write-time permission enforcement (PRD §32)
    # -------------------------------------------------------------------------
    def _check_clause_edit_permission(self):
        """
        Enforce editability rules before content writes.
        Called from write() / unlink() when protected fields change.
        """
        is_legal_admin = self.env.user.has_group('clm.group_clm_legal_admin')

        for clause in self:
            if clause.editability == 'locked' and not self.env.su:
                raise AccessError(
                    f"Clause '{clause.name}' is locked. "
                    "Use 'Unlock Clause' to make changes — this creates an audit event."
                )
            if clause.editability == 'restricted' and not is_legal_admin and not self.env.su:
                raise AccessError(
                    f"Clause '{clause.name}' is restricted to Legal Administrators."
                )

    def write(self, vals):
        # Unlock / lock / lifecycle must work even while Locked. Content fields
        # (wording, title, risk, etc.) still require Unlock first.
        protected = any(key not in _LOCK_BYPASS_FIELDS for key in vals)
        if protected:
            self._check_clause_edit_permission()
        elif 'editability' in vals and not self.env.su:
            is_legal_admin = self.env.user.has_group('clm.group_clm_legal_admin')
            unlocking = any(
                clause.editability == 'locked'
                and vals.get('editability') != 'locked'
                for clause in self
            )
            if unlocking and not is_legal_admin:
                raise AccessError("Only Legal Administrators can unlock clauses.")
        if 'standard_wording' in vals or 'alternate_wording' in vals or 'name' in vals:
            vals = dict(vals, last_updated=fields.Datetime.now())
        return super().write(vals)

    def unlink(self):
        self._check_clause_edit_permission()
        return super().unlink()

    # -------------------------------------------------------------------------
    # Lifecycle actions
    # -------------------------------------------------------------------------
    def action_submit_review(self):
        for clause in self:
            if clause.state != 'draft':
                raise UserError("Only draft clauses can be submitted for review.")
            clause.write({'state': 'under_review'})

    def action_approve(self):
        is_legal_admin = self.env.user.has_group('clm.group_clm_legal_admin')
        for clause in self:
            if clause.state != 'under_review':
                raise UserError("Only clauses under review can be approved.")
            if not is_legal_admin and not self.env.su:
                raise AccessError("Only Legal Administrators can approve clauses.")
            clause.write({
                'state': 'approved',
                'approved_by_id': self.env.uid,
                'approval_date': fields.Date.today(),
                'version': clause.version + 1,
            })

    def action_publish(self):
        is_legal_admin = self.env.user.has_group('clm.group_clm_legal_admin')
        for clause in self:
            if clause.state != 'approved':
                raise UserError("Only approved clauses can be published.")
            if not is_legal_admin and not self.env.su:
                raise AccessError("Only Legal Administrators can publish clauses.")
            clause.write({'state': 'published'})

    def action_retire(self):
        is_legal_admin = self.env.user.has_group('clm.group_clm_legal_admin')
        for clause in self:
            if clause.state not in ('published', 'superseded'):
                raise UserError("Only published or superseded clauses can be retired.")
            if not is_legal_admin and not self.env.su:
                raise AccessError("Only Legal Administrators can retire clauses.")
            clause.write({'state': 'retired'})

    def action_supersede(self):
        is_legal_admin = self.env.user.has_group('clm.group_clm_legal_admin')
        for clause in self:
            if clause.state != 'published':
                raise UserError("Only published clauses can be superseded.")
            if not is_legal_admin and not self.env.su:
                raise AccessError("Only Legal Administrators can supersede clauses.")
            clause.write({'state': 'superseded'})

    # -------------------------------------------------------------------------
    # Unlock / re-lock (PRD §32 — every unlock must generate an audit event)
    # -------------------------------------------------------------------------
    def action_unlock(self):
        is_legal_admin = self.env.user.has_group('clm.group_clm_legal_admin')
        for clause in self:
            if not is_legal_admin and not self.env.su:
                raise AccessError("Only Legal Administrators can unlock clauses.")
            if clause.editability != 'locked':
                continue
            clause.write({'editability': 'editable'})
            clause.message_post(
                body=(
                    f"⚠️ Clause unlocked by {self.env.user.name}. "
                    "This action has been recorded for audit."
                )
            )
        return True

    def action_lock(self):
        for clause in self:
            if clause.editability == 'locked':
                continue
            clause.write({'editability': 'locked'})
            clause.message_post(
                body=f"🔒 Clause locked by {self.env.user.name}."
            )
        return True
