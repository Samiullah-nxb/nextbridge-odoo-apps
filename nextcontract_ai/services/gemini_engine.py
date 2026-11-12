# Part of NextContract. See LICENSE file for full copyright and licensing details.

import logging
import time

from odoo.exceptions import UserError
from .llm_engine import (  # noqa: F401  (re-exported for existing importers)
    ClauseAnalysisResult,
    ConnectionProbeResult,
    InvalidProviderResponse,
    MIN_REQUEST_SECONDS,
    ProviderOverloaded,
    PYTHON_DOCX_AVAILABLE,
    StructuredLLMEngine,
    UnreadableDocument,
)

_logger = logging.getLogger(__name__)

# Total attempts for a request the provider refused as overloaded, and the
# base delay between them (doubling each time: 4s, then 8s). Kept small --
# extraction runs inside a cron worker that has other jobs waiting, so a
# document that cannot get through in three tries is better left to the
# next run than sat on.
_RETRY_ATTEMPTS = 3
_RETRY_BACKOFF_SECONDS = 4

try:
    from google import genai
    from pydantic import BaseModel, Field  # noqa: F401
    GOOGLE_GENAI_AVAILABLE = True
except ImportError:
    GOOGLE_GENAI_AVAILABLE = False


class GeminiAIEngine(StructuredLLMEngine):
    """
    Production AI Engine using Google Gemini via google-genai SDK.
    Uses Structured Outputs for reliable parsing.
    """

    provider_label = 'Gemini'

    def __init__(self, env, provider=None, model_name=None):
        """Build against a configured provider record, or the legacy settings.

        Without a record the engine reads the original single-key settings
        (nextcontract_ai.gemini_api_key / nextcontract_ai.gemini_model), so deployments that
        predate configurable providers keep working untouched.
        """
        self.env = env
        if not GOOGLE_GENAI_AVAILABLE:
            raise UserError("The 'google-genai' and 'pydantic' Python libraries are required for Gemini AI.")

        params = self.env['ir.config_parameter'].sudo()
        if provider:
            self.api_key = provider.sudo().api_key
            self.model_name = model_name or provider.sudo().default_model_name
            self.fallback_model_name = provider.sudo().fallback_model_id.name or ''
        else:
            self.api_key = params.get_param('nextcontract_ai.gemini_api_key')
            self.model_name = model_name or params.get_param('nextcontract_ai.gemini_model', 'gemini-3.7-flash')
        if not self.api_key:
            raise UserError("Gemini API Key is not configured. Please configure it in Settings → NextContract AI Configuration.")
        if not self.model_name:
            raise UserError("No Gemini model is selected for this provider.")
        self.client = genai.Client(api_key=self.api_key)

    def _choose_transport(self, payload):
        from .transport import choose_transport
        return choose_transport(self.env, payload)

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
            # Every attempt is capped to what is left of the job's time, so
            # a provider that hangs cannot carry the worker past Odoo's limit.
            remaining = self._require_time()
            try:
                return self.client.models.generate_content(
                    model=self.model_name,
                    contents=contents,
                    config=genai.types.GenerateContentConfig(
                        response_mime_type='application/json',
                        response_schema=response_schema,
                        temperature=0.0,
                        http_options=genai.types.HttpOptions(
                            timeout=int(remaining * 1000)),
                    ),
                )
            except Exception as error:
                last_error = error
                if getattr(error, 'code', None) != 503:
                    break
                if attempt + 1 < _RETRY_ATTEMPTS:
                    delay = _RETRY_BACKOFF_SECONDS * (2 ** attempt)
                    if self._time_left() < delay + MIN_REQUEST_SECONDS:
                        break
                    _logger.warning(
                        "Gemini model %s overloaded (attempt %d/%d), retrying "
                        "in %ds", self.model_name, attempt + 1,
                        _RETRY_ATTEMPTS, delay,
                    )
                    time.sleep(delay)

        raise self._translate_provider_error(last_error) from last_error

    def list_models(self):
        """Model ids this key can call generateContent on."""
        try:
            return sorted(
                model.name.split('/', 1)[-1]
                for model in self.client.models.list()
                if 'generateContent' in (getattr(model, 'supported_actions', None) or ['generateContent'])
            )
        except Exception as error:
            raise self._translate_provider_error(error) from error

    def _usage_counts(self, response):
        usage = getattr(response, 'usage_metadata', None)
        return (
            getattr(usage, 'prompt_token_count', 0) or 0,
            getattr(usage, 'candidates_token_count', 0) or 0,
        )

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
            return ProviderOverloaded(
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

    def _build_contents(self, payload, transport):
        """Assemble request contents. Returns (contents, uploaded_files)."""
        instruction = self.EXTRACTION_INSTRUCTION

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

    def _upload_pages(self, payload):
        """Upload every page image to the provider's file store."""
        uploaded = []
        import io
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
