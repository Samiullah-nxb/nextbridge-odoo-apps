# Part of NextContract. See LICENSE file for full copyright and licensing details.

import base64
import io
import logging
import time
from typing import List, Optional

from odoo.exceptions import UserError
from .ai_engine import AIEngine

_logger = logging.getLogger(__name__)

# Total attempts for a request the provider refused as overloaded, and the
# base delay between them (doubling each time: 4s, then 8s). Kept small --
# extraction runs inside a cron worker that has other jobs waiting, so a
# document that cannot get through in three tries is better left to the
# next run than sat on.
_RETRY_ATTEMPTS = 3
_RETRY_BACKOFF_SECONDS = 4


class InvalidProviderResponse(UserError):
    """The provider replied, but not in the shape the schema demanded."""


class UnreadableDocument(UserError):
    """Neither text nor page images could be obtained from the document."""


try:
    from google import genai
    from pydantic import BaseModel, Field
    GOOGLE_GENAI_AVAILABLE = True
except ImportError:
    GOOGLE_GENAI_AVAILABLE = False
    
try:
    import docx
    PYTHON_DOCX_AVAILABLE = True
except ImportError:
    PYTHON_DOCX_AVAILABLE = False


# Structured Output Schemas for Field Extraction (Tier 1 MVP)
if GOOGLE_GENAI_AVAILABLE:
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


class GeminiAIEngine(AIEngine):
    """
    Production AI Engine using Google Gemini via google-genai SDK.
    Uses Structured Outputs for reliable parsing.
    """

    # Gemini reads scanned pages directly; no OCR step is required.
    accepts_documents = True
    # Transmits contract content off-host, so construction is consent-gated.
    is_cloud = True

    def __init__(self, env):
        self.env = env
        if not GOOGLE_GENAI_AVAILABLE:
            raise UserError("The 'google-genai' and 'pydantic' Python libraries are required for Gemini AI.")
            
        self.api_key = self.env['ir.config_parameter'].sudo().get_param('clm_ai.gemini_api_key')
        if not self.api_key:
            raise UserError("Gemini API Key is not configured. Please configure it in Settings → NextContract AI Configuration.")
            
        # Keep this fallback in step with the default on
        # res.config.settings.clm_ai_gemini_model; a name Google has retired
        # fails every job with a 404.
        self.model_name = self.env['ir.config_parameter'].sudo().get_param('clm_ai.gemini_model', 'gemini-3.7-flash')
        self.client = genai.Client(api_key=self.api_key)

    def _generate(self, contents, response_schema):
        """The one place this engine talks to the network.

        Tests override this to return a parsed object, which keeps the
        orchestration, schema, and suggestion-creation paths under test
        without an API key or a live call.

        Retries a 503 because Gemini returns one routinely under load, and a
        refused request is not a billed one -- the retry costs nothing but
        the wait. Only 503 is retried: a 404 (retired model), 429 (exhausted
        quota) and 401/403 (bad key) all need a human to change something,
        and hammering them just delays the message saying so.
        """
        last_error = None
        for attempt in range(_RETRY_ATTEMPTS):
            try:
                return self.client.models.generate_content(
                    model=self.model_name,
                    contents=contents,
                    config=genai.types.GenerateContentConfig(
                        response_mime_type='application/json',
                        response_schema=response_schema,
                        temperature=0.0,
                    ),
                )
            except Exception as error:
                last_error = error
                if getattr(error, 'code', None) != 503:
                    break
                if attempt + 1 < _RETRY_ATTEMPTS:
                    delay = _RETRY_BACKOFF_SECONDS * (2 ** attempt)
                    _logger.warning(
                        "Gemini model %s overloaded (attempt %d/%d), retrying "
                        "in %ds", self.model_name, attempt + 1,
                        _RETRY_ATTEMPTS, delay,
                    )
                    time.sleep(delay)

        raise self._translate_provider_error(last_error) from last_error

    def _translate_provider_error(self, error):
        """Turn an SDK exception into something a reviewer can act on.

        The raw exception is a JSON blob that lands verbatim in the job's
        Error Log, where it reads as a stack trace rather than as the one
        thing the administrator has to change. The original is still logged
        and chained, so nothing is lost for debugging.
        """
        _logger.exception(
            "Gemini request failed (model=%s)", self.model_name)
        status = getattr(error, 'code', None)
        detail = str(error)

        if status == 404:
            return UserError(
                "The configured Gemini model '%s' is not available to this "
                "API key — Google retires model versions over time.\n\n"
                "Choose a current model in Settings → NextContract AI Configuration → Gemini Model. "
                "The 'Latest' options track Google's current release and keep "
                "working across a retirement.\n\nProvider said: %s"
                % (self.model_name, detail)
            )
        if status == 429:
            return UserError(
                "The Gemini API quota for this key is exhausted, so '%s' "
                "refused the request. Nothing was extracted.\n\nCheck the "
                "quota and billing on the Google AI Studio key, or select a "
                "lower-cost model in Settings → NextContract AI Configuration.\n\nProvider said: %s"
                % (self.model_name, detail)
            )
        if status == 503:
            return UserError(
                "The Gemini model '%s' is temporarily overloaded and refused "
                "the request. Nothing was extracted — re-run this job to try "
                "again, or select a different model in Settings → NextContract AI Configuration."
                "\n\nProvider said: %s" % (self.model_name, detail)
            )
        if status in (401, 403):
            return UserError(
                "Gemini rejected the configured API key. Check the key in "
                "Settings → NextContract AI Configuration, and that the Generative Language API is "
                "enabled for it.\n\nProvider said: %s" % detail
            )
        return UserError(
            "The Gemini request failed and nothing was extracted.\n\n%s"
            % detail
        )

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
            contract_type = self.env['clm.contract.type'].search([('name', 'ilike', string_value)], limit=1)
            if contract_type:
                resolved_record_id = contract_type.id
                resolved_model = 'clm.contract.type'

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
        from .transport import assert_within_page_limit, choose_transport

        if payload is None:
            from odoo.addons.clm.services.document_reader import resolve_reader
            attachment = job.contract_version_id.document_id.attachment_id
            if not attachment:
                raise UserError(
                    "Version %s has no document attached."
                    % job.contract_version_id.version_number
                )
            payload = resolve_reader(self.env, attachment, want_images=True).read(
                attachment, want_images=True)

        assert_within_page_limit(self.env, payload)

        want_text_layer = bool(
            self.env['ir.config_parameter'].sudo().get_param(
                'clm_ai.generate_text_layer')
        ) and not payload.text

        specs = job._resolve_field_specs()
        schema = build_extraction_schema(
            specs, include_text_layer=want_text_layer)
        transport = choose_transport(self.env, payload)

        uploaded_files = []
        try:
            contents, uploaded_files = self._build_contents(payload, transport)
            response = self._generate(contents, schema)
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

    def _build_contents(self, payload, transport):
        """Assemble request contents. Returns (contents, uploaded_files)."""
        instruction = (
            "You are a contract data extraction assistant. Extract the fields "
            "described by the response schema from the document provided. "
            "Use only what the document states; omit any field you cannot find. "
            "For every field you return, cite the page and the smallest "
            "supporting excerpt."
        )

        if payload.pages:
            if transport == 'files_api':
                uploaded_files = self._upload_pages(payload)
                return [instruction] + uploaded_files, uploaded_files
            parts = [
                genai.types.Part.from_bytes(data=page.image, mime_type=page.mimetype)
                for page in payload.pages
            ]
            return [instruction] + parts, []

        if not payload.text:
            raise UnreadableDocument(
                "This document has no readable text and no page images could "
                "be produced, so there is nothing to extract from."
            )
        return [instruction, payload.text], []

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

    def _upload_pages(self, payload):
        """Upload every page image to the provider's file store."""
        import io
        uploaded = []
        for page in payload.pages:
            buffer = io.BytesIO(page.image)
            uploaded.append(self.client.files.upload(
                file=buffer,
                config={'mime_type': page.mimetype},
            ))
        return uploaded

    def _delete_remote_file(self, uploaded_file):
        """Remove the provider-side copy.

        Uploading a confidential contract to a third-party file store is a
        data-residency decision, not an implementation detail: no remote
        object outlives the job that created it. Best-effort against the
        provider API, and a failure is logged rather than swallowed.
        """
        try:
            self.client.files.delete(name=uploaded_file.name)
            _logger.info("Deleted provider-side file %s", uploaded_file.name)
        except Exception:
            _logger.exception(
                "Could not delete provider-side file %s. A copy of contract "
                "content may still exist with the provider.",
                getattr(uploaded_file, 'name', 'unknown'),
            )

    def _record_usage(self, job, response, transport, payload):
        """Persist token counts and page accounting for cost visibility."""
        usage = getattr(response, 'usage_metadata', None)
        job.write({
            'input_tokens': getattr(usage, 'prompt_token_count', 0) or 0,
            'output_tokens': getattr(usage, 'candidates_token_count', 0) or 0,
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
        definitions = self.env['clm.contract.field.definition'].sudo()

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
        # In a full implementation, we would pull all published clauses from clm.clause
        # and provide them in the prompt for semantic matching.
        # For Phase 6B MVP, we provide a simplified prompt instructing the AI to find key deviations.

        # Fetch standard clauses (limit to core categories for prompt size management)
        # L-04: Guard against prompt overflow — cap at 50 clauses
        _MAX_PLAYBOOK_CLAUSES = 50
        standard_clauses = self.env['clm.clause'].search([('state', '=', 'published')], limit=_MAX_PLAYBOOK_CLAUSES)

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
        total_published = self.env['clm.clause'].search_count([('state', '=', 'published')])
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
        response = self._generate(prompt, ClauseAnalysisResult)

        result = getattr(response, 'parsed', None)
        if result is None:
            raise InvalidProviderResponse(
                "The AI provider returned a clause analysis that did not "
                "match the expected schema. No deviations were recorded."
            )

        for dev in result.deviations:
            clause_id = False
            if dev.matched_clause_id:
                matched = self.env['clm.clause'].browse(dev.matched_clause_id)
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
