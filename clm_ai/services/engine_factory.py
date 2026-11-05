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


def build_engine(env, provider, company):
    """Return an AIEngine for provider, enforcing consent for cloud engines.

    Args:
        env: Odoo environment.
        provider: one of the clm.ai.job provider selection values.
        company: the res.company that owns the contract being processed.
            Not the acting user's company — a Legal user working across
            companies must not carry one company's approval into another's
            contract.
    """
    engine_class = _resolve_engine_class(provider)

    if engine_class.is_cloud:
        _assert_cloud_consent(company, provider)

    return engine_class(env)


def _resolve_engine_class(provider):
    if provider == 'mock':
        from .ai_engine import MockAIEngine
        return MockAIEngine
    if provider == 'gemini':
        from .gemini_engine import GeminiAIEngine
        return GeminiAIEngine
    raise UserError(
        "AI provider '%s' is not implemented." % provider
    )


def _assert_cloud_consent(company, provider):
    if not company:
        raise CloudConsentError(
            "Cannot determine which company owns this contract, so cloud AI "
            "processing cannot be authorised."
        )
    if not company.sudo().clm_ai_cloud_processing_acknowledged:
        raise CloudConsentError(
            "Cloud AI processing is not enabled for %s.\n\n"
            "Contract content would be transmitted to %s. Enable it in "
            "Settings → NextContract AI Configuration → Cloud Processing Consent, which requires explicitly "
            "acknowledging that contract content may leave this system."
            % (company.display_name, provider)
        )
    _logger.info(
        "Cloud AI consent verified for company %s (provider=%s)",
        company.display_name, provider,
    )
