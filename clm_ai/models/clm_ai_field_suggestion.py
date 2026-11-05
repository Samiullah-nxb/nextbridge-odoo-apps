# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models, fields, api
from odoo.exceptions import UserError, ValidationError

class ClmAiFieldSuggestion(models.Model):
    _name = 'clm.ai.field.suggestion'
    _description = 'AI Field Suggestion'
    _order = 'confidence desc, id desc'

    # H-02: Allowlist of fields AI may write to. Security-sensitive fields
    # (state, user_id, confidentiality_level, company_id, etc.) are excluded.
    ALLOWED_AI_FIELDS = frozenset({
        'name', 'effective_date', 'expiry_date', 'contract_value',
        'contract_type_id', 'partner_id', 'currency_id',
    })

    name = fields.Char(string='Suggestion Reference', required=True, copy=False, readonly=True, default='New')
    description = fields.Char(string='Description')
    context_reasoning = fields.Text(string='Context / Reasoning')

    job_id = fields.Many2one(
        'clm.ai.job', string='AI Job',
        required=True, ondelete='cascade', index=True
    )
    contract_id = fields.Many2one(
        'clm.contract', related='job_id.contract_id',
        store=True, index=True
    )
    contract_version_id = fields.Many2one(
        'clm.contract.version', related='job_id.contract_version_id',
        store=True, index=True
    )
    
    field_name = fields.Char(string='Field', required=True)
    suggested_value = fields.Char(string='Suggested Value', required=True)

    target_type = fields.Selection([
        ('core', 'Core Contract Field'),
        ('definition', 'Contract Field Definition'),
    ], string='Target', default='core', required=True,
        help='Core targets write an allowlisted column on clm.contract. '
             'Definition targets write a clm.contract.field.value row.'
    )
    definition_id = fields.Many2one(
        'clm.contract.field.definition', string='Field Definition',
        ondelete='cascade', index=True
    )
    superseded_by_job_id = fields.Many2one(
        'clm.ai.job', string='Superseded By', readonly=True, index=True,
        help='The later extraction run that replaced this suggestion.'
    )
    evidence_source = fields.Selection([
        ('native', 'Verified Against Text Layer'),
        ('ocr', 'Verified Against OCR Text'),
        ('model_reported', 'Reported By Model'),
    ], string='Evidence Source', default='model_reported', readonly=True,
        help='Whether the quoted excerpt was verified against extracted text. '
             'A model can produce an imperfect quotation, so evidence is '
             'reported, not proven, unless verified.'
    )

    confidence = fields.Float(string='Confidence (%)', default=0.0, help='AI confidence score (0-100)')
    source_text = fields.Text(string='Source Text', help='Extracted text evidence')
    source_page = fields.Integer(string='Source Page')
    
    status = fields.Selection([
        ('pending', 'Pending Review'),
        ('needs_mapping', 'Needs Mapping'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('superseded', 'Superseded'),
    ], string='Status', default='pending', required=True)
    
    reviewed_by_id = fields.Many2one('res.users', string='Reviewed By')
    reviewed_at = fields.Datetime(string='Reviewed At')
    review_comment = fields.Text(string='Review Comment')

    resolved_record_id = fields.Integer(string='Resolved Record ID')
    resolved_model = fields.Char(string='Resolved Model')

    review_band = fields.Selection([
        ('blocker', 'Needs Attention'),
        ('review', 'Review'),
        ('verified', 'Verified'),
    ], string='Review Band', compute='_compute_review_band',
        search='_search_review_band', store=False,
        help='Derived at render time from confidence and status. Deliberately '
             'not stored: re-tuning thresholds re-bands the current view '
             'without touching any recorded review outcome.'
    )

    @api.depends('confidence', 'status')
    def _compute_review_band(self):
        config = self.env['ir.config_parameter'].sudo()
        high = float(config.get_param('clm_ai.confidence_high', 95.0))
        low = float(config.get_param('clm_ai.confidence_low', 80.0))
        for suggestion in self:
            if suggestion.status == 'needs_mapping':
                suggestion.review_band = 'blocker'
            elif suggestion.confidence >= high:
                suggestion.review_band = 'verified'
            elif suggestion.confidence >= low:
                suggestion.review_band = 'review'
            else:
                suggestion.review_band = 'blocker'

    def _search_review_band(self, operator, value):
        """Translate a review_band domain into confidence/status terms.

        Reads the same live threshold config _compute_review_band does, so
        a search stays exactly as current as a read -- nothing here is
        cached or persisted.
        """
        if operator == '=':
            bands = {value}
        elif operator == 'in':
            bands = set(value)
        else:
            raise UserError("Unsupported operator %r for review_band search." % operator)

        config = self.env['ir.config_parameter'].sudo()
        high = float(config.get_param('clm_ai.confidence_high', 95.0))
        low = float(config.get_param('clm_ai.confidence_low', 80.0))

        band_domains = {
            'blocker': ['|', ('status', '=', 'needs_mapping'),
                        '&', ('status', '!=', 'needs_mapping'), ('confidence', '<', low)],
            'review': ['&', ('status', '!=', 'needs_mapping'),
                       '&', ('confidence', '>=', low), ('confidence', '<', high)],
            'verified': ['&', ('status', '!=', 'needs_mapping'), ('confidence', '>=', high)],
        }
        domains = [band_domains[band] for band in bands if band in band_domains]
        if not domains:
            return [('id', '=', False)]
        if len(domains) == 1:
            return domains[0]
        result = ['|'] * (len(domains) - 1)
        for domain in domains:
            result.extend(domain)
        return result

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('name') or vals.get('name') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('clm.ai.field.suggestion') or 'New'
        return super().create(vals_list)

    @api.constrains('target_type', 'definition_id', 'field_name')
    def _check_target(self):
        """Exactly one target, and a definition target must be in scope.

        Without the scope check, a suggestion could write a field belonging
        to an entirely different contract type onto this contract.
        """
        for suggestion in self:
            if suggestion.target_type == 'definition':
                if not suggestion.definition_id:
                    raise ValidationError(
                        "Suggestion '%s' targets a field definition but none is set."
                        % suggestion.name
                    )
                contract_type = suggestion.contract_id.contract_type_id
                if not suggestion.definition_id.applies_to_type(contract_type):
                    raise ValidationError(
                        "Field '%s' does not apply to contract type '%s'."
                        % (suggestion.definition_id.name, contract_type.name)
                    )
            elif suggestion.definition_id:
                raise ValidationError(
                    "Suggestion '%s' targets a core field and must not also "
                    "reference a field definition." % suggestion.name
                )

    def _apply_to_contract(self):
        """Write this suggestion's value to its target.

        Split out of action_approve so the contract-level review can apply a
        whole set in one pass without going through per-record approval.
        """
        for suggestion in self:
            if suggestion.target_type == 'definition':
                suggestion._apply_to_definition()
            else:
                suggestion._apply_to_core_field()

    def _apply_to_definition(self):
        self.ensure_one()
        FieldValue = self.env['clm.contract.field.value']
        value = FieldValue.search([
            ('contract_id', '=', self.contract_id.id),
            ('definition_id', '=', self.definition_id.id),
        ], limit=1)
        if not value:
            value = FieldValue.create({
                'contract_id': self.contract_id.id,
                'definition_id': self.definition_id.id,
            })
        value.set_typed_value(self.suggested_value)

    def _apply_to_core_field(self):
        """Unchanged H-02 behaviour: allowlist, then write."""
        self.ensure_one()
        FIELD_MAPPING = {
            'currency': 'currency_id',
            'contract_type': 'contract_type_id',
            'partner': 'partner_id',
        }
        contract = self.contract_id
        mapped_field = FIELD_MAPPING.get(self.field_name, self.field_name)

        if mapped_field not in self.ALLOWED_AI_FIELDS:
            raise UserError(
                "AI is not permitted to modify the field '%s'. Allowed fields: %s"
                % (mapped_field, ', '.join(sorted(self.ALLOWED_AI_FIELDS)))
            )
        if mapped_field not in contract._fields:
            raise UserError(
                "Field '%s' does not exist on contract model." % mapped_field)

        field_def = contract._fields[mapped_field]
        if field_def.type == 'many2one' and self.resolved_record_id:
            contract.write({mapped_field: self.resolved_record_id})
        else:
            contract.write({mapped_field: self.suggested_value})

    def supersede(self, superseding_job):
        """Retire still-pending suggestions in favour of a later run.

        Approved and rejected suggestions are left alone: a decision that has
        already been applied is history, not a pending question.
        """
        pending = self.filtered(lambda s: s.status in ('pending', 'needs_mapping'))
        pending.write({
            'status': 'superseded',
            'superseded_by_job_id': superseding_job.id,
        })
        return pending

    def action_approve(self):
        """
        Approves the suggestion and updates the underlying contract record.
        Only fields in ALLOWED_AI_FIELDS may be written (H-02 security).
        """
        if not self.env.su and not self.env.user.has_group('clm.group_clm_legal'):
            from odoo.exceptions import AccessError
            raise AccessError("Only Legal Reviewers can approve or reject AI suggestions.")

        for suggestion in self:
            if suggestion.status != 'pending':
                continue

            try:
                suggestion._apply_to_contract()
            except UserError:
                raise
            except Exception as e:
                raise UserError("Could not apply suggestion %s: %s" % (suggestion.name, e))

            suggestion.write({
                'status': 'approved',
                'reviewed_by_id': self.env.user.id,
                'reviewed_at': fields.Datetime.now()
            })

    def action_reject(self):
        """Rejects the suggestion without modifying the contract."""
        if not self.env.su and not self.env.user.has_group('clm.group_clm_legal'):
            from odoo.exceptions import AccessError
            raise AccessError("Only Legal Reviewers can approve or reject AI suggestions.")

        self.write({
            'status': 'rejected',
            'reviewed_by_id': self.env.user.id,
            'reviewed_at': fields.Datetime.now()
        })

    def action_open_mapping_wizard(self):
        self.ensure_one()
        return {
            'name': 'Resolve AI Mapping',
            'type': 'ir.actions.act_window',
            'res_model': 'clm.ai.mapping.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_suggestion_id': self.id,
            }
        }
