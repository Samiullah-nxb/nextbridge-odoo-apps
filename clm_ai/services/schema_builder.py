# Part of NextContract. See LICENSE file for full copyright and licensing details.
"""Builds the AI response schema from configured field definitions.

Two things matter here.

First, the schema is generated, not written by hand, so the model is asked
for exactly the fields that are configured for the contract's type.

Second, ai_hint is injected as a *field description* inside the JSON schema
rather than concatenated into the prompt body. JSON-schema descriptions are
structurally per-field metadata, so a hint reading "ignore previous
instructions" arrives as documentation attached to one field, not as an
instruction in the system turn. This is containment by structure rather than
by sanitising free text, which never holds.

That is not a claim that the model cannot read the description. The real
invariant is the pipeline: schema validation, then staged suggestions, then
human review. The worst outcome of a hostile hint is a bad suggestion that a
reviewer sees.
"""

import datetime
from dataclasses import dataclass, field as dataclass_field
from typing import Literal, Optional

from odoo.exceptions import UserError

try:
    from pydantic import BaseModel, Field, create_model
    PYDANTIC_AVAILABLE = True
except ImportError:
    PYDANTIC_AVAILABLE = False
    BaseModel = object

# The smallest excerpt that justifies a value is enough for a reviewer.
# Twenty fields times full paragraphs times two thousand contracts is a
# material cost with no reviewer benefit.
SOURCE_TEXT_MAX_CHARS = 1000


class SchemaBuildError(UserError):
    """Raised when configured definitions cannot produce a valid schema."""


@dataclass
class FieldSpec:
    """One field the model is asked to extract.

    Deliberately a plain dataclass rather than an ORM record: the builder is
    pure and unit-testable, and the same spec can describe a core contract
    column or a dynamic definition.
    """
    code: str
    label: str
    value_type: str
    ai_hint: str = ''
    options: list = None
    is_core: bool = False

    def __post_init__(self):
        if self.options is None:
            self.options = []


_SIMPLE_TYPES = {
    'char': str,
    'text': str,
    'date': datetime.date,
    'float': float,
    'monetary': float,
    'boolean': bool,
}


def build_extraction_schema(specs, include_text_layer=False):
    """Return a Pydantic model describing the expected extraction response.

    Every field is optional: a field the model cannot find must be omitted
    rather than guessed at.

    include_text_layer adds a full transcription to the *same* response rather
    than making a second call, which is what keeps "retain a text layer" from
    doubling the request count.
    """
    if not PYDANTIC_AVAILABLE:
        raise SchemaBuildError(
            "The 'pydantic' library is required for AI extraction. "
            "Install it on the Odoo server: pip install 'pydantic>=2.0.0'"
        )
    if not specs:
        raise SchemaBuildError(
            "No extractable fields are configured for this contract type. "
            "Add contract field definitions with 'AI Extractable' enabled, or "
            "enable extraction for at least one core field."
        )

    attributes = {}
    for spec in specs:
        nested_model = _build_field_model(spec)
        attributes[spec.code] = (
            Optional[nested_model],
            Field(default=None, description='%s. Omit entirely if not found.' % spec.label),
        )

    if include_text_layer:
        attributes['document_text'] = (
            Optional[str],
            Field(default=None,
                  description='A complete plain-text transcription of the '
                              'document, for full-text search. Preserve reading '
                              'order; do not summarise.'),
        )

    return create_model('ContractExtractionResult', **attributes)


def _build_field_model(spec):
    """One nested model per field, carrying the value and its evidence."""
    value_type = _resolve_value_type(spec)
    return create_model(
        'Extracted_%s' % spec.code,
        value=(
            value_type,
            Field(description=_value_description(spec)),
        ),
        confidence=(
            float,
            Field(ge=0.0, le=100.0,
                  description='How clearly the document states this value, 0-100.'),
        ),
        source_page=(
            int,
            Field(ge=1, description='1-indexed page the value was read from.'),
        ),
        source_text=(
            str,
            Field(max_length=SOURCE_TEXT_MAX_CHARS,
                  description='The smallest excerpt that supports this value.'),
        ),
    )


def _resolve_value_type(spec):
    if spec.value_type == 'selection':
        if not spec.options:
            raise SchemaBuildError(
                "Field '%s' is a selection but has no active options. Add at "
                "least one option, or disable AI extraction for it."
                % spec.label
            )
        return Literal[tuple(spec.options)]

    try:
        return _SIMPLE_TYPES[spec.value_type]
    except KeyError:
        raise SchemaBuildError(
            "Field '%s' has unsupported value type '%s'."
            % (spec.label, spec.value_type)
        )


def _value_description(spec):
    """Build the field's schema description.

    The configured hint is embedded here — as metadata about this one field —
    and nowhere else.
    """
    parts = [spec.label]
    if spec.ai_hint:
        parts.append(spec.ai_hint.strip())
    if spec.value_type == 'date':
        parts.append('Format strictly as YYYY-MM-DD.')
    if spec.value_type == 'selection':
        parts.append('Return exactly one of: %s.' % ', '.join(spec.options))
    return ' '.join(parts)
