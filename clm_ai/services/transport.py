# Part of NextContract. See LICENSE file for full copyright and licensing details.
"""Transport routing and page limits for provider requests.

Transport is a delivery decision only: the same document must produce the
same normalised suggestions whether it went inline or through the Files API.
Because the threshold is configurable, that equivalence is a regression test
rather than an assumption.
"""

import logging

from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

INLINE_LIMIT_PARAM = 'clm_ai.inline_payload_limit_mb'
MAX_PAGES_PARAM = 'clm_ai.max_pages'

# Under Gemini's ~20MB inline request cap, leaving headroom for the prompt
# and the response schema.
DEFAULT_INLINE_LIMIT_MB = 15
DEFAULT_MAX_PAGES = 300

# base64 encodes 3 bytes as 4 characters.
_BASE64_INFLATION = 4 / 3


class MaxPagesExceeded(UserError):
    """Raised when a document is longer than the configured limit."""


def serialised_payload_size(payload):
    """Bytes the request will carry for this payload.

    Measures the encoded size, not the original attachment: rasterising a
    scan and base64-encoding the pages can multiply the figure several times
    over, and routing on the smaller number sends oversized requests.
    """
    image_bytes = sum(len(page.image) for page in payload.pages)
    text_bytes = len((payload.text or '').encode('utf-8'))
    return int(image_bytes * _BASE64_INFLATION) + text_bytes


def choose_transport(env, payload):
    """Return 'inline' or 'files_api' for this payload."""
    limit_mb = float(
        env['ir.config_parameter'].sudo().get_param(
            INLINE_LIMIT_PARAM, DEFAULT_INLINE_LIMIT_MB)
    )
    limit_bytes = limit_mb * 1024 * 1024
    size = serialised_payload_size(payload)

    transport = 'inline' if size <= limit_bytes else 'files_api'
    _logger.debug(
        "Transport=%s (payload %.2fMB, limit %.2fMB)",
        transport, size / 1024 / 1024, limit_mb,
    )
    return transport


def assert_within_page_limit(env, payload):
    """Raise unless the document is short enough to process in full.

    Truncation is never an option here. Extracting from part of a contract
    and reporting high confidence would be worse than failing.
    """
    max_pages = int(
        env['ir.config_parameter'].sudo().get_param(
            MAX_PAGES_PARAM, DEFAULT_MAX_PAGES)
    )
    if payload.page_count > max_pages:
        raise MaxPagesExceeded(
            "This document has %d pages, which exceeds the configured maximum "
            "of %d.\n\nThe document was not processed. Extracting from only "
            "part of a contract would produce confident but incomplete data. "
            "Raise the limit in Settings → NextContract AI Configuration → Document Limits, or split the document."
            % (payload.page_count, max_pages)
        )
    return True
