# Part of NextContract. See LICENSE file for full copyright and licensing details.
"""REST adapters for OpenAI, Anthropic and any OpenAI-compatible server.

These talk to the vendor over plain HTTPS with `requests`, which Odoo already
ships, so enabling them needs no extra Python package on the server. The
orchestration -- schemas, page limits, suggestions -- is inherited from
StructuredLLMEngine; an adapter only builds the request, sends it and reads
the reply.

Contract content leaves the host through _post and nowhere else, and is never
logged: error messages carry the provider's own explanation, not the request.
"""

import base64
import json
import logging
import re
import time
from types import SimpleNamespace

import requests

from odoo.exceptions import UserError
from .llm_engine import (
    MIN_REQUEST_SECONDS, ProviderOverloaded, StructuredLLMEngine,
    UnreadableDocument,
)

_logger = logging.getLogger(__name__)

# Overloaded or briefly unavailable. 429 is deliberately absent: it is as
# often an exhausted quota as a rate limit, and retrying the former only
# delays the message saying so.
_RETRY_STATUSES = frozenset({502, 503, 504, 529})
_RETRY_ATTEMPTS = 3
_RETRY_BACKOFF_SECONDS = 4

# (connect, read). Extracting a long scanned contract is slow, so the read
# timeout is generous; an unreachable host should still fail quickly.
_TIMEOUT = (10, 300)


class ProviderHTTPError(Exception):
    """A non-2xx reply, before it is turned into a message for a reviewer."""

    def __init__(self, status, detail):
        super().__init__('%s: %s' % (status, detail))
        self.status = status
        self.detail = detail


def plain_json_schema(model):
    """Return the model's JSON Schema with every $ref inlined.

    Pydantic emits $defs/$ref for nested models. Providers differ in how
    much of that they accept, and local servers often accept none, so the
    schema is flattened once here rather than debugged per vendor.
    """
    schema = model.model_json_schema()
    definitions = schema.pop('$defs', {})

    def resolve(node):
        if isinstance(node, dict):
            ref = node.get('$ref')
            if ref and ref.startswith('#/$defs/'):
                target = resolve(definitions[ref.rsplit('/', 1)[1]])
                overrides = {k: resolve(v) for k, v in node.items() if k != '$ref'}
                return {**target, **overrides}
            return {k: resolve(v) for k, v in node.items()}
        if isinstance(node, list):
            return [resolve(item) for item in node]
        return node

    return resolve(schema)


def _as_parts(contents):
    """Normalise engine contents to a list of {'type': text|image, ...}."""
    if isinstance(contents, str):
        contents = [contents]
    return [
        {'type': 'text', 'text': item} if isinstance(item, str) else item
        for item in contents
    ]


def _json_from_text(text):
    """Parse a JSON object out of a model reply, tolerating code fences."""
    text = (text or '').strip()
    fenced = re.match(r'^```(?:json)?\s*(.*?)\s*```$', text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    try:
        return json.loads(text)
    except ValueError:
        start, end = text.find('{'), text.rfind('}')
        if start == -1 or end <= start:
            raise
        return json.loads(text[start:end + 1])


class HttpLLMEngine(StructuredLLMEngine):
    """Common request plumbing for the REST adapters."""

    default_base_url = ''
    # Local servers often take a key-less request; hosted vendors never do.
    requires_api_key = True

    def __init__(self, env, provider, model_name=None):
        self.env = env
        record = provider.sudo()
        self.provider_record = record
        self.provider_label = record.name
        self.api_key = record.api_key or ''
        self.base_url = (record.base_url or self.default_base_url).rstrip('/')
        self.model_name = model_name or record.default_model_name
        self.fallback_model_name = record.fallback_model_id.name or ''
        self.accepts_documents = bool(record.supports_vision)
        self.is_cloud = record.sends_data_off_host
        self.max_output_tokens = record.max_output_tokens or 8192
        self.structured_mode = record.structured_mode or 'json_schema'
        self._send_temperature = self._default_send_temperature

        if not self.base_url:
            raise UserError(
                "Provider '%s' has no Base URL. Enter the server's API address "
                "on the provider." % record.name)
        if self.requires_api_key and not self.api_key:
            raise UserError(
                "Provider '%s' has no API key. Enter it under NextContract → "
                "Configuration → AI Providers." % record.name)
        if not self.model_name:
            raise UserError(
                "No model is selected for provider '%s'." % record.name)

    _default_send_temperature = True

    # -- subclass contract --------------------------------------------------

    def _headers(self):
        raise NotImplementedError

    def _call(self, parts, response_schema):
        """Send one request. Return (reply: dict | str, (in_tokens, out_tokens))."""
        raise NotImplementedError

    def list_models(self):
        raise NotImplementedError

    # -- shared plumbing ----------------------------------------------------

    def _build_contents(self, payload, transport):
        instruction = {'type': 'text', 'text': self.EXTRACTION_INSTRUCTION}
        if payload.pages and self.accepts_documents:
            return [instruction] + [
                {'type': 'image', 'mime': page.mimetype, 'data': page.image}
                for page in payload.pages
            ], []
        if not payload.text:
            raise UnreadableDocument(
                "This document has no readable text and no page images could "
                "be produced, so there is nothing to extract from. If %s's "
                "model cannot read images, only documents with a text layer "
                "can be processed." % self.provider_label
            )
        return [instruction, {'type': 'text', 'text': payload.text}], []

    def _post(self, path, body):
        url = self.base_url + path
        for attempt in range(_RETRY_ATTEMPTS):
            # The read timeout is whatever is left of the job's time: a
            # provider that hangs must not carry the worker past Odoo's
            # real-time limit, which restarts the whole server.
            remaining = self._require_time()
            try:
                response = requests.post(
                    url, headers=self._headers(), json=body,
                    timeout=(_TIMEOUT[0], min(_TIMEOUT[1], remaining)))
            except requests.RequestException as error:
                raise self._connection_error(error) from error
            if response.status_code in _RETRY_STATUSES \
                    and attempt + 1 < _RETRY_ATTEMPTS \
                    and self._time_left() > _RETRY_BACKOFF_SECONDS * (2 ** attempt) + MIN_REQUEST_SECONDS:
                delay = _RETRY_BACKOFF_SECONDS * (2 ** attempt)
                _logger.warning(
                    "%s model %s unavailable (HTTP %s, attempt %d/%d), "
                    "retrying in %ds", self.provider_label, self.model_name,
                    response.status_code, attempt + 1, _RETRY_ATTEMPTS, delay)
                time.sleep(delay)
                continue
            break
        return self._read_json(response)

    def _get(self, path):
        try:
            response = requests.get(
                self.base_url + path, headers=self._headers(), timeout=_TIMEOUT)
        except requests.RequestException as error:
            raise self._connection_error(error) from error
        return self._read_json(response)

    def _read_json(self, response):
        if response.status_code >= 400:
            raise ProviderHTTPError(
                response.status_code, self._error_detail(response))
        try:
            return response.json()
        except ValueError as error:
            raise UserError(
                "%s answered with something that is not JSON. Check the Base "
                "URL (%s) points at the API, not a web page."
                % (self.provider_label, self.base_url)) from error

    @staticmethod
    def _error_detail(response):
        try:
            body = response.json()
        except ValueError:
            return (response.text or '')[:500]
        error = body.get('error') if isinstance(body, dict) else None
        if isinstance(error, dict):
            return str(error.get('message') or error)[:500]
        return str(error or body)[:500]

    def _connection_error(self, error):
        _logger.warning(
            "Could not reach %s at %s: %s",
            self.provider_label, self.base_url, type(error).__name__)
        return UserError(
            "Could not reach %s at %s (%s). Check the Base URL, and that the "
            "Odoo server can reach it over the network."
            % (self.provider_label, self.base_url, type(error).__name__))

    def _generate(self, contents, response_schema):
        parts = _as_parts(contents)
        try:
            try:
                reply, usage = self._call(parts, response_schema)
            except ProviderHTTPError as error:
                # Some newer models reject an explicit temperature. Drop it
                # once and carry on rather than failing every job.
                if error.status == 400 and self._send_temperature \
                        and 'temperature' in error.detail.lower():
                    self._send_temperature = False
                    reply, usage = self._call(parts, response_schema)
                else:
                    raise
        except ProviderHTTPError as error:
            raise self._translate_http_error(error) from error
        return SimpleNamespace(
            parsed=self._parse(reply, response_schema), usage=usage)

    def _parse(self, reply, response_schema):
        """Validate the reply against the schema; None when it does not fit."""
        try:
            data = reply if isinstance(reply, dict) else _json_from_text(reply)
            return response_schema.model_validate(data)
        except ValueError as error:
            # Log shapes, never values: a validation message can quote the
            # contract text the model was reading.
            problems = getattr(error, 'errors', None)
            detail = [
                '%s:%s' % ('.'.join(str(p) for p in e.get('loc', ())), e.get('type'))
                for e in problems()[:5]
            ] if callable(problems) else type(error).__name__
            _logger.warning(
                "%s (%s) returned a reply that did not match the schema: %s",
                self.provider_label, self.model_name, detail)
            return None

    def _usage_counts(self, response):
        return response.usage

    def _translate_http_error(self, error):
        _logger.warning(
            "%s request failed (model=%s, HTTP %s)",
            self.provider_label, self.model_name, error.status)
        label, model = self.provider_label, self.model_name
        said = "\n\nProvider said: %s" % error.detail
        if error.status in (401, 403):
            return UserError(
                "%s rejected the API key. Check the key on the provider, and "
                "that it may use model '%s'.%s" % (label, model, said))
        if error.status == 404:
            return UserError(
                "%s does not know model '%s' (or the Base URL is wrong). Use "
                "Fetch Models on the provider to see what this key can call."
                "%s" % (label, model, said))
        if error.status == 429:
            return UserError(
                "%s refused the request for '%s': rate limit or quota "
                "exhausted. Nothing was extracted.%s" % (label, model, said))
        if error.status in _RETRY_STATUSES:
            return ProviderOverloaded(
                "%s model '%s' is temporarily unavailable. Nothing was "
                "extracted — re-run this job, or pick another model.%s"
                % (label, model, said))
        return UserError(
            "The %s request failed (HTTP %s) and nothing was extracted.%s"
            % (label, error.status, said))


class OpenAIEngine(HttpLLMEngine):
    """OpenAI Chat Completions, and any server that speaks the same API."""

    default_base_url = 'https://api.openai.com/v1'
    # OpenAI's reasoning models reject a non-default temperature, so the
    # hosted adapter leaves it alone; the compatible adapter sets it to 0.
    _default_send_temperature = False

    def _headers(self):
        headers = {'Content-Type': 'application/json'}
        if self.api_key:
            headers['Authorization'] = 'Bearer %s' % self.api_key
        return headers

    def _call(self, parts, response_schema):
        schema = plain_json_schema(response_schema)
        content = []
        for part in parts:
            if part['type'] == 'image':
                encoded = base64.b64encode(part['data']).decode('ascii')
                content.append({
                    'type': 'image_url',
                    'image_url': {
                        'url': 'data:%s;base64,%s' % (part['mime'], encoded)},
                })
            else:
                content.append({'type': 'text', 'text': part['text']})

        body = {
            'model': self.model_name,
            'messages': [{'role': 'user', 'content': content}],
        }
        if self._send_temperature:
            body['temperature'] = 0
        if self.structured_mode == 'json_schema':
            body['response_format'] = {
                'type': 'json_schema',
                'json_schema': {
                    'name': re.sub(r'\W', '_', response_schema.__name__)[:64],
                    'schema': schema,
                    'strict': False,
                },
            }
        else:
            if self.structured_mode == 'json_object':
                body['response_format'] = {'type': 'json_object'}
            content.insert(0, {
                'type': 'text',
                'text': 'Reply with a single JSON object that conforms to '
                        'this JSON Schema, and nothing else:\n%s'
                        % json.dumps(schema),
            })

        data = self._post('/chat/completions', body)
        choices = data.get('choices') or [{}]
        message = choices[0].get('message') or {}
        reply = message.get('content') or ''
        if isinstance(reply, list):  # some servers return content blocks
            reply = ''.join(
                block.get('text', '') for block in reply if isinstance(block, dict))
        if choices[0].get('finish_reason') == 'length':
            _logger.warning(
                "%s (%s) stopped at its output limit; the reply is truncated.",
                self.provider_label, self.model_name)
        usage = data.get('usage') or {}
        return reply, (
            usage.get('prompt_tokens', 0), usage.get('completion_tokens', 0))

    def list_models(self):
        try:
            data = self._get('/models')
        except ProviderHTTPError as error:
            raise self._translate_http_error(error) from error
        items = data.get('data') or data.get('models') or []
        return sorted({
            item.get('id') or item.get('name')
            for item in items if isinstance(item, dict)
            if item.get('id') or item.get('name')
        })


class OpenAICompatibleEngine(OpenAIEngine):
    """A self-chosen server (Ollama, vLLM, OpenRouter, a gateway...)."""

    requires_api_key = False
    _default_send_temperature = True


class AnthropicEngine(HttpLLMEngine):
    """Anthropic Messages API, using a forced tool call for structured output."""

    default_base_url = 'https://api.anthropic.com/v1'
    API_VERSION = '2023-06-01'
    TOOL_NAME = 'record_result'

    def _headers(self):
        return {
            'x-api-key': self.api_key,
            'anthropic-version': self.API_VERSION,
            'content-type': 'application/json',
        }

    def _call(self, parts, response_schema):
        content = []
        for part in parts:
            if part['type'] == 'image':
                content.append({
                    'type': 'image',
                    'source': {
                        'type': 'base64',
                        'media_type': part['mime'],
                        'data': base64.b64encode(part['data']).decode('ascii'),
                    },
                })
            else:
                content.append({'type': 'text', 'text': part['text']})

        body = {
            'model': self.model_name,
            'max_tokens': self.max_output_tokens,
            'messages': [{'role': 'user', 'content': content}],
            'tools': [{
                'name': self.TOOL_NAME,
                'description': 'Record the structured result.',
                'input_schema': plain_json_schema(response_schema),
            }],
            'tool_choice': {'type': 'tool', 'name': self.TOOL_NAME},
        }
        if self._send_temperature:
            body['temperature'] = 0

        data = self._post('/messages', body)
        reply = ''
        for block in data.get('content') or []:
            if block.get('type') == 'tool_use':
                reply = block.get('input') or ''
                break
        if data.get('stop_reason') == 'max_tokens':
            _logger.warning(
                "%s (%s) stopped at its output limit; raise Max Output Tokens "
                "on the provider.", self.provider_label, self.model_name)
        usage = data.get('usage') or {}
        return reply, (
            usage.get('input_tokens', 0), usage.get('output_tokens', 0))

    def list_models(self):
        try:
            data = self._get('/models?limit=1000')
        except ProviderHTTPError as error:
            raise self._translate_http_error(error) from error
        return sorted(
            item['id'] for item in data.get('data') or [] if item.get('id'))
