# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models, fields, api
from odoo.exceptions import ValidationError

class ClmRiskAssessment(models.Model):
    _name = 'clm.risk.assessment'
    _description = 'Contract Risk Assessment'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _rec_name = 'name'

    contract_id = fields.Many2one('clm.contract', required=True, ondelete='cascade')
    name = fields.Char(related='contract_id.name', string='Contract Name')
    
    # Calculated Risk
    calculated_score = fields.Integer('Calculated Score', readonly=True, tracking=True)
    calculated_rating = fields.Selection([
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
        ('critical', 'Critical')
    ], string='Calculated Rating', readonly=True, tracking=True)
    
    # Override Risk
    override_score = fields.Integer('Override Score', tracking=True)
    override_rating = fields.Selection([
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
        ('critical', 'Critical')
    ], string='Override Rating', tracking=True)
    
    override_reason = fields.Text('Override Reason', tracking=True)
    override_by_id = fields.Many2one('res.users', string='Overridden By', readonly=True, tracking=True)
    override_date = fields.Datetime('Override Date', readonly=True)
    
    # Final Risk (Computed)
    final_score = fields.Integer('Final Score', compute='_compute_final_risk', store=True)
    final_rating = fields.Selection([
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
        ('critical', 'Critical')
    ], string='Final Rating', compute='_compute_final_risk', store=True)
    
    # Risk Items (The specific factors triggered on this contract)
    item_ids = fields.One2many('clm.risk.assessment.item', 'assessment_id', string='Risk Factors')

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if not res.get('contract_id'):
            contract_id = self.env.context.get('default_contract_id')
            if not contract_id and self.env.context.get('active_model') == 'clm.contract':
                contract_id = self.env.context.get('active_id')
            if contract_id:
                res['contract_id'] = contract_id
        return res

    def _resolve_contract(self):
        self.ensure_one()
        if self.contract_id:
            return self.contract_id
        contract_id = self.env.context.get('default_contract_id')
        if not contract_id and self.env.context.get('active_model') == 'clm.contract':
            contract_id = self.env.context.get('active_id')
        if contract_id:
            self.contract_id = contract_id
        return self.contract_id

    def _ensure_persisted(self):
        """Persist unsaved assessments opened from the contract form."""
        self.ensure_one()
        if self.id:
            return self
        item_commands = [
            (0, 0, {
                'factor_id': item.factor_id.id,
                'evidence': item.evidence,
                'comments': item.comments,
            })
            for item in self.item_ids
            if item.factor_id
        ]
        return self.create({
            'contract_id': self.contract_id.id,
            'item_ids': item_commands,
        })

    @api.constrains('override_rating', 'override_reason')
    def _check_override_reason(self):
        for record in self:
            if record.override_rating and not record.override_reason:
                raise ValidationError("An override reason is required when setting an override rating.")

    @api.depends('calculated_score', 'calculated_rating', 'override_score', 'override_rating')
    def _compute_final_risk(self):
        for record in self:
            record.final_score = record.override_score if record.override_rating else record.calculated_score
            record.final_rating = record.override_rating if record.override_rating else record.calculated_rating

    def _add_ai_risk_items(self):
        """Hook for NextContract AI: add one item per confirmed high-risk clause deviation."""
        return

    def action_recalculate(self):
        self.ensure_one()
        if not self._resolve_contract():
            raise ValidationError("This risk assessment must be linked to a contract before recalculating risk.")

        assessment = self._ensure_persisted()
        
        # 1. Clear previously auto-generated items only
        assessment.item_ids.filtered(
            lambda i: (
                i.factor_id.source_type == 'ai_deviation'
                and (i.comments or '').startswith('AI Risk Level:')
            ) or (
                i.factor_id.source_type == 'metadata'
                and (i.comments or '') == 'Contract value exceeds 1,000,000'
            )
        ).unlink()
        
        # 2. AI Deviations (added by NextContract AI when installed)
        assessment._add_ai_risk_items()

        # 3. High Value Contract Metadata
        if assessment.contract_id.contract_value and assessment.contract_id.contract_value > 1000000:
            meta_factor = self.env.ref('clm.risk_factor_high_value', raise_if_not_found=False)
            if meta_factor:
                self.env['clm.risk.assessment.item'].create({
                    'assessment_id': assessment.id,
                    'factor_id': meta_factor.id,
                    'evidence': str(assessment.contract_id.contract_value),
                    'comments': 'Contract value exceeds 1,000,000',
                })

        # 4. Calculate score
        score = sum(assessment.item_ids.mapped('factor_id.weight'))
        rating = 'low'
        if score > 75:
            rating = 'critical'
        elif score > 50:
            rating = 'high'
        elif score > 25:
            rating = 'medium'
            
        assessment.write({
            'calculated_score': score,
            'calculated_rating': rating,
        })
        return True

class ClmRiskAssessmentItem(models.Model):
    _name = 'clm.risk.assessment.item'
    _description = 'Contract Risk Factor Instance'

    assessment_id = fields.Many2one('clm.risk.assessment', ondelete='cascade')
    factor_id = fields.Many2one('clm.risk.factor', required=True)
    
    source_type = fields.Selection(related='factor_id.source_type')
    weight = fields.Integer(related='factor_id.weight')
    
    evidence = fields.Char('Evidence / Matched Text')
    comments = fields.Text('Comments')
