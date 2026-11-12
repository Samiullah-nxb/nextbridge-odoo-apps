# Part of NextContract. See LICENSE file for full copyright and licensing details.
"""Provider-neutral orchestration shared by every LLM-backed engine.

An engine adapter only has to answer three questions: how are the request
contents assembled, how is one request sent, and where do the token counts
live in the reply. Everything else -- the extraction schema, page limits,
suggestion creation, evidence classification, clause analysis -- is the same
whichever vendor sits behind the adapter, so it lives here once.
"""

import base64
import io
import logging
from typing import List, Optional

from odoo.exceptions import UserError
from .ai_engine import AIEngine

_logger = logging.getLogger(__name__)


class InvalidProviderResponse(UserError):
    """The provider replied, but not in the shape the schema demanded."""


class UnreadableDocument(UserError):
    """Neither text nor page images could be obtained from the document."""


class ProviderOverloaded(UserError):
    """The provider refused the request as temporarily overloaded.

    The only failure a second model can cure: a bad key, an exhausted quota
    or an unknown model fail identically whichever model is asked.
    """


class JobTimeBudgetExceeded(UserError):
    """The job ran out of the time Odoo allows a background worker.

    Failing the job cleanly is the point: a worker that outlives Odoo's
    real-time limit does not just lose the job, it makes Odoo restart the
    whole server, taking every open page down with it.
    """


# Headroom left under Odoo's limit for database work, commits and the
# watchdog's coarse check interval.
_LIMIT_MARGIN_SECONDS = 30.0
# A request started with less than this left cannot be expected to finish.
MIN_REQUEST_SECONDS = 10.0
# A fallback attempt needs room for at least one full round trip.
MIN_FALLBACK_SECONDS = 25.0
_UNLIMITED_SECONDS = 3600.0


def worker_time_budget():
    """Seconds an AI job may use, derived from Odoo's own worker limit.

    limit_time_real_cron applies to cron workers (-1 means "use
    limit_time_real", 0 means unlimited). Reading it rather than hard-coding
    a number keeps the budget honest when an administrator raises the limit.
    """
    from odoo.tools import config
    limit = config['limit_time_real_cron']
    if limit is None or limit < 0:
        limit = config['limit_time_real']
    if not limit or limit <= 0:
        return _UNLIMITED_SECONDS
    return max(float(limit) - _LIMIT_MARGIN_SECONDS, 30.0)


try:
    from pydantic import BaseModel, Field
    PYDANTIC_AVAILABLE = True
except ImportError:
    PYDANTIC_AVAILABLE = False

try:
    import docx
    PYTHON_DOCX_AVAILABLE = True
except ImportError:
    PYTHON_DOCX_AVAILABLE = False


if PYDANTIC_AVAILABLE:
    # Structured Output Schemas for Field Extraction (Tier 1 MVP)
    class ContractFieldSuggestion(BaseModel):
        field_name: str = Field(description="The internal code name of the field (e.g. name, effective_date, expiry_date, contract_value, currency).")
        suggested_value: str = Field(description="The extracted value formatted clearly. Dates MUST be YYYY-MM-DD.")
        confidence_score: float = Field(description="Confidence score from 0.0 to 100.0")
        source_text: str = Field(description="The exact text snippet from the contract providing this value.")

    class ContractFieldExtractionResult(BaseModel):
        fields: List[ContractFieldSuggestion]

    # Structured Output Schemas for Clause Analysis
    class ClauseDeviationSuggestion(BaseModel):
        matched_text: str = Field(description="The text from the inbound contract that corresponds to this clause concept.")
        matched_clause_id: Optional[int] = Field(description="If a match was found in the library, the ID of the standard clause it was matched against.")
        deviation_type: str = Field(description="One of: 'standard', 'approved_alternative', 'modified', 'non_standard', 'rejected'.")
        risk_level: str = Field(description="One of: 'low', 'medium', 'high', 'critical'.")
        confidence_score: float = Field(description="Confidence score from 0.0 to 100.0")
        explanation: str = Field(description="Why this classification and risk level was chosen.")

    class ClauseAnalysisResult(BaseModel):
        deviations: List[ClauseDeviationSuggestion]

    class ConnectionProbeResult(BaseModel):
        status: str = Field(description="The single word OK.")


class StructuredLLMEngine(AIEngine):
    """Base for engines that ask an LLM for schema-shaped JSON.

    Subclasses must set model_name and implement _build_contents, _generate
    and _usage_counts. _generate returns an object whose .parsed attribute is
    the validated schema instance (or None when the reply did not conform).
    """

    accepts_documents = True
    is_cloud = True
    # The orchestrator hands these engines the payload it already read.
    takes_payload = True

    model_name = ''
    # Tried once when model_name is overloaded. Empty means no fallback.
    fallback_model_name = ''
    provider_label = 'AI provider'

    # -- adapter hooks ------------------------------------------------------

    def _build_contents(self, payload, transport):
        """Return (contents, uploaded_files) for this provider's request."""
        raise NotImplementedError

    def _generate(self, contents, response_schema):
        """The one place an engine talks to the network."""
        raise NotImplementedError

    def _usage_counts(self, response):
        """Return (input_tokens, output_tokens) from a provider response."""
        return 0, 0

    def _time_left(self):
        """Seconds remaining before this job must hand the worker back.

        The deadline comes from the orchestrator (the cron loop shares one
        budget across the jobs it processes); an engine built outside it
        falls back to a fresh budget starting at first use.
        """
        import time
        if getattr(self, '_deadline', None) is None:
            self._deadline = (
                self.env.context.get('nextcontract_ai_deadline')
                or time.monotonic() + worker_time_budget())
        return self._deadline - time.monotonic()

    def _require_time(self, minimum=MIN_REQUEST_SECONDS):
        left = self._time_left()
        if left < minimum:
            raise JobTimeBudgetExceeded(
                "The AI provider did not answer within the time a background "
                "job is allowed (%d seconds), so the run was stopped before "
                "it could disrupt the server. Nothing was extracted — retry "
                "the analysis, or choose a faster or less busy model."
                % worker_time_budget())
        return left

    def _request(self, contents, response_schema):
        """_generate, with one attempt on the fallback model when overloaded.

        _generate has already retried the primary model with backoff, so
        reaching here means it stayed unavailable. On success model_name
        stays on the fallback: the job then records the model that really
        answered, and a later call in the same job (clause analysis after
        extraction) does not wait out the overloaded model again. If the
        fallback fails too, the primary is restored and its error raised.
        """
        self._require_time()
        try:
            return self._generate(contents, response_schema)
        except ProviderOverloaded as primary_error:
            fallback = self.fallback_model_name
            if not fallback or fallback == self.model_name:
                raise
            if self._time_left() < MIN_FALLBACK_SECONDS:
                _logger.warning(
                    "%s model %s is overloaded and only %ds of the job's "
                    "time is left, so the fallback model %s is not tried.",
                    self.provider_label, self.model_name,
                    max(self._time_left(), 0), fallback)
                raise
            primary = self.model_name
            _logger.warning(
                "%s model %s is overloaded; trying fallback model %s.",
                self.provider_label, primary, fallback)
            self.model_name = fallback
            try:
                return self._generate(contents, response_schema)
            except Exception as fallback_error:
                self.model_name = primary
                if isinstance(fallback_error, ProviderOverloaded):
                    raise primary_error from fallback_error
                raise

    def _choose_transport(self, payload):
        """Inline by default; only providers with a file store override it."""
        return 'inline'

    def _delete_remote_file(self, uploaded_file):
        """Providers that upload files override this to clean up."""

    # -- connection probe ---------------------------------------------------

    def probe(self):
        """Send a tiny request that carries no contract content.

        Returns the model's reply as a string. Used by the provider form's
        Test Connection button, so an administrator finds out about a bad
        key, URL or model name before a reviewer does.
        """
        response = self._generate(
            'Reply with a JSON object whose "status" field is the word OK.',
            ConnectionProbeResult,
        )
        parsed = getattr(response, 'parsed', None)
        if parsed is None:
            raise InvalidProviderResponse(
                "The provider answered, but not with the expected JSON. The "
                "model may not support structured output.")
        return parsed.status

    # -- shared orchestration -----------------------------------------------

    def _extract_text_from_version(self, version):
        # P3-OCR: Prefer pre-extracted text (from OCR or prior extraction)
        if hasattr(version, 'extracted_text') and version.extracted_text:
            return version.extracted_text

        if not PYTHON_DOCX_AVAILABLE:
            raise UserError("The 'python-docx' library is required to extract text from DOCX.")

        if not version.document_id or not version.document_id.attachment_id:
            raise UserError(f"Version {version.version_number} has no document attached.")

        attachment = version.document_id.attachment_id
        if 'wordprocessingml.document' not in (attachment.mimetype or ''):
            # Fallback to plain text if it was somehow saved as text
            if 'text/plain' in attachment.mimetype:
                return base64.b64decode(attachment.datas).decode('utf-8')
            raise UnreadableDocument("Currently, only DOCX or plain text files are supported for AI extraction.")

        docx_bytes = base64.b64decode(attachment.datas)
        doc = docx.Document(io.BytesIO(docx_bytes))
        return "\n".join([p.text for p in doc.paragraphs if p.text.strip()])

    def _resolve_field_value(self, field_name, string_value):
        resolved_record_id = False
        resolved_model = False

        if field_name == 'partner_id' and string_value:
            partner = self.env['res.partner'].search([('name', 'ilike', string_value)], limit=1)
            if partner:
                resolved_record_id = partner.id
                resolved_model = 'res.partner'

        elif field_name == 'currency_id' and string_value:
            currency = self.env['res.currency'].search([('name', '=ilike', string_value)], limit=1)
            if currency:
                resolved_record_id = currency.id
                resolved_model = 'res.currency'

        elif field_name == 'contract_type_id' and string_value:
            contract_type = self.env['nextcontract.contract.type'].search([('name', 'ilike', string_value)], limit=1)
            if contract_type:
                resolved_record_id = contract_type.id
                resolved_model = 'nextcontract.contract.type'

        return resolved_record_id, resolved_model

    def extract_fields(self, job, payload=None):
        """Extract the job's resolved field set from the supplied payload.

        The payload comes from the orchestrator, which obtained it from
        DocumentReader. This method never inspects mimetypes and never opens
        attachments — that responsibility belongs to DocumentReader alone.
        When no payload is supplied (the pre-orchestration call path), one is
        resolved here from the version's attached document.
        """
        from .schema_builder import build_extraction_schema
        from .transport import assert_within_page_limit

        if payload is None:
            from odoo.addons.nextcontract.services.document_reader import resolve_reader
            attachment = job.contract_version_id.document_id.attachment_id
            if not attachment:
                raise UserError(
                    "Version %s has no document attached."
                    % job.contract_version_id.version_number
                )
            want_images = self.accepts_documents
            payload = resolve_reader(
                self.env, attachment, want_images=want_images,
            ).read(attachment, want_images=want_images)

        assert_within_page_limit(self.env, payload)

        want_text_layer = bool(
            self.env['ir.config_parameter'].sudo().get_param(
                'nextcontract_ai.generate_text_layer')
        ) and not payload.text

        specs = job._resolve_field_specs()
        schema = build_extraction_schema(
            specs, include_text_layer=want_text_layer)
        transport = self._choose_transport(payload)

        uploaded_files = []
        try:
            contents, uploaded_files = self._build_contents(payload, transport)
            response = self._request(contents, schema)
            parsed = getattr(response, 'parsed', None)
            if parsed is None:
                raise InvalidProviderResponse(
                    "The AI provider returned a response that did not match "
                    "the expected schema. No suggestions were recorded."
                )
            self._record_usage(job, response, transport, payload)
            self._persist_transcription(job, parsed, want_text_layer)
            self._create_suggestions_from_result(job, specs, parsed, payload)
        finally:
            for uploaded_file in uploaded_files:
                self._delete_remote_file(uploaded_file)

    EXTRACTION_INSTRUCTION = (
        "You are a contract data extraction assistant. Extract the fields "
        "described by the response schema from the document provided. "
        "Use only what the document states; omit any field you cannot find. "
        "For every field you return, cite the page and the smallest "
        "supporting excerpt."
    )

    def _persist_transcription(self, job, parsed, want_text_layer):
        """Store the transcription when one was requested.

        It arrived in the same response as the field values, so retaining a
        searchable text layer never costs an extra provider call.
        """
        if not want_text_layer:
            return
        transcription = getattr(parsed, 'document_text', None)
        if not transcription:
            return
        job.contract_version_id.sudo().write({
            'extracted_text': transcription,
            'text_source': 'vlm',
            'text_layer_status': 'available',
        })

    def _record_usage(self, job, response, transport, payload):
        """Persist token counts and page accounting for cost visibility."""
        input_tokens, output_tokens = self._usage_counts(response)
        job.write({
            'input_tokens': input_tokens or 0,
            'output_tokens': output_tokens or 0,
            'model': self.model_name,
            'transport': transport,
            'page_count': payload.page_count,
            'pages_processed': payload.page_count,
        })

    def _create_suggestions_from_result(self, job, specs, parsed, payload):
        """Turn the validated response into staged suggestions.

        Nothing is written to the contract here. Every value lands as a
        suggestion awaiting contract-level review.
        """
        specs_by_code = {spec.code: spec for spec in specs}
        definitions = self.env['nextcontract.contract.field.definition'].sudo()

        for code, spec in specs_by_code.items():
            extracted = getattr(parsed, code, None)
            if extracted is None:
                continue

            raw_value = extracted.value
            string_value = (
                raw_value.isoformat() if hasattr(raw_value, 'isoformat')
                else str(raw_value)
            )

            values = {
                'field_name': code,
                'suggested_value': string_value,
                'confidence': extracted.confidence,
                'source_text': extracted.source_text,
                'source_page': extracted.source_page,
                'evidence_source': self._classify_evidence(extracted, payload),
                'status': 'pending',
            }

            if spec.is_core:
                values['target_type'] = 'core'
                resolved_id, resolved_model = self._resolve_field_value(
                    code, string_value)
                values['resolved_record_id'] = resolved_id
                values['resolved_model'] = resolved_model
                if code in ('partner_id', 'currency_id', 'contract_type_id') \
                        and not resolved_id:
                    values['status'] = 'needs_mapping'
            else:
                definition = definitions.search([('code', '=', code)], limit=1)
                values['target_type'] = 'definition'
                values['definition_id'] = definition.id

            self._create_suggestion(job, values)

    @staticmethod
    def _classify_evidence(extracted, payload):
        """Verify the quoted excerpt against the text layer where one exists.

        A multimodal model can produce an imperfect quotation, so evidence is
        reported rather than proven unless it can be checked.
        """
        if not payload.text or not extracted.source_text:
            return 'model_reported'
        if extracted.source_text.strip()[:60] in payload.text:
            return 'native' if payload.text_source == 'native' else 'ocr'
        return 'model_reported'

    def analyze_clauses(self, job):
        # In a full implementation, we would pull all published clauses from nextcontract.clause
        # and provide them in the prompt for semantic matching.
        # For Phase 6B MVP, we provide a simplified prompt instructing the AI to find key deviations.

        # Fetch standard clauses (limit to core categories for prompt size management)
        # L-04: Guard against prompt overflow — cap at 50 clauses
        _MAX_PLAYBOOK_CLAUSES = 50
        standard_clauses = self.env['nextcontract.clause'].search([('state', '=', 'published')], limit=_MAX_PLAYBOOK_CLAUSES)

        # Deviation analysis measures a contract against a playbook. With no
        # published clauses there is no baseline to deviate from, so every
        # clause would come back 'non_standard' -- noise a reviewer then has
        # to clear. Skip before the request rather than paying for a call
        # whose result cannot be meaningful. Checked ahead of reading the
        # document so an empty library costs nothing at all.
        if not standard_clauses:
            _logger.warning(
                "Clause analysis skipped for job %s: no published clauses in "
                "the library, so there is no playbook to compare against. "
                "Publish clauses under Library to enable deviation analysis.",
                job.id,
            )
            return

        contract_text = self._extract_text_from_version(job.contract_version_id)
        total_published = self.env['nextcontract.clause'].search_count([('state', '=', 'published')])
        if total_published > _MAX_PLAYBOOK_CLAUSES:
            _logger.warning(
                "Clause playbook truncated: %d published clauses, using first %d. "
                "Consider filtering by contract_type_id for better relevance.",
                total_published, _MAX_PLAYBOOK_CLAUSES
            )
        clause_playbook = "\n".join([f"ID: {c.id}\nCategory: {c.category}\nStandard Wording: {c.standard_wording}" for c in standard_clauses])

        prompt = f"""
        You are an expert legal AI assistant. Compare the provided contract text against our standard clause playbook.
        Identify key deviations, classify them, and assess risk.

        Standard Playbook:
        ---
        {clause_playbook}
        ---

        Rules for Classification:
        - standard: Exactly matches or semantically identical to standard wording.
        - approved_alternative: A minor variation that is legally equivalent.
        - modified: Changes material terms but might be acceptable.
        - non_standard: Significantly different from playbook.
        - rejected: Unacceptable terms (e.g. unlimited liability).

        Rules for Risk:
        - low, medium, high, critical

        Contract Text:
        ---
        {contract_text}
        """

        # Goes through _generate so clause analysis shares the extraction
        # path's error translation, and so a test that stubs _generate
        # covers both. Only the call is guarded: wrapping the loop below
        # too would report an ORM failure as a provider failure.
        response = self._request(prompt, ClauseAnalysisResult)

        result = getattr(response, 'parsed', None)
        if result is None:
            raise InvalidProviderResponse(
                "The AI provider returned a clause analysis that did not "
                "match the expected schema. No deviations were recorded."
            )

        for dev in result.deviations:
            clause_id = False
            if dev.matched_clause_id:
                matched = self.env['nextcontract.clause'].browse(dev.matched_clause_id)
                if matched.exists():
                    clause_id = matched.id

            self._create_deviation(job, {
                'clause_id': clause_id,
                'matched_text': dev.matched_text,
                'ai_deviation_type': dev.deviation_type,
                'ai_risk_level': dev.risk_level,
                'ai_confidence': dev.confidence_score,
                'explanation': dev.explanation,
                'status': 'pending',
            })
