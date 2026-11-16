# Part of NextContract. See LICENSE file for full copyright and licensing details.
"""Engine construction and the cloud-egress consent boundary.

Every AI engine in the system is built here. That is deliberate: the consent
check below is the only thing standing between a confidential contract and a
third-party API, and a check that can be routed around is not a check.
Constructing a cloud engine without company consent raises before any
network client is created.
"""

import logging

from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class CloudConsentError(UserError):
    """Raised when cloud processing is attempted without company consent."""


def build_engine(env, provider, company, provider_record=None,
                 model_name=None, contract_content=True):
    """Return an AIEngine for provider, enforcing consent for cloud engines.

    Args:
        env: Odoo environment.
        provider: a provider type: mock, gemini, openai, anthropic, custom.
        company: the res.company that owns the contract being processed.
            Not the acting user's company — a Legal user working across
            companies must not carry one company's approval into another's
            contract.
        provider_record: the nextcontract.ai.provider holding the key, URL and model
            list. Without one, only mock and the legacy single-key Gemini
            settings are available.
        model_name: overrides the provider's default model.
        contract_content: False only for the connection test, which sends a
            fixed one-line prompt and no contract data, so there is nothing
            for consent to protect. Every path that reads a contract keeps
            the default.
    """
    engine_class = _resolve_engine_class(provider)

    if contract_content and _sends_data_off_host(engine_class, provider_record):
        _assert_cloud_consent(
            company,
            provider_record.name if provider_record else provider)

    if provider == 'mock':
        return engine_class(env)
    if provider_record:
        return engine_class(env, provider_record, model_name)
    if provider == 'gemini':
        return engine_class(env, None, model_name)
    raise UserError(
        "AI provider '%s' needs a configured provider. Add one under "
        "NextContract → Configuration → AI Providers." % provider
    )


def _sends_data_off_host(engine_class, provider_record):
    """Whether content leaves the host, judged before any client exists."""
    if provider_record:
        return bool(provider_record.sudo().sends_data_off_host)
    return engine_class.is_cloud


def _resolve_engine_class(provider):
    if provider == 'mock':
        from .ai_engine import MockAIEngine
        return MockAIEngine
    if provider == 'gemini':
        from .gemini_engine import GeminiAIEngine
        return GeminiAIEngine
    if provider == 'openai':
        from .http_engines import OpenAIEngine
        return OpenAIEngine
    if provider == 'custom':
        from .http_engines import OpenAICompatibleEngine
        return OpenAICompatibleEngine
    if provider == 'anthropic':
        from .http_engines import AnthropicEngine
        return AnthropicEngine
    raise UserError(
        "AI provider '%s' is not implemented." % provider
    )


def _assert_cloud_consent(company, provider):
    if not company:
        raise CloudConsentError(
            "Cannot determine which company owns this contract, so cloud AI "
            "processing cannot be authorised."
        )
    if not company.sudo().nextcontract_ai_cloud_processing_acknowledged:
        raise CloudConsentError(
            "Cloud AI processing is not enabled for %s.\n\n"
            "Contract content would be transmitted to %s. Enable it in "
            "Settings → NextContract → Cloud Processing Consent, which requires explicitly "
            "acknowledging that contract content may leave this system."
            % (company.display_name, provider)
        )
    _logger.info(
        "Cloud AI consent verified for company %s (provider=%s)",
        company.display_name, provider,
    )
