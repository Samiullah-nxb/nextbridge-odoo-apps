# Part of NextContract. See LICENSE file for full copyright and licensing details.
"""Shared NextContract Jinja / docxtpl placeholder catalog."""

from __future__ import annotations

# Scalar placeholders resolved by DraftAssemblyEngine / DocxRenderer.
# Keep in sync with assembly_engine._resolve_variables / _contract_term_variables
# and docx_renderer._build_jinja_context.
SCALAR_PLACEHOLDERS = (
    # Parties
    ('partner_name', 'Counterparty name'),
    ('partner_address', 'Counterparty full address'),
    ('partner_street', 'Counterparty street'),
    ('partner_city', 'Counterparty city'),
    ('partner_zip', 'Counterparty ZIP / postal code'),
    ('partner_country', 'Counterparty country'),
    ('partner_email', 'Counterparty email'),
    ('partner_phone', 'Counterparty phone'),
    ('partner_vat', 'Counterparty VAT / Tax ID'),
    ('company_name', 'Provider company'),
    ('company_address', 'Provider company full address'),
    ('company_street', 'Provider company street'),
    ('company_city', 'Provider company city'),
    ('company_zip', 'Provider company ZIP / postal code'),
    ('company_country', 'Provider company country'),
    ('company_email', 'Provider company email'),
    ('company_phone', 'Provider company phone'),
    ('company_vat', 'Provider company VAT / Tax ID'),
    # Contract identity
    ('contract_name', 'Contract title'),
    ('contract_ref', 'Contract reference'),
    ('contract_type', 'Contract type'),
    # Dates
    ('effective_date', 'Effective date'),
    ('expiry_date', 'Expiry date'),
    ('signing_date', 'Signing date'),
    ('renewal_date', 'Renewal date'),
    ('today', 'Today’s date (generation date)'),
    # Terms
    ('notice_period_days', 'Notice period (days)'),
    ('notice_period', 'Notice period (e.g. "30 days")'),
    ('contract_value', 'Contract value'),
    ('currency', 'Currency'),
    ('auto_renewal', 'Auto-renewal (Yes/No)'),
    ('termination_for_convenience', 'Termination for convenience (Yes/No)'),
    # Ownership & org
    ('business_owner', 'Business owner'),
    ('legal_owner', 'Legal owner'),
    ('procurement_owner', 'Procurement owner'),
    ('finance_owner', 'Finance owner'),
    ('department', 'Department'),
    # Risk / classification
    ('risk_level', 'Risk level'),
    ('risk_rating', 'Risk rating'),
    ('confidentiality_level', 'Confidentiality level'),
    # Signature parties (names / labels for the signature block)
    ('company_signatory', 'Provider authorised signatory name'),
    ('partner_signatory', 'Counterparty authorised signatory name'),
    ('company_signatory_title', 'Provider signatory job title'),
    ('partner_signatory_title', 'Counterparty signatory job title'),
    ('company_signature_label', 'Provider signature party label'),
    ('partner_signature_label', 'Counterparty signature party label'),
    # Template meta (filled at DOCX render)
    ('template_name', 'Template name'),
    ('template_version', 'Template version'),
)

# Loop / block expressions allowed inside clause loops.
LOOP_PLACEHOLDERS = (
    ('section.title', 'Section title (inside clause loop)'),
    ('clause.title', 'Clause title (inside clause loop)'),
    ('clause.category', 'Clause category (inside clause loop)'),
    ('clause.content', 'Clause standard wording (inside clause loop)'),
    ('clause.description', 'Clause description (inside clause loop)'),
    ('clause.editability', 'Clause editability'),
    ('clause.risk_level', 'Clause risk level'),
    ('clause.required', 'Clause required (true/false)'),
    ('clause.is_locked', 'Clause is locked'),
    ('clause.is_restricted', 'Clause is restricted'),
)

LOOP_EXPRESSIONS = tuple(key for key, _label in LOOP_PLACEHOLDERS)

CLAUSE_LOOP_MARKER = '{% for section in sections %}'

# One block per clause: title, category, standard wording.
# Do not print {{ section.title }} here — with one-clause sections it duplicates
# the clause title and leaves a large empty gap from the inner {% for %}.
CLAUSE_LOOP_SNIPPET = (
    '{% for section in sections %}',
    '{% for clause in section.clauses %}',
    '{{ clause.title }}',
    '{{ clause.category }}',
    '{{ clause.content }}',
    '{% endfor %}',
    '{% endfor %}',
)

# Dual-party wet-ink / print signature block for Base DOCX.
# Each blank '' becomes a real paragraph when inserted (see tag editor).
# One blank line above Signature only — room for the Sign stroke.
# No blank after IN WITNESS (that looked like too much line height).
SIGNATURE_BLOCK_SNIPPET = (
    'IN WITNESS WHEREOF, the parties have executed this Agreement.',
    'For {{ company_signature_label }}:',
    'Name: {{ company_signatory }}',
    'Title: {{ company_signatory_title }}',
    '',
    'Signature: ___________________________',
    'Date: {{ signing_date }}',
    '',
    'For {{ partner_signature_label }}:',
    'Name: {{ partner_signatory }}',
    'Title: {{ partner_signatory_title }}',
    '',
    'Signature: ___________________________',
    'Date: {{ signing_date }}',
)

SNIPPET_INSERTS = {
    'clause_loop': '\n'.join(CLAUSE_LOOP_SNIPPET),
    'signature_block': '\n'.join(SIGNATURE_BLOCK_SNIPPET),
}


def catalog_for_ui(clause_lines=None):
    """Return list of {key, label, insert} for the tag palette."""
    rows = []
    for key, label in SCALAR_PLACEHOLDERS:
        rows.append({
            'key': key,
            'label': label,
            'insert': '{{ %s }}' % key,
            'group': 'Fields',
        })
    for key, label in LOOP_PLACEHOLDERS:
        rows.append({
            'key': key,
            'label': label,
            'insert': '{{ %s }}' % key,
            'group': 'Clause fields',
        })
    rows.append({
        'key': 'clause_loop',
        'label': 'Insert Clause Collection',
        'insert': SNIPPET_INSERTS['clause_loop'],
        'group': 'Snippets',
        'is_snippet': True,
    })
    rows.append({
        'key': 'signature_block',
        'label': 'Insert signature block (provider + counterparty)',
        'insert': SNIPPET_INSERTS['signature_block'],
        'group': 'Snippets',
        'is_snippet': True,
    })
    return rows


def known_expression_names():
    """Set of allowed {{ expression }} names for validation."""
    names = {key for key, _label in SCALAR_PLACEHOLDERS}
    names.update(LOOP_EXPRESSIONS)
    names.update({'sections', 'section', 'clause', 'clause_set'})
    return names


def selection_options():
    """Odoo Selection list: [(key, label), ...] for form dropdowns."""
    options = [(key, '%s — %s' % (key, label)) for key, label in SCALAR_PLACEHOLDERS]
    options.extend(
        (key, '%s — %s' % (key, label)) for key, label in LOOP_PLACEHOLDERS
    )
    options.append(('clause_set', 'Full Clause Set'))
    options.append(('clause_loop', 'Clause Set snippet'))
    options.append(('signature_block', 'Signature block (both parties)'))
    return options


def insert_text_for_key(key):
    if key in SNIPPET_INSERTS:
        return SNIPPET_INSERTS[key]
    if not key:
        return ''
    return '{{ %s }}' % key
