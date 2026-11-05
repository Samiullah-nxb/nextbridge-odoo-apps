# Part of NextContract. See LICENSE file for full copyright and licensing details.
"""
Final Contract Content Builder

The single source of truth for every contract export. The PDF renderer
(QWeb / wkhtmltopdf), the DOCX renderer (python-docx), the on-screen preview and
the version comparison all consume the dict this builder returns, so they
cannot drift apart:

                      CONTRACT VERSION
                            |
                    FinalContractBuilder
                            |
          +---------+-------+--------+-----------+
          |         |                |           |
      QWeb PDF  python-docx       Preview     Redline
          |         |
         PDF       DOCX

Versioned content
-----------------
A version created through the contract editor (Generate Draft, Create New
Version, an amendment) carries `content_snapshot`: the contract's title,
parties, terms, template body, clause wording and placeholder values, frozen
at the moment the version was created. Everything rendered for that version is
built from the snapshot, never from the live contract — so Version 1 keeps
saying "20 days" after the contract is edited to 30 and Version 2 is created.

`capture()` produces a snapshot from the live contract; `build()` renders from
the snapshot when there is one.

Versions without a snapshot (created before snapshots existed, or uploaded as a
file) keep the earlier behaviour: the wording comes from the version's own
document — or the signature draft it was executed from — and otherwise from the
template; the terms come from the contract record. Nothing is invented: if no
wording is available the document says so rather than shipping an empty body.

This module holds no AI analysis, no review metadata and no internal Odoo
technical fields — it produces the legal/business document only.
"""

import base64
import html as html_lib
import re

from lxml import etree, html as lxml_html
from markupsafe import Markup, escape

from odoo import fields
from odoo.tools import html_sanitize

from .assembly_engine import (
    PLACEHOLDER_PATTERN, DraftAssemblyEngine, can_read_field, readable_field_values,
)
from .contract_text_parser import parse_contract_text

SNAPSHOT_SCHEMA = 1

# Snapshot keys that record when/how content was captured, not what it says.
_SNAPSHOT_VOLATILE_KEYS = ('schema', 'captured_at', 'captured_from')

# Contract field definitions carry a code but no category, so the two headline
# term groups are mapped by code here. Anything unmapped falls through to
# "Other Terms" (monetary/numeric values land in Commercial Terms instead).
_COMMERCIAL_CODES = {
    'payment_milestones',
    'bank_guarantee',
    'insurance_required',
    'tax_treatment',
}
_LEGAL_CODES = {
    'governing_law',
    'dispute_forum',
    'liability_cap',
    'indemnity_position',
    'confidentiality_period',
}

_KIND_LABELS = {
    'signed': 'Final Signed Contract',
    'approved': 'Final Approved Contract',
    'draft': 'Contract Draft',
}

# Below this, an extracted "text layer" is page furniture rather than a contract.
_MIN_CONTRACT_TEXT_CHARS = 200

# Which versions are most likely to carry the agreed wording, best first.
_TEXT_VERSION_RANK = {
    'signature_draft': 60,
    'final_legal_draft': 50,
    'negotiated_draft': 40,
    'counterparty_draft': 30,
    'legal_draft': 20,
    'internal_draft': 10,
    'executed': 5,
}

_EXECUTION_METHOD_LABELS = {
    'odoo_sign': 'Electronic signature (Odoo Sign)',
    'manual': 'Manual signature',
    'legacy_migration': 'Migrated legacy record',
}

_TEMPLATE_STATES = ('published', 'superseded')

_BLOCK_PATTERN = re.compile(
    r'<(h[1-6]|p|li|div|blockquote)\b[^>]*>(.*?)</\1>', re.IGNORECASE | re.DOTALL
)

# "30 days", "thirty (30) days", "30 (thirty) calendar days"
_DAYS_PATTERN = re.compile(
    r'\b(\d{1,4})\)?\s*(?:\([^)]{0,30}\)\s*)?(?:calendar\s+|business\s+|working\s+)?days?\b',
    re.IGNORECASE,
)


def comparable_snapshot(snapshot):
    """The part of a content snapshot that says what the contract is."""
    return {
        key: value for key, value in (snapshot or {}).items()
        if key not in _SNAPSHOT_VOLATILE_KEYS
    }


def _normalize_clause_html(html):
    """Return HTML with real tags, even when the DB stored them entity-escaped.

    Odoo Html fields sometimes persist ``&lt;p&gt;…&lt;/p&gt;``. Treating that as
    already-safe text leaves literal ``<p>`` in DOCX/PDF after striptags/unescape.
    """
    text = str(html or '')
    if '&lt;' in text and re.search(
        r'&lt;/?\s*(?:p|div|li|ul|ol|br|h[1-6]|span|blockquote)\b', text, re.I
    ):
        return html_lib.unescape(text)
    return text


def _plain(html):
    """Visible text of an HTML fragment, whitespace collapsed."""
    return Markup(_normalize_clause_html(html)).striptags()


# Editing aids of the HTML editor: attached-file boxes and embedded components.
_EDITOR_WIDGETS_XPATH = (
    ".//*[contains(concat(' ', normalize-space(@class), ' '), ' o_file_box ')]"
    " | .//*[@data-embedded]"
)
_EDITOR_MARKERS = ('o_file_box', 'data-embedded', 'data-oe-version')
_HTML_BLOCK_TAGS = frozenset({
    'p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'ul', 'ol', 'li', 'blockquote', 'table', 'pre',
})


def _unwrap_block_container(node):
    """Replace a wrapper <div> by its content, keeping loose text as paragraphs."""
    if node.text and node.text.strip():
        paragraph = lxml_html.Element('p')
        paragraph.text, node.text = node.text, None
        node.insert(0, paragraph)
    for child in list(node):
        if child.tail and child.tail.strip():
            paragraph = lxml_html.Element('p')
            paragraph.text, child.tail = child.tail, None
            child.addnext(paragraph)
    node.drop_tag()


def _clean_editor_html(html):
    """Wording only: drop editor widgets and unwrap the editor's root <div>.

    A box linking to an uploaded file is not contract wording. The editor also
    marks content with `data-oe-version`: sometimes on a <div> wrapping every
    block — which would otherwise be read as one single block, hiding the
    headings inside it — and sometimes simply on the first line's own <div>,
    which must stay. Only a wrapper (a div holding blocks) is unwrapped.
    """
    html = _normalize_clause_html(html)
    if not any(marker in html for marker in _EDITOR_MARKERS):
        return html
    try:
        root = lxml_html.fragment_fromstring(html, create_parent='div')
    except (etree.ParserError, ValueError):
        return html
    for node in root.xpath(_EDITOR_WIDGETS_XPATH):
        node.drop_tree()
    for node in root.xpath('.//div[@data-oe-version]'):
        if any(child.tag in _HTML_BLOCK_TAGS for child in node):
            _unwrap_block_container(node)
    serialized = lxml_html.tostring(root, encoding='unicode')
    return serialized[len('<div>'):-len('</div>')]


class FinalContractBuilder:
    """Builds the canonical content of a contract document.

    Usage:
        content = FinalContractBuilder(contract, version, kind='signed').build()
        snapshot = FinalContractBuilder(contract).capture()
    """

    def __init__(self, contract, version=None, kind='approved', snapshot=None):
        self.contract = contract
        self.version = version
        self.kind = kind if kind in _KIND_LABELS else 'approved'
        if snapshot is None and version and 'content_snapshot' in version._fields:
            snapshot = version.sudo().content_snapshot
        self.snapshot = snapshot or None

    # -------------------------------------------------------------------------
    # Entry points
    # -------------------------------------------------------------------------

    def build(self):
        contract = self.contract
        snapshot = self.snapshot
        body, origin, text_version = self._build_body()
        if snapshot:
            title = snapshot.get('title') or ''
            reference = snapshot.get('reference') or ''
            contract_type = snapshot.get('contract_type') or ''
            key_terms = snapshot.get('key_terms') or []
            commercial = snapshot.get('commercial_terms') or []
            legal = snapshot.get('legal_terms') or []
            other = snapshot.get('other_terms') or []
        else:
            title = contract.name or ''
            reference = contract.contract_ref or ''
            contract_type = contract.contract_type_id.name or ''
            key_terms = self._build_key_terms()
            commercial, legal, other = self._build_custom_terms()
        signatures, evidence = self._build_signatures()
        signing_date = self._signing_date_display()
        parties = self._parties_for_signature_cards(
            self._parties(), signatures, signing_date,
        )

        return {
            'contract': contract,
            'version': self.version,
            'kind': self.kind,
            'kind_label': _KIND_LABELS[self.kind],
            'status_label': self._status_label(),
            'title': title,
            'reference': reference,
            'contract_type': contract_type,
            'version_label': self._version_label(),
            'version_type_label': self._version_type_label(),
            'parties': parties,
            # Printed on signature Name/Date lines (parity with Base DOCX
            # {{ company_signatory }} / {{ partner_signatory }} / {{ signing_date }}).
            'signing_date': signing_date,
            'key_terms': key_terms,
            'commercial_terms': commercial,
            'legal_terms': legal,
            'other_terms': other,
            'body': body,
            'content_origin': origin,
            'content_version_label': (
                'v%s' % text_version.version_number if text_version else ''
            ),
            'approved_changes': self._build_approved_changes(),
            'deviations': self._build_deviations(),
            'signatures': signatures,
            'signature_evidence': evidence,
            # 'professional' = Default Format cards (Signature blank; Name/Date filled).
            # 'classic' = Formats 2–3 evidence table / older signature block.
            'signature_layout': self._signature_layout(),
            'signature_image': self._build_signature_image(),
            'execution': self._build_execution(),
            'source_document': self._build_source_document(),
            'captured_at': (snapshot or {}).get('captured_at') or '',
            'warnings': self.content_warnings(snapshot),
        }

    def capture(self):
        """Freeze the live contract's content into a JSON-serialisable dict.

        Clause wording is stored raw, with its {{placeholders}}, next to the
        placeholder values; rendering fills them in. The wording therefore stays
        editable as a template when an amendment starts from this version, while
        what the version says can never change.
        """
        contract = self.contract
        # sudo: the template is library wording; every role that can read the
        # contract may see the document it produces.
        template = contract.template_id.sudo() if 'template_id' in contract._fields else False
        if template and template.state not in _TEMPLATE_STATES:
            template = contract.env['clm.template']
        context = DraftAssemblyEngine(template, contract).assemble(substitute=False)

        clauses = []
        for section in context['sections']:
            for clause in section['clauses']:
                clauses.append({
                    'section': section['title'] or '',
                    'title': clause['title'] or '',
                    'content': str(clause['content'] or ''),
                    'category': clause.get('category') or '',
                    'code': clause.get('code') or '',
                    'description': clause.get('description') or '',
                    'clause_id': clause.get('clause_id') or False,
                    'clause_version': clause.get('clause_version') or 0,
                    'required': bool(clause.get('required')),
                    'source': clause.get('source') or 'template',
                })

        commercial, legal, other = self._build_custom_terms()
        template_meta = False
        if template:
            # Prefer the contract's locked Base DOCX; never snapshot the
            # template's current Active version for an already-locked contract.
            locked_base = False
            if (
                'base_docx_version_id' in contract._fields
                and contract.base_docx_version_id
            ):
                locked_base = contract.base_docx_version_id
            elif hasattr(template, '_active_base_docx_version'):
                locked_base = template._active_base_docx_version()
            template_meta = {
                'id': template.id,
                'name': template.name or '',
                'version': template.version_number or 0,
                'body': str(template.content or ''),
                'base_docx_version_id': locked_base.id if locked_base else False,
                'base_docx_version_number': (
                    locked_base.version_number if locked_base else 0
                ),
                'base_document_id': (
                    locked_base.document_id.id
                    if locked_base and locked_base.document_id
                    else (
                        template.base_document_id.id
                        if template.base_document_id else False
                    )
                ),
            }
        return {
            'schema': SNAPSHOT_SCHEMA,
            'captured_at': fields.Datetime.to_string(fields.Datetime.now()),
            'title': contract.name or '',
            'reference': contract.contract_ref or '',
            'contract_type': contract.contract_type_id.name or '',
            'currency': contract.currency_id.name or '',
            'parties': self._build_parties(),
            'key_terms': self._build_key_terms(),
            'commercial_terms': commercial,
            'legal_terms': legal,
            'other_terms': other,
            'variables': {
                key: '' if value in (None, False) else str(value)
                for key, value in context['variables'].items()
            },
            'template': template_meta,
            'clauses': clauses,
        }

    # -------------------------------------------------------------------------
    # Identity / status
    # -------------------------------------------------------------------------

    def _version_label(self):
        if self.version:
            return 'v%s' % self.version.version_number
        return ''

    def _version_type_label(self):
        if not self.version:
            return ''
        return dict(
            self.version._fields['version_type']._description_selection(self.version.env)
        ).get(self.version.version_type, self.version.version_type or '')

    def _status_label(self):
        contract = self.contract
        if self.kind == 'signed':
            return 'Signed'
        if self.kind == 'draft':
            return dict(
                contract._fields['state']._description_selection(contract.env)
            ).get(contract.state, 'Draft')
        if 'signature_status' in contract._fields and contract.signature_status == 'pending':
            return 'Approved — awaiting signature'
        return 'Approved'

    # -------------------------------------------------------------------------
    # Parties
    # -------------------------------------------------------------------------

    def _parties(self):
        if not self.snapshot:
            return self._build_parties()
        frozen = self.snapshot.get('parties') or []
        if not frozen:
            return self._build_parties()
        # Refresh empty signatory Name lines from the live contract so Default
        # Format matches Base DOCX {{ company_signatory }} / {{ partner_signatory }}
        # even when an older snapshot omitted them.
        live_by_role = {
            (party.get('role') or '').strip().lower(): party
            for party in self._build_parties()
        }
        parties = []
        for party in frozen:
            entry = dict(party)
            if not (entry.get('representative') or '').strip():
                live = live_by_role.get((entry.get('role') or '').strip().lower()) or {}
                if live.get('representative'):
                    entry['representative'] = live['representative']
                    entry['representative_label'] = (
                        live.get('representative_label')
                        or entry.get('representative_label')
                        or ''
                    )
            parties.append(entry)
        return parties

    def _build_parties(self):
        parties = []
        contract = self.contract
        company_partner = contract.company_id.partner_id
        # Same resolution as assembly_engine company_signatory / partner_signatory
        # so Default Format Name lines match Base DOCX placeholders.
        company_signatory = (
            (contract.legal_owner_id.name if contract.legal_owner_id else '')
            or (contract.user_id.name if contract.user_id else '')
        )
        partner = contract.partner_id
        partner_signatory_rec = False
        if partner and 'partner_signatory_id' in contract._fields:
            partner_signatory_rec = contract.partner_signatory_id
        partner_signatory = (
            (partner_signatory_rec.name if partner_signatory_rec else '')
            or (partner.name if partner else '')
        )
        if company_partner:
            parties.append({
                'role': 'Company',
                'name': company_partner.name or '',
                'address': self._format_address(company_partner),
                'representative': company_signatory,
                'representative_label': 'Authorised Signatory' if company_signatory else '',
            })
        if partner:
            parties.append({
                'role': 'Counterparty',
                'name': partner.name or '',
                'address': self._format_address(partner),
                'representative': partner_signatory,
                'representative_label': 'Authorised Signatory' if partner_signatory else '',
            })
        return parties

    def _signing_date_display(self):
        """Locale date for signature Date lines — same rule as {{ signing_date }}."""
        from odoo.tools.misc import format_date
        contract = self.contract
        day = contract.signing_date or fields.Date.context_today(contract)
        return format_date(contract.env, day) or ''

    @staticmethod
    def _parties_for_signature_cards(parties, signatures, signing_date):
        """Stamp Name / Date onto party cards for Default Format + QWeb.

        Prefer recorded e-sign identity when present; otherwise keep the
        authorised signatory and the contract signing date.
        """
        by_party = {
            (row.get('party') or '').strip().lower(): row
            for row in (signatures or [])
            if (row.get('party') or '').strip()
        }
        enriched = []
        for party in parties or []:
            entry = dict(party)
            row = by_party.get((entry.get('name') or '').strip().lower()) or {}
            if (row.get('name') or '').strip():
                entry['representative'] = row['name'].strip()
            if (row.get('signed_on') or '').strip():
                entry['signing_date'] = row['signed_on'].strip()
            elif signing_date and not (entry.get('signing_date') or '').strip():
                entry['signing_date'] = signing_date
            enriched.append(entry)
        return enriched

    def _signature_layout(self):
        """Which signature chrome Preview / DOCX / PDF / Final must share."""
        contract = self.contract
        if hasattr(contract, '_clm_uses_default_docx_layout'):
            try:
                if contract._clm_uses_default_docx_layout(version=self.version):
                    return 'professional'
            except Exception:
                pass
        if not contract.template_id:
            return 'professional'
        return 'classic'

    @staticmethod
    def _format_address(partner):
        try:
            address = partner._display_address(without_company=True)
        except Exception:
            address = ''
        return ', '.join(part.strip() for part in (address or '').splitlines() if part.strip())

    # -------------------------------------------------------------------------
    # Terms
    # -------------------------------------------------------------------------

    def _build_key_terms(self):
        contract = self.contract
        terms = [
            ('Contract Type', contract.contract_type_id.name),
            ('Effective Date', self._format_date(contract.effective_date)),
            ('Expiry Date', self._format_date(contract.expiry_date)),
            ('Signing Date', self._format_date(contract.signing_date)),
            ('Notice Period', '%s days' % contract.notice_period_days if contract.notice_period_days else ''),
            ('Auto-Renewal', self._format_bool(contract.auto_renewal)),
            ('Termination for Convenience', self._format_bool(contract.termination_for_convenience)),
        ]
        if contract.department_id:
            terms.append(('Department', contract.department_id.name))
        return self._as_terms(terms)

    def _build_custom_terms(self):
        """Split contract field values into Commercial / Legal / Other buckets."""
        commercial, legal, other = [], [], []
        # Contract value is field-restricted; a user who may not read it gets a
        # document without it rather than an access error.
        if can_read_field(self.contract, 'contract_value'):
            contract_value = self._format_money(self.contract.contract_value)
            if contract_value:
                commercial.append({'label': 'Contract Value', 'value': contract_value})

        for value in readable_field_values(self.contract).sorted(
            lambda v: (v.definition_sequence, v._origin.id or 0)
        ):
            definition = value.definition_id
            display = value.display_value
            if not display:
                continue
            if definition.value_type == 'monetary':
                display = self._format_money(value.value_float)
            entry = {'label': definition.name, 'value': display}
            if definition.code in _COMMERCIAL_CODES or definition.value_type in ('monetary', 'float'):
                commercial.append(entry)
            elif definition.code in _LEGAL_CODES:
                legal.append(entry)
            else:
                other.append(entry)

        if self.contract.confidentiality_level:
            legal.append({
                'label': 'Confidentiality Level',
                'value': dict(
                    self.contract._fields['confidentiality_level']._description_selection(self.contract.env)
                ).get(self.contract.confidentiality_level, ''),
            })
        return commercial, legal, other

    @staticmethod
    def _as_terms(pairs):
        return [{'label': label, 'value': value} for label, value in pairs if value]

    @staticmethod
    def _format_date(value):
        return value.strftime('%d %b %Y') if value else ''

    @staticmethod
    def _format_bool(value):
        return 'Yes' if value else 'No'

    def _format_money(self, amount):
        if not amount:
            return ''
        currency = self.contract.currency_id
        formatted = '{:,.2f}'.format(amount)
        if currency:
            return '%s %s' % (currency.name, formatted)
        return formatted

    # -------------------------------------------------------------------------
    # Contract body — clauses and sections
    # -------------------------------------------------------------------------

    def _build_body(self):
        """Assemble the actual contract wording.

        Returns (body, origin, text_version). `body` is a flat list of
        structural entries — the shape every renderer consumes:

            [{'heading': str, 'level': int,
              'blocks': [{'kind': 'p'|'li', 'text': str, 'html': Markup}]}]

        Sources, in order of authority:
          'version'  — the version's frozen content (template body + clauses)
          'document' — the wording of the version's own file, or of the draft
                       it was executed from, parsed back into headings and
                       paragraphs
          'template' — the template's library clauses (versions that predate
                       content snapshots and carry no readable file)
          'none'     — no wording could be recovered
        """
        if self.snapshot:
            body = self._body_from_snapshot(self.snapshot)
            if body:
                return body, 'version', self.version
            text, text_version = self._resolve_contract_text()
            if text:
                return self._body_from_text(text), 'document', text_version
            return [], 'none', None

        # A version's own file is what that version says. The template only
        # speaks for versions that have no readable wording of their own.
        text, text_version = self._resolve_lineage_text()
        if text:
            return self._body_from_text(text), 'document', text_version

        template = self.contract.template_id.sudo()
        if template and template.state in _TEMPLATE_STATES:
            body = self._body_from_template(template)
            if body:
                return body, 'template', self.version

        text, text_version = self._resolve_contract_text()
        if text:
            return self._body_from_text(text), 'document', text_version
        return [], 'none', None

    def _body_from_snapshot(self, snapshot):
        variables = snapshot.get('variables') or {}
        body = []
        template = snapshot.get('template') or {}
        if template.get('body'):
            body.extend(self._entries_from_html(self._fill(template['body'], variables)))

        current_section = None
        for clause in snapshot.get('clauses') or []:
            section = clause.get('section') or ''
            if section and section != current_section:
                body.append({'heading': section, 'level': 1, 'blocks': []})
            current_section = section
            body.append({
                'heading': clause.get('title') or '',
                'level': 2 if section else 1,
                'blocks': self._blocks_from_html(self._fill(clause.get('content') or '', variables)),
            })
        return body

    def _body_from_template(self, template):
        # A live document (no version) shows the contract's own clause wording;
        # a version that predates snapshots must not pick up later edits.
        live = not self.version
        engine = DraftAssemblyEngine(template, self.contract, use_contract_clauses=live)
        context = engine.assemble()
        body = []
        if live and template.content:
            body.extend(self._entries_from_html(
                self._fill(template.content, context['variables'])
            ))
        for section in context['sections']:
            clauses = [clause for clause in section['clauses']]
            if not clauses:
                continue
            if section['title']:
                body.append({'heading': section['title'], 'level': 1, 'blocks': []})
            for clause in clauses:
                html = clause['content'] or ''
                body.append({
                    'heading': clause['title'] or '',
                    'level': 2 if section['title'] else 1,
                    'blocks': self._blocks_from_html(html),
                })
        return body

    @staticmethod
    def _fill(html, variables):
        """Replace {{placeholders}} with HTML-escaped values; unknown ones stay visible."""
        def replace(match):
            key = match.group(1).strip()
            if key not in variables:
                return match.group(0)
            return str(escape(variables[key] or ''))

        return PLACEHOLDER_PATTERN.sub(replace, str(html or ''))

    @staticmethod
    def _blocks_from_html(html):
        """Split clause HTML into paragraph / list-item blocks."""
        html = _clean_editor_html(html)
        blocks = []
        for tag, inner in re.findall(
            r'<(p|li|div|blockquote)\b[^>]*>(.*?)</\1>', str(html or ''), re.I | re.S
        ):
            text = _plain(inner)
            if not text:
                continue
            blocks.append({
                'kind': 'li' if tag.lower() == 'li' else 'p',
                'text': text,
                'html': Markup(escape(text)),
            })
        if not blocks:
            text = _plain(html)
            if text:
                blocks.append({'kind': 'p', 'text': text, 'html': Markup(html_sanitize(str(html)))})
        return blocks

    @classmethod
    def _entries_from_html(cls, html):
        """Split free-form HTML (a template body) into headed entries."""
        html = _clean_editor_html(html)
        entries = []
        current = None
        for match in _BLOCK_PATTERN.finditer(str(html or '')):
            tag, inner = match.group(1).lower(), match.group(2)
            text = _plain(inner)
            if not text:
                continue
            if tag.startswith('h'):
                current = {'heading': text, 'level': 1, 'blocks': []}
                entries.append(current)
                continue
            if current is None:
                current = {'heading': '', 'level': 1, 'blocks': []}
                entries.append(current)
            current['blocks'].append({
                'kind': 'li' if tag == 'li' else 'p',
                'text': text,
                'html': Markup(html_sanitize(inner)),
            })
        if not entries:
            blocks = cls._blocks_from_html(html)
            if blocks:
                entries.append({'heading': '', 'level': 1, 'blocks': blocks})
        return entries

    @staticmethod
    def _body_from_text(text):
        body = []
        for section in parse_contract_text(text):
            body.append({
                'heading': section['heading'],
                'level': section['level'],
                'blocks': [
                    {'kind': block['kind'], 'text': block['text'],
                     'html': Markup(escape(block['text']))}
                    for block in section['blocks']
                ],
            })
        return body

    def _resolve_lineage_text(self):
        """The wording of this version's own file, or of the draft it came from.

        Versions produced by the draft generator are skipped: their file is a
        rendering of the template, and parsing it back would duplicate the
        cover and terms tables as if they were clauses.
        """
        if not self.version:
            return '', None
        lineage = self._lineage_versions()
        generated = self._generated_version_ids(lineage)
        for version in lineage:
            if version.id in generated:
                continue
            text = self._version_text(version)
            if text and len(text.strip()) >= _MIN_CONTRACT_TEXT_CHARS:
                return text, version
        return '', None

    def _resolve_contract_text(self):
        """Find the contract's own wording along the final version's lineage.

        An executed version usually holds the signed artifact — often a scan or
        an image — while the wording lives on the signature draft it was
        executed from. Walking `source_version_id` first, then the remaining
        versions newest-first, finds the text without guessing.
        """
        for version in self._text_candidate_versions():
            text = self._version_text(version)
            if text and len(text.strip()) >= _MIN_CONTRACT_TEXT_CHARS:
                return text, version
        return '', None

    def _lineage_versions(self):
        """This version, then the version it was executed from / derived from."""
        ordered = []
        seen = set()
        cursor, hops = self.version, 0
        while cursor and cursor.id not in seen and hops < 10:
            seen.add(cursor.id)
            ordered.append(cursor)
            if 'source_version_id' in cursor._fields and cursor.source_version_id:
                cursor = cursor.source_version_id
            else:
                cursor = cursor.parent_version_id
            hops += 1
        return ordered

    def _text_candidate_versions(self):
        """Versions to try for contract wording, most authoritative first."""
        ordered = list(self._lineage_versions())
        seen = {version.id for version in ordered}
        # sudo, like the final-version resolution: which versions a contract
        # has is part of the contract, readable by every role that reads it.
        remaining = self.contract.sudo().version_ids.sorted(
            lambda v: (_TEXT_VERSION_RANK.get(v.version_type, 0), v.version_number),
            reverse=True,
        )
        for version in remaining:
            if version.id not in seen:
                seen.add(version.id)
                ordered.append(version)
        return ordered

    def _generated_version_ids(self, versions):
        env = self.contract.env
        ids = [version.id for version in versions if isinstance(version.id, int)]
        if not ids or 'clm.assembly.request' not in env:
            return set()
        requests = env['clm.assembly.request'].sudo().search([('result_version_id', 'in', ids)])
        return set(requests.mapped('result_version_id').ids)

    @staticmethod
    def _version_text(version):
        version = version._origin if version else version
        if not version:
            return ''
        return version._ensure_extracted_text() or ''

    # -------------------------------------------------------------------------
    # Consistency checks and plain-text views of the content
    # -------------------------------------------------------------------------

    @classmethod
    def content_warnings(cls, snapshot):
        """Wording that may contradict the contract's structured terms.

        Two checks, both advisory:
          * a {{placeholder}} that is unknown or has no value;
          * clause text that states a notice period in days different from the
            contract's Notice Period (typed as a number instead of using
            {{notice_period_days}}).
        """
        if not snapshot:
            return []
        variables = snapshot.get('variables') or {}
        sources = []
        template = snapshot.get('template') or {}
        if template.get('body'):
            sources.append(('The template body', template['body']))
        for clause in snapshot.get('clauses') or []:
            sources.append(("Clause '%s'" % (clause.get('title') or '?'), clause.get('content') or ''))

        notice = str(variables.get('notice_period_days') or '').strip()
        warnings = []
        for label, html in sources:
            html = str(html or '')
            for key in dict.fromkeys(match.strip() for match in PLACEHOLDER_PATTERN.findall(html)):
                if key not in variables:
                    warnings.append(
                        '%s uses {{%s}}, which is not a known contract value.' % (label, key)
                    )
                elif not variables[key]:
                    warnings.append(
                        '%s uses {{%s}}, but that value is empty on the contract.' % (label, key)
                    )
            if notice.isdigit():
                text = _plain(cls._fill(html, variables))
                for days in cls._notice_days_mentioned(text):
                    if days != int(notice):
                        warnings.append(
                            "%s mentions %s days' notice, but the contract's Notice Period "
                            "is %s days." % (label, days, notice)
                        )
        return list(dict.fromkeys(warnings))

    @staticmethod
    def _notice_days_mentioned(text):
        days = []
        for sentence in re.split(r'(?<=[.;!?])\s+', text or ''):
            if 'notice' in sentence.lower():
                days.extend(int(number) for number in _DAYS_PATTERN.findall(sentence))
        return days

    @staticmethod
    def content_lines(content):
        """The document's contract content as comparable lines (for redlines)."""
        lines = []

        def add(text):
            for line in str(text or '').splitlines():
                line = ' '.join(line.split())
                if line:
                    lines.append(line)

        add(content.get('title'))
        if content.get('reference'):
            add('Contract Number: %s' % content['reference'])
        if content.get('contract_type'):
            add('Contract Type: %s' % content['contract_type'])
        for party in content.get('parties') or []:
            role = party.get('role') or 'Party'
            add('%s: %s' % (role, party.get('name') or ''))
            if party.get('address'):
                add('%s address: %s' % (role, party['address']))
        for key in ('key_terms', 'commercial_terms', 'legal_terms', 'other_terms'):
            for term in content.get(key) or []:
                add('%s: %s' % (term.get('label'), term.get('value')))
        for entry in content.get('body') or []:
            add(entry.get('heading'))
            for block in entry.get('blocks') or []:
                add(block.get('text'))
        return lines

    @staticmethod
    def clauses_from_body(body):
        """Turn rendered body entries back into snapshot clauses.

        Used to freeze a version that predates snapshots exactly as it renders.
        """
        clauses = []
        section = ''
        for entry in body or []:
            blocks = entry.get('blocks') or []
            heading = entry.get('heading') or ''
            if heading and not blocks and entry.get('level', 1) <= 1:
                section = heading
                continue
            html = ''.join(
                ('<ul><li>%s</li></ul>' if block['kind'] == 'li' else '<p>%s</p>')
                % escape(block['text'])
                for block in blocks
            )
            clauses.append({
                'section': section,
                'title': heading,
                'content': html,
                'clause_id': False,
                'clause_version': 0,
                'required': True,
                'source': 'document',
            })
        return clauses

    # -------------------------------------------------------------------------
    # Approved changes and deviations
    # -------------------------------------------------------------------------

    def _build_approved_changes(self):
        """Change history of the versions that led to this final version.

        Only classified, non-empty change summaries are carried into the legal
        document — draft chatter and review metadata stay out of it.
        """
        changes = []
        versions = self.contract.sudo().version_ids.sorted(lambda v: v.version_number)
        for version in versions:
            if self.version and version.version_number > self.version.version_number:
                continue
            if not version.change_summary:
                continue
            if version.change_classification in ('none', False):
                continue
            classification = dict(
                version._fields['change_classification']._description_selection(version.env)
            ).get(version.change_classification, '')
            changes.append({
                'version': 'v%s' % version.version_number,
                'classification': classification,
                'summary': version.change_summary,
            })
        return changes

    def _build_deviations(self):
        """Confirmed clause deviations / exemptions applicable to the contract."""
        env = self.contract.env
        if 'clm.clause.deviation' not in env:
            return []
        version_ids = [vid for vid in self.contract.sudo().version_ids.ids if isinstance(vid, int)]
        if not version_ids:
            return []
        deviations = env['clm.clause.deviation'].sudo().search([
            ('version_id', 'in', version_ids),
            ('status', '=', 'confirmed'),
            ('deviation_type', '!=', 'standard'),
        ], order='version_id, id')
        rows = []
        seen = set()
        for deviation in deviations:
            label = deviation.clause_id.name or deviation.clause_title or ''
            key = (label, deviation.deviation_type)
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                'clause': label,
                'type': dict(
                    deviation._fields['deviation_type']._description_selection(env)
                ).get(deviation.deviation_type, ''),
                'rationale': deviation.rationale or '',
            })
        return rows

    # -------------------------------------------------------------------------
    # Signature / execution
    # -------------------------------------------------------------------------

    def _build_signatures(self):
        """Signature rows plus the evidence backing them.

        Returns (rows, evidence) where evidence is:
          'esign'  — Odoo Sign recorded who signed and when
          'manual' — a signatory and signing date were captured at execution
          'none'   — nothing is recorded, so the block stays a blank one to sign

        A row is only emitted when it carries real data. A contract marked
        signed with no signer information produces evidence 'none', so the
        document never asserts a signature it cannot show.

        The Sign records are read with sudo: reading sign.request needs Odoo
        Sign's own group, which NextContract roles do not carry, and the caller has
        already established read access on the contract itself.
        """
        rows = self._esign_signatures()
        if rows:
            return rows, 'esign'

        rows = self._manual_signatures()
        if rows:
            return rows, 'manual'

        return self._blank_signature_block(), 'none'

    def _esign_signatures(self):
        contract = self.contract
        if 'sign_request_ids' not in contract._fields:
            return []
        # Only a signed document of the version being rendered is its signature.
        if self.kind != 'signed':
            return []
        rows = []
        requests = contract.sudo().sign_request_ids.filtered(
            lambda r: r.state == 'signed'
        )
        source = self.version.source_version_id if (
            self.version and 'source_version_id' in self.version._fields
        ) else False
        if source and 'clm_source_version_id' in requests._fields:
            requests = requests.filtered(lambda r: r.clm_source_version_id.id == source.id)
        for request in requests:
            for item in request.request_item_ids:
                name = item.partner_id.name or ''
                signed_on = self._format_date(item.signing_date)
                # Both identity and a signing date are required: an item with
                # neither is an unsent invitation, not a signature.
                if not name or not signed_on:
                    continue
                row = {
                    'party': name,
                    'name': name,
                    'role': item.role_id.name or '',
                    'signed_on': signed_on,
                    'method': 'Electronic signature (Odoo Sign)',
                    'image': self._esign_signature_image(item),
                }
                rows.append(row)
        return rows

    def _esign_signature_image(self, item):
        """PNG/JPEG stroke captured by Odoo Sign for this signer, if any."""
        item = item.sudo()
        if item.signature:
            return self._normalize_signature_image(item.signature)
        for value in item.sign_item_value_ids:
            raw = value.value or ''
            if not raw:
                continue
            image = self._normalize_signature_image(raw)
            if image:
                return image
        return None

    @staticmethod
    def _normalize_signature_image(data):
        """Return ``{'mimetype', 'base64'}`` from Binary or data-URI text."""
        if not data:
            return None
        mimetype = 'image/png'
        if isinstance(data, memoryview):
            data = data.tobytes()
        if isinstance(data, bytes):
            if data.startswith(b'\x89PNG') or data.startswith(b'\xff\xd8'):
                if data.startswith(b'\xff\xd8'):
                    mimetype = 'image/jpeg'
                return {
                    'mimetype': mimetype,
                    'base64': base64.b64encode(data).decode('ascii'),
                }
            data = data.decode('ascii', errors='ignore')
        text = str(data).strip()
        if text.startswith('data:') and 'base64,' in text:
            header, payload = text.split('base64,', 1)
            if ';' in header:
                mimetype = header[5:].split(';', 1)[0] or mimetype
            text = payload
        text = ''.join(text.split())
        if not text:
            return None
        return {'mimetype': mimetype, 'base64': text}

    def _manual_signatures(self):
        execution = self._build_execution()
        signatory = execution.get('signatory')
        signed_on = execution.get('executed_on')
        if not signatory or not signed_on:
            return []
        return [{
            'party': self.contract.company_id.partner_id.name or 'Company',
            'name': signatory,
            'role': 'Authorised signatory',
            'signed_on': signed_on,
            'method': execution.get('method_label') or 'Manual signature',
        }]

    def _blank_signature_block(self):
        """An unsigned block: each party gets a line to sign on."""
        return [{
            'party': party['role'],
            'name': party['name'],
            'role': '',
            'signed_on': '',
            'method': '',
        } for party in self._parties()]

    def _build_signature_image(self):
        """The signed artifact as an inline image, when it is one.

        A manually executed contract is often evidenced by a photo or scan of
        the signature page. Embedding it puts the actual signature artifact in
        the exported document instead of only naming it.
        """
        if self.kind != 'signed' or not self.version:
            return None
        document = self.version.sudo().document_id
        attachment = document.attachment_id if document else False
        if not attachment or not (attachment.mimetype or '').startswith('image/'):
            return None
        if not attachment.datas:
            return None
        raw = attachment.datas
        return {
            'mimetype': attachment.mimetype,
            'name': attachment.name or '',
            'base64': raw.decode() if isinstance(raw, bytes) else raw,
            'bytes': base64.b64decode(raw),
        }

    def _build_execution(self):
        contract = self.contract
        version = self.version
        method = version.execution_method if version and 'execution_method' in version._fields else False
        signature_status = ''
        if 'signature_status' in contract._fields and contract.signature_status:
            signature_status = dict(
                contract._fields['signature_status']._description_selection(contract.env)
            ).get(contract.signature_status, '')
        # Execution details belong to the signed version only; a draft rendered
        # while the contract is executed must not claim that execution.
        executed_on = False
        if self.kind == 'signed' and 'execution_date' in contract._fields:
            executed_on = contract.execution_date
        return {
            'executed_on': self._format_date(executed_on),
            'method': method or '',
            'method_label': _EXECUTION_METHOD_LABELS.get(method, ''),
            'signature_status': signature_status,
            'signatory': (
                version.execution_signatory
                if version and 'execution_signatory' in version._fields else ''
            ) or '',
        }

    def _build_source_document(self):
        if not self.version or not self.version.document_id:
            return None
        document = self.version.document_id
        return {
            'name': document.name or '',
            'mimetype': document.mimetype or '',
        }
