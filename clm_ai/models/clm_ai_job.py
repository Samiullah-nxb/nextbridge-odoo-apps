# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models, fields, api
from odoo.exceptions import UserError
import logging
from ..services.schema_builder import FieldSpec

_logger = logging.getLogger(__name__)

# The allowlisted columns on clm.contract that AI may populate. Mirrors
# clm.ai.field.suggestion.ALLOWED_AI_FIELDS; the hints here are what the
# model is told about each one.
CORE_FIELD_SPECS = (
    FieldSpec(code='name', label='Contract Title', value_type='char',
              ai_hint='The formal title of the agreement as written on its first page.',
              is_core=True),
    FieldSpec(code='effective_date', label='Effective Date', value_type='date',
              ai_hint='The date the agreement takes effect, which may differ from the signature date.',
              is_core=True),
    FieldSpec(code='expiry_date', label='Expiry Date', value_type='date',
              ai_hint='The date the agreement ends. Derive it from the stated term if no explicit date is given.',
              is_core=True),
    FieldSpec(code='contract_value', label='Contract Value', value_type='monetary',
              ai_hint='The total consideration payable under the agreement, as a number without currency symbols.',
              is_core=True),
    FieldSpec(code='partner_id', label='Counterparty', value_type='char',
              ai_hint='The legal name of the other party, excluding our own entity.',
              is_core=True),
    FieldSpec(code='currency_id', label='Currency', value_type='char',
              ai_hint='The three-letter ISO currency code of the contract value, e.g. USD.',
              is_core=True),
    FieldSpec(code='contract_type_id', label='Contract Type', value_type='char',
              ai_hint='The kind of agreement, e.g. NDA, Master Services Agreement.',
              is_core=True),
)

class ClmAiJob(models.Model):
    _name = 'clm.ai.job'
    _description = 'AI Analysis Job'
    _order = 'create_date desc'
    _rec_name = 'name'

    name = fields.Char(string='Job Reference', compute='_compute_name', store=True)
    contract_version_id = fields.Many2one(
        'clm.contract.version', string='Source Version',
        required=True, ondelete='cascade', index=True
    )
    contract_id = fields.Many2one(
        'clm.contract', related='contract_version_id.contract_id',
        store=True, index=True
    )
    
    analysis_type = fields.Selection([
        ('field_extraction', 'Field Extraction'),
        ('clause_analysis', 'Clause Analysis'),
        ('full', 'Full Analysis (Fields + Clauses)')
    ], string='Analysis Type', default='full', required=True)
    
    provider = fields.Selection([
        ('mock', 'Mock (Testing)'),
        ('gemini', 'Google Gemini'),
        ('openai', 'OpenAI')
    ], string='AI Provider', default='mock', required=True)
    
    status = fields.Selection([
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('failed', 'Failed')
    ], string='Status', default='pending', required=True)

    # Review state is deliberately separate from execution status: a job can
    # be 'completed' and still awaiting review, and reviewing it does not
    # change the fact that it ran.
    review_state = fields.Selection([
        ('pending_review', 'Pending Review'),
        ('reviewed', 'Reviewed'),
        ('rejected', 'Rejected'),
    ], string='Review State', default='pending_review', required=True, index=True)
    reviewed_by_id = fields.Many2one('res.users', string='Reviewed By', readonly=True)
    reviewed_at = fields.Datetime(string='Reviewed At', readonly=True)

    is_reviewable = fields.Boolean(
        string='Awaiting Review', compute='_compute_is_reviewable', store=True,
        help='A run is reviewable only while it holds pending suggestions. '
             'Superseded runs stay readable for audit but drop out of queues.'
    )

    # ─── Cloud processing audit ──────────────────────────────────────────────
    # The factory enforces consent; these fields record what was in force when
    # this particular extraction ran. Withdrawing consent later must not
    # rewrite them.
    cloud_processing_consent = fields.Boolean(
        string='Cloud Processing Consented', readonly=True)
    consent_company_id = fields.Many2one(
        'res.company', string='Consent Company', readonly=True)
    consent_user_id = fields.Many2one(
        'res.users', string='Consent Given By', readonly=True)
    consent_provider = fields.Char(string='Consent Provider', readonly=True)
    consent_timestamp = fields.Datetime(string='Consent Timestamp', readonly=True)

    # The cost and volume accounting fields (model, transport, input_tokens,
    # output_tokens, estimated_cost, pricing_version, page_count,
    # pages_processed) were added in Task 11, alongside the engine that writes
    # them. Do not redeclare them here.

    failure_reason = fields.Selection([
        ('consent_missing', 'Cloud Consent Missing'),
        ('max_pages_exceeded', 'Document Too Long'),
        ('schema_invalid', 'Invalid Field Configuration'),
        ('unreadable_document', 'Document Unreadable'),
        ('provider_error', 'Provider Error'),
        ('response_invalid', 'Invalid Provider Response'),
    ], string='Failure Reason', readonly=True)

    requested_by_id = fields.Many2one('res.users', string='Requested By', default=lambda self: self.env.user)
    error_message = fields.Text(string='Error')
    
    suggestion_ids = fields.One2many(
        'clm.ai.field.suggestion', 'job_id', string='Field Suggestions'
    )
    deviation_ids = fields.One2many(
        'clm.clause.deviation', 'job_id', string='Clause Deviations'
    )
    field_snapshot_ids = fields.One2many(
        'clm.ai.job.field.snapshot', 'job_id', string='Field Configuration'
    )

    blocker_suggestion_ids = fields.One2many(
        'clm.ai.field.suggestion', compute='_compute_review_tabs',
        string='Needs Attention',
        help='Suggestions in the blocker band, still pending a decision.'
    )
    review_suggestion_ids = fields.One2many(
        'clm.ai.field.suggestion', compute='_compute_review_tabs',
        inverse='_inverse_review_suggestion_ids',
        string='Review',
        help='Suggestions in the review band, still pending a decision.'
    )
    verified_suggestion_ids = fields.One2many(
        'clm.ai.field.suggestion', compute='_compute_review_tabs',
        string='Verified',
        help='Suggestions in the verified band, still pending a decision.'
    )

    # ─── Cost and volume accounting ──────────────────────────────────────────
    model = fields.Char(string='Model', readonly=True)
    transport = fields.Selection([
        ('inline', 'Inline'),
        ('files_api', 'Files API'),
    ], string='Transport', readonly=True)
    input_tokens = fields.Integer(string='Input Tokens', readonly=True)
    output_tokens = fields.Integer(string='Output Tokens', readonly=True)
    estimated_cost = fields.Float(
        string='Estimated AI Cost', readonly=True,
        help='An estimate derived from token counts and configured pricing. '
             'Actual provider billing can differ. Zero when the provider does '
             'not report usage.'
    )
    pricing_version = fields.Char(
        string='Pricing Version', readonly=True,
        help='Which price list produced the estimate, so historical jobs are '
             'not silently re-priced at current rates.'
    )
    page_count = fields.Integer(string='Pages Detected', readonly=True)
    pages_processed = fields.Integer(string='Pages Processed', readonly=True)

    extracted_text_preview = fields.Text(
        string='Extracted Text', related='contract_version_id.extracted_text',
        readonly=True
    )

    blocker_count = fields.Integer(
        string='Blockers', compute='_compute_review_readiness')
    blocker_summary = fields.Char(
        string='Blocker Summary', compute='_compute_review_readiness')
    can_confirm = fields.Boolean(
        string='Can Confirm', compute='_compute_review_readiness')

    @api.depends('suggestion_ids.status', 'suggestion_ids.confidence',
                 'review_state')
    def _compute_review_readiness(self):
        for job in self:
            blockers = job.suggestion_ids.filtered(
                lambda s: s.status in ('pending', 'needs_mapping')
                and s.review_band == 'blocker'
            )
            missing = job._missing_required_definitions()

            job.blocker_count = len(blockers) + len(missing)
            job.can_confirm = (
                job.review_state == 'pending_review' and job.blocker_count == 0
            )

            parts = []
            if blockers:
                parts.append('%d field(s) need attention' % len(blockers))
            if missing:
                parts.append('%d required field(s) missing' % len(missing))
            job.blocker_summary = ', '.join(parts) or 'Ready to confirm'

    @api.depends('suggestion_ids.review_band', 'suggestion_ids.status')
    def _compute_review_tabs(self):
        for job in self:
            job.blocker_suggestion_ids = job.suggestion_ids.filtered(
                lambda s: s.review_band == 'blocker'
                and s.status in ('pending', 'needs_mapping')
            )
            job.review_suggestion_ids = job.suggestion_ids.filtered(
                lambda s: s.review_band == 'review' and s.status == 'pending'
            )
            job.verified_suggestion_ids = job.suggestion_ids.filtered(
                lambda s: s.review_band == 'verified' and s.status == 'pending'
            )

    def _inverse_review_suggestion_ids(self):
        """No-op: editing a row writes to the underlying suggestion record
        directly, not through this field. This inverse exists only so
        Odoo's ORM stops forcing the field readonly=True (a computed
        one2many with no inverse is readonly by default, which silently
        disabled the Review tab's inline suggested_value editing). Mirrors
        Odoo core's own documented pattern for this exact situation --
        see mrp.production.finished_move_line_ids / _inverse_lines."""
        pass

    def _missing_required_definitions(self):
        """Required definitions with neither a suggestion nor an existing value.

        Enforced here rather than as a model constraint so that draft, intake
        and migration flows are never blocked by a field that only matters at
        review time.
        """
        self.ensure_one()
        contract = self.contract_id
        required = self.env['clm.contract.field.definition'].sudo().search([
            ('required', '=', True),
        ]).filtered(lambda d: d.applies_to_type(contract.contract_type_id))

        suggested = self.suggestion_ids.filtered(
            lambda s: s.status != 'rejected').mapped('definition_id')
        existing = self.env['clm.contract.field.value'].sudo().search([
            ('contract_id', '=', contract.id),
        ]).mapped('definition_id')

        return required - suggested - existing

    def action_confirm_review(self):
        """Apply every accepted suggestion and close the review.

        This is the only path by which AI-derived values reach authoritative
        data. Nothing above this line writes to the contract.
        """
        self.ensure_one()
        if not self.env.su and not self.env.user.has_group('clm.group_clm_legal'):
            from odoo.exceptions import AccessError
            raise AccessError("Only Legal Reviewers can confirm an AI review.")
        if self.review_state != 'pending_review':
            raise UserError(
                "This extraction has already been %s. Run a new extraction to "
                "make further changes." % self.review_state.replace('_', ' ')
            )
        if not self.can_confirm:
            raise UserError(
                "This review cannot be confirmed yet: %s." % self.blocker_summary)

        to_apply = self.suggestion_ids.filtered(lambda s: s.status == 'pending')
        try:
            to_apply._apply_to_contract()
        except UserError:
            raise
        except Exception as e:
            raise UserError(
                "Could not apply suggestion %s (field '%s'): %s"
                % (to_apply.name, to_apply.field_name, e)
            ) from e
        to_apply.write({
            'status': 'approved',
            'reviewed_by_id': self.env.user.id,
            'reviewed_at': fields.Datetime.now(),
        })
        self.write({
            'review_state': 'reviewed',
            'reviewed_by_id': self.env.user.id,
            'reviewed_at': fields.Datetime.now(),
        })
        return True

    def action_reject_review(self):
        """Discard the run without writing anything to the contract."""
        self.ensure_one()
        if self.review_state != 'pending_review':
            raise UserError("This extraction has already been closed.")
        self.suggestion_ids.filtered(
            lambda s: s.status in ('pending', 'needs_mapping')
        ).write({'status': 'rejected'})
        self.write({
            'review_state': 'rejected',
            'reviewed_by_id': self.env.user.id,
            'reviewed_at': fields.Datetime.now(),
        })
        return True

    @api.depends('contract_version_id', 'analysis_type', 'create_date')
    def _compute_name(self):
        for job in self:
            date_str = job.create_date.strftime('%Y%m%d%H%M') if job.create_date else 'NEW'
            analysis = (job.analysis_type or 'full').upper()
            version_id = job.contract_version_id.id or 0
            job.name = f"AI-{analysis}-V{version_id}-{date_str}"

    @api.depends('suggestion_ids.status')
    def _compute_is_reviewable(self):
        for job in self:
            job.is_reviewable = any(
                suggestion.status in ('pending', 'needs_mapping')
                for suggestion in job.suggestion_ids
            )

    def action_run_sync(self):
        """Run the job, mapping every failure onto a recorded reason."""
        from ..services.engine_factory import CloudConsentError
        from ..services.schema_builder import SchemaBuildError
        from ..services.transport import MaxPagesExceeded
        from ..services.gemini_engine import (
            InvalidProviderResponse, UnreadableDocument,
        )

        for job in self:
            if job.status not in ('pending', 'failed'):
                continue

            job.write({'status': 'processing', 'failure_reason': False,
                       'error_message': False})
            try:
                job._run_extraction()
                job.status = 'completed'
            except CloudConsentError as error:
                job._record_failure('consent_missing', error)
            except MaxPagesExceeded as error:
                job._record_failure('max_pages_exceeded', error)
            except SchemaBuildError as error:
                job._record_failure('schema_invalid', error)
            except InvalidProviderResponse as error:
                job._record_failure('response_invalid', error)
            except UnreadableDocument as error:
                job._record_failure('unreadable_document', error)
            except Exception as error:
                _logger.exception("AI Job failed: %s", job.name)
                job._record_failure('provider_error', error)

    def _record_failure(self, reason, error):
        self.ensure_one()
        self.write({
            'status': 'failed',
            'failure_reason': reason,
            'error_message': str(error),
        })

    def _run_extraction(self):
        """Build the engine, fetch the matching payload shape, hand it over.

        The orchestrator owns the routing decision. Engines never choose a
        reading strategy and never open attachments; that is what keeps
        mimetype sniffing and PDF parsing from creeping back into them.
        """
        from ..services.engine_factory import build_engine
        from ..services.gemini_engine import UnreadableDocument

        self.ensure_one()
        self._reset_prior_run_artifacts()
        company = self.contract_id.company_id
        engine = build_engine(self.env, self.provider, company)
        self._record_consent_snapshot(engine, company)

        if self.analysis_type in ('clause_analysis',):
            engine.analyze_clauses(self)
            return

        payload = self._read_payload(engine)
        self._persist_text_layer(payload)

        specs = self._resolve_field_specs()
        self._snapshot_field_specs(specs)

        if engine.accepts_documents:
            engine.extract_fields(self, payload=payload)
        else:
            engine.extract_fields(self)

        if self.analysis_type == 'full':
            try:
                engine.analyze_clauses(self)
            except UnreadableDocument:
                _logger.warning(
                    "Clause analysis skipped for job %s: the document "
                    "format isn't supported by clause analysis yet. Field "
                    "extraction results are unaffected.", self.id,
                )

    def _reset_prior_run_artifacts(self):
        """Clear this job's own leftovers from a previous failed attempt.

        action_run_sync allows retrying a 'failed' job in place. Without
        this, a retry's new suggestions and field snapshots would pile up
        alongside the orphaned ones the failed attempt already created.
        Suggestions in a terminal review state (approved/rejected/
        superseded) are left alone: those can only exist after a real
        review pass, which cannot have happened on a job whose status
        never reached 'completed'.
        """
        self.field_snapshot_ids.unlink()
        self.suggestion_ids.filtered(
            lambda s: s.status in ('pending', 'needs_mapping')
        ).unlink()

    def _read_payload(self, engine):
        """Ask DocumentReader for the shape this engine consumes."""
        from odoo.addons.clm.services.document_reader import resolve_reader, DocumentPayload

        document = self.contract_version_id.document_id
        if not document or not document.attachment_id:
            # Mock and text-only engines are exercised without a document.
            if engine.accepts_documents:
                raise UserError(
                    "Version %s has no document attached, so there is nothing "
                    "to extract from." % self.contract_version_id.version_number
                )
            return DocumentPayload(mimetype='text/plain')

        attachment = document.attachment_id
        want_images = engine.accepts_documents
        reader = resolve_reader(self.env, attachment, want_images=want_images)
        return reader.read(attachment, want_images=want_images)

    def _persist_text_layer(self, payload):
        """Retain whatever text the active pipeline already produced.

        Never a second pass purely to manufacture text: transcribing a long
        contract costs output tokens that most deployments do not need.
        """
        self.ensure_one()
        if not payload.page_count and not payload.text:
            return
        self.contract_version_id.sudo().write({
            'extracted_text': payload.text or False,
            'text_source': payload.text_source,
            'text_layer_status': 'available' if payload.text else 'unavailable',
            'page_count': payload.page_count,
        })

    def _record_consent_snapshot(self, engine, company):
        """Freeze the consent position that authorised this run."""
        self.ensure_one()
        if not engine.is_cloud:
            self.cloud_processing_consent = False
            return
        self.write({
            'cloud_processing_consent': True,
            'consent_company_id': company.id,
            'consent_user_id': company.sudo().clm_ai_cloud_consent_user_id.id,
            'consent_provider': self.provider,
            'consent_timestamp': company.sudo().clm_ai_cloud_consent_date,
        })

    def action_rerun_extraction(self):
        """Create a fresh run and retire this one's pending suggestions.

        Re-extraction never mutates an existing job: the earlier run stays
        readable exactly as it was, with lineage pointing at its replacement.
        """
        self.ensure_one()
        new_job = self.create({
            'contract_version_id': self.contract_version_id.id,
            'analysis_type': self.analysis_type,
            'provider': self.provider,
        })
        self.suggestion_ids.supersede(new_job)
        return new_job

    def action_run_async(self):
        """
        Enqueues the job to be picked up by the cron worker.
        """
        self.ensure_one()
        return True

    @api.model
    def _cron_process_pending_jobs(self, limit=50):
        """Claim and process pending jobs one at a time.

        SKIP LOCKED means two workers never claim the same row, and the commit
        per job means one bad scan among two thousand cannot roll back its
        siblings. The bare UPDATE this replaces gave neither guarantee.
        """
        for _ in range(limit):
            self.env.cr.execute("""
                SELECT id FROM clm_ai_job
                WHERE status = 'pending'
                ORDER BY id
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            """)
            row = self.env.cr.fetchone()
            if not row:
                break

            job = self.browse(row[0])
            try:
                job.action_run_sync()
            except Exception:
                _logger.exception("Unhandled error processing AI job %s", job.id)
                self.env.cr.rollback()
                job._record_failure('provider_error', 'Unhandled worker error')
            self.env.cr.commit()

    def _resolve_field_specs(self):
        """Resolve the exact field set this job extracts.

        Drives both the request schema and the review screen, so the two
        cannot drift apart.
        """
        self.ensure_one()
        contract_type = self.contract_id.contract_type_id
        specs = list(CORE_FIELD_SPECS)

        definitions = self.env['clm.contract.field.definition'].sudo().search([
            ('ai_extractable', '=', True),
        ])
        for definition in definitions:
            if not definition.applies_to_type(contract_type):
                continue
            specs.append(FieldSpec(
                code=definition.code,
                label=definition.name,
                value_type=definition.value_type,
                ai_hint=definition.ai_hint or '',
                options=[
                    option.code
                    for option in definition.option_ids.filtered('active')
                ],
                is_core=False,
            ))
        return specs

    def _snapshot_field_specs(self, specs):
        """Freeze the configuration this job ran against."""
        self.ensure_one()
        definitions = self.env['clm.contract.field.definition'].sudo()
        rows = []
        for index, spec in enumerate(specs):
            definition = (
                definitions.search([('code', '=', spec.code)], limit=1)
                if not spec.is_core else definitions.browse()
            )
            rows.append({
                'job_id': self.id,
                'definition_id': definition.id if definition else False,
                'definition_code': spec.code,
                'definition_name': spec.label,
                'value_type': spec.value_type,
                'ai_hint': spec.ai_hint,
                'option_codes': ','.join(spec.options) if spec.options else False,
                'is_core': spec.is_core,
                'sequence': index * 10,
            })
        return self.env['clm.ai.job.field.snapshot'].sudo().create(rows)
