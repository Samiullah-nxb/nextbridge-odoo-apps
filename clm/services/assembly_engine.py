# Part of NextContract. See LICENSE file for full copyright and licensing details.
"""
Draft Assembly Engine

Resolves variables, orders sections, collects enabled clause content,
and evaluates conditional sections using Odoo domain syntax.

Phase 3: PlainTextRenderer
Phase 4: DocxRenderer / PdfRenderer (same context interface)

Clause source, in order:
  1. the contract's own clause wording (clm.contract.clause), once the
     contract has been drafted or edited — that is what the contract says;
  2. otherwise the template's clause lines from the clause library.

Assembly context structure:
    {
        'contract': <clm.contract record>,
        'template': <clm.template record>,
        'variables': {'partner_name': 'Acme Corp', ...},
        'clause_versions': {clause_id: version_number, ...},  # audit snapshot
        'sections': [
            {
                'title': 'Confidentiality',
                'clauses': [
                    {
                        'title': 'Standard Confidentiality',
                        'content': '<p>...</p>',
                        'required': True,
                        'editability': 'locked',
                        'risk_level': 'high',
                        'condition_enabled': False,
                        'condition_domain': None,
                        'condition_description': None,
                    }
                ]
            },
            ...
        ]
    }
"""

import re

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tools.safe_eval import safe_eval

from odoo.addons.clm.services.clause_set_context import clause_category_label

PLACEHOLDER_PATTERN = re.compile(r'\{\{(\s*\w+\s*)\}\}')


def readable_field_values(contract):
    """The contract's custom field values, or none when the user may not read them.

    Roles such as Read-Only can read a contract without access to its custom
    field values; their preview then simply omits those terms.
    """
    if 'field_value_ids' not in contract._fields:
        return []
    try:
        contract.env['clm.contract.field.value'].check_access('read')
    except AccessError:
        return contract.env['clm.contract.field.value']
    return contract.field_value_ids


def can_read_field(record, field_name):
    """True when the current user may read `field_name` (field-level groups)."""
    field = record._fields.get(field_name)
    if not field:
        return False
    if not field.groups or record.env.su:
        return True
    return record.env.user.has_groups(field.groups)


class DraftAssemblyEngine:
    """
    Assembles a draft from a template + contract.

    Usage:
        engine = DraftAssemblyEngine(template, contract)
        context = engine.assemble()

    `use_contract_clauses=False` forces the template's library clauses even when
    the contract carries its own wording (used to (re)load a template).
    """

    def __init__(self, template, contract, use_contract_clauses=True):
        self.template = template
        self.contract = contract
        self.use_contract_clauses = use_contract_clauses

    def assemble(self, substitute=True):
        """Build and return the assembly context dict.

        `substitute=False` keeps {{placeholders}} in clause content, for
        callers that freeze the raw wording alongside the variable values.
        """
        variables = self._resolve_variables()
        clause_versions = {}
        sections = self._collect_sections(variables, clause_versions, substitute=substitute)
        return {
            'contract': self.contract,
            'template': self.template,
            'variables': variables,
            'clause_versions': clause_versions,  # audit snapshot
            'sections': sections,
        }

    # -------------------------------------------------------------------------
    # Variable resolution
    # -------------------------------------------------------------------------

    def _resolve_variables(self):
        """
        Resolve all template variables against the contract record.
        Returns a dict: {variable_name: resolved_value}.
        """
        variables = {}
        env = self.contract.env
        if self.template and 'clm.template.variable' in env:
            var_records = env['clm.template.variable'].search([
                ('template_id', '=', self.template.id)
            ], order='sequence asc')
            for var in var_records:
                variables[var.name] = self._resolve_field_path(var.field_path) \
                    or var.default_value or ''

        # Core contract fields always available
        contract = self.contract
        variables.setdefault('contract_name', contract.name or '')
        variables.setdefault(
            'partner_name',
            contract.partner_id.name if contract.partner_id else '',
        )
        variables.setdefault(
            'effective_date',
            str(contract.effective_date) if contract.effective_date else '',
        )
        variables.setdefault(
            'expiry_date',
            str(contract.expiry_date) if contract.expiry_date else '',
        )
        variables.setdefault(
            'department',
            contract.department_id.name if contract.department_id else '',
        )
        variables.setdefault(
            'legal_owner',
            contract.legal_owner_id.name if contract.legal_owner_id else '',
        )
        variables.setdefault('today', str(fields.Date.context_today(contract)))
        for key, value in self._party_variables().items():
            variables.setdefault(key, value)
        for key, value in self._contract_term_variables().items():
            variables.setdefault(key, value)
        return variables

    def _party_variables(self):
        """Counterparty and company identity / address placeholders."""
        contract = self.contract
        partner = contract.partner_id
        company = contract.company_id.partner_id if contract.company_id else False

        def _addr(partner_rec):
            if not partner_rec:
                return ''
            # Prefer Odoo’s formatted address; fall back to joined parts.
            formatted = (partner_rec.contact_address or '').strip()
            if formatted:
                return re.sub(r'\n+', ', ', formatted).strip(' ,')
            parts = [
                partner_rec.street or '',
                partner_rec.street2 or '',
                partner_rec.city or '',
                partner_rec.zip or '',
                partner_rec.country_id.name if partner_rec.country_id else '',
            ]
            return ', '.join(p for p in parts if p)

        def _part(partner_rec, attr, related=None):
            if not partner_rec:
                return ''
            if related:
                rec = getattr(partner_rec, related, False)
                return rec.name if rec else ''
            return getattr(partner_rec, attr, '') or ''

        signatory = False
        if 'partner_signatory_id' in contract._fields:
            signatory = contract.partner_signatory_id

        return {
            'partner_address': _addr(partner),
            'partner_street': _part(partner, 'street'),
            'partner_city': _part(partner, 'city'),
            'partner_zip': _part(partner, 'zip'),
            'partner_country': _part(partner, None, related='country_id'),
            'partner_email': _part(partner, 'email'),
            'partner_phone': _part(partner, 'phone') or _part(partner, 'mobile'),
            'partner_vat': _part(partner, 'vat'),
            'company_address': _addr(company),
            'company_street': _part(company, 'street'),
            'company_city': _part(company, 'city'),
            'company_zip': _part(company, 'zip'),
            'company_country': _part(company, None, related='country_id'),
            'company_email': _part(company, 'email'),
            'company_phone': _part(company, 'phone') or _part(company, 'mobile'),
            'company_vat': _part(company, 'vat'),
            'company_signature_label': (
                contract.company_id.name if contract.company_id else ''
            ),
            'partner_signature_label': partner.name if partner else '',
            'company_signatory': (
                (contract.legal_owner_id.name if contract.legal_owner_id else '')
                or (contract.user_id.name if contract.user_id else '')
            ),
            # Prefer Counterparty Signatory (person); fall back to Counterparty.
            'partner_signatory': (
                (signatory.name if signatory else '')
                or (partner.name if partner else '')
            ),
            'company_signatory_title': self._signatory_title(
                contract.legal_owner_id or contract.user_id
            ),
            'partner_signatory_title': self._partner_job_title(
                signatory or partner
            ),
        }

    def _partner_job_title(self, partner):
        """Best-effort job title for a partner (function / HR job / user)."""
        if not partner:
            return ''
        if partner.function:
            return partner.function
        env = partner.env
        Employee = env['hr.employee'] if 'hr.employee' in env else None
        if Employee is not None:
            emp = Employee.sudo().search([
                '|',
                ('work_contact_id', '=', partner.id),
                ('user_id.partner_id', '=', partner.id),
            ], limit=1)
            if emp:
                if emp.job_title:
                    return emp.job_title
                if emp.job_id and emp.job_id.name:
                    return emp.job_id.name
        user = env['res.users'].sudo().search(
            [('partner_id', '=', partner.id)], limit=1,
        )
        if user and getattr(user, 'function', None):
            return user.function or ''
        return ''

    def _signatory_title(self, user):
        """Job title for a user — printed in DOCX like Name (not a Sign widget)."""
        if not user:
            return ''
        partner_rec = user.partner_id if hasattr(user, 'partner_id') else False
        title = self._partner_job_title(partner_rec) if partner_rec else ''
        if title:
            return title
        return getattr(user, 'function', '') or ''

    def _format_signing_date(self, contract):
        """Locale date string for {{ signing_date }} — printed like Name."""
        from odoo.tools.misc import format_date
        day = contract.signing_date or fields.Date.context_today(contract)
        return format_date(contract.env, day) or ''

    def _contract_term_variables(self):
        """Structured contract terms exposed as {{placeholders}}.

        Clause wording that uses these placeholders always carries the same
        value as the contract field, so the document cannot say one thing in
        the terms table and another in the clause text.
        """
        contract = self.contract
        reference = contract.contract_ref if contract.contract_ref not in (False, 'New') else ''
        risk_level = ''
        risk_rating = ''
        if can_read_field(contract, 'risk_rating') and contract.risk_rating:
            risk_rating = dict(contract._fields['risk_rating'].selection).get(
                contract.risk_rating, contract.risk_rating
            )
            risk_level = risk_rating
        confidentiality = ''
        if (
            can_read_field(contract, 'confidentiality_level')
            and contract.confidentiality_level
        ):
            confidentiality = dict(contract._fields['confidentiality_level'].selection).get(
                contract.confidentiality_level, contract.confidentiality_level
            )
        values = {
            'contract_ref': reference or '',
            'contract_type': contract.contract_type_id.name or '',
            'company_name': contract.company_id.name or '',
            'business_owner': contract.user_id.name or '',
            'procurement_owner': (
                contract.procurement_owner_id.name if contract.procurement_owner_id else ''
            ),
            'finance_owner': (
                contract.finance_owner_id.name if contract.finance_owner_id else ''
            ),
            # Printed in the DOCX like Name (not an Odoo Sign overlay). Default
            # to today when unset so Signature Draft / Send for Signature show a date.
            'signing_date': self._format_signing_date(contract),
            'renewal_date': str(contract.renewal_date) if contract.renewal_date else '',
            'notice_period_days': (
                str(contract.notice_period_days) if contract.notice_period_days else ''
            ),
            'notice_period': (
                '%s days' % contract.notice_period_days if contract.notice_period_days else ''
            ),
            'auto_renewal': 'Yes' if contract.auto_renewal else 'No',
            'termination_for_convenience': (
                'Yes' if contract.termination_for_convenience else 'No'
            ),
            'currency': contract.currency_id.name or '',
            'risk_level': risk_level,
            'risk_rating': risk_rating,
            'confidentiality_level': confidentiality,
        }
        if can_read_field(contract, 'contract_value'):
            values['contract_value'] = (
                '{:,.2f}'.format(contract.contract_value) if contract.contract_value else ''
            )
        for value in readable_field_values(contract):
            code = value.definition_id.code
            if code:
                values.setdefault(code, value.display_value or '')
        return values

    def _resolve_field_path(self, field_path):
        """Traverse a dot-notation path on self.contract and return the string value."""
        if not field_path:
            return None
        try:
            obj = self.contract
            for part in field_path.split('.'):
                obj = getattr(obj, part, None)
                if obj is None:
                    return None
            return str(obj) if obj else None
        except Exception:
            return None

    # -------------------------------------------------------------------------
    # Section and clause collection
    # -------------------------------------------------------------------------

    def _uses_contract_clauses(self):
        return (
            self.use_contract_clauses
            and 'clause_ids' in self.contract._fields
            and bool(self.contract.sudo().clause_ids)
        )

    def _template_clause_rows(self):
        if not self.template:
            return []
        rows = []
        for line in self.template.clause_line_ids.filtered(lambda l: l.enabled):
            clause = line.clause_id
            rows.append({
                'section': line.section or 'General',
                'title': line.label or clause.name,
                'content': clause.standard_wording or '',
                'description': clause.description or '',
                'category': clause_category_label(clause),
                'code': clause.code or clause.category or '',
                'required': line.required,
                'editability': clause.editability,
                'risk_level': clause.risk_level,
                'condition_enabled': line.condition_enabled,
                'condition_domain': line.condition_domain,
                'condition_description': line.condition_description,
                'clause_id': clause.id,
                'clause_version': clause.version,
                'source': 'template',
            })
        return rows

    def _contract_clause_rows(self):
        # sudo: the clause lines are the wording of a contract the caller has
        # already opened; roles without direct access to the line model still
        # need to see what their contract says.
        rows = []
        for line in self.contract.sudo().clause_ids.sorted('sequence'):
            clause = line.clause_id
            rows.append({
                'section': line.section or '',
                'title': line.title or clause.name or '',
                'content': line.content or '',
                'description': (clause.description if clause else '') or '',
                'category': clause_category_label(clause) if clause else '',
                'code': ((clause.code if clause else '') or (clause.category if clause else '') or ''),
                'required': line.required,
                'editability': clause.editability or 'editable',
                'risk_level': clause.risk_level,
                'condition_enabled': False,
                'condition_domain': False,
                'condition_description': False,
                'clause_id': clause.id or False,
                'clause_version': line.clause_version or clause.version or 0,
                'source': line.source or 'custom',
            })
        return rows

    def _collect_sections(self, variables, clause_versions, substitute=True):
        """
        Group enabled clause lines by section, ordered by sequence.
        Evaluates condition fields using Odoo domain syntax.
        Returns a list of section dicts.
        """
        if self._uses_contract_clauses():
            rows = self._contract_clause_rows()
        else:
            rows = self._template_clause_rows()

        section_map = {}
        section_order = []
        for row in rows:
            # Evaluate condition (Odoo domain against the contract) if enabled
            if row['condition_enabled'] and row['condition_domain']:
                if not self._evaluate_condition(row['condition_domain']):
                    continue

            section_key = row['section']
            if section_key not in section_map:
                section_map[section_key] = []
                section_order.append(section_key)

            content = row['content']
            if substitute:
                content = self._substitute_variables(content, variables)

            # Capture clause version for audit snapshot
            if row['clause_id']:
                clause_versions[row['clause_id']] = row['clause_version']

            section_map[section_key].append({
                'title': row['title'],
                'content': content,
                'description': row.get('description') or '',
                'category': row.get('category') or '',
                'code': row.get('code') or '',
                'required': row['required'],
                'editability': row['editability'],
                'risk_level': row['risk_level'],
                'condition_enabled': row['condition_enabled'],
                'condition_domain': row['condition_domain'],
                'condition_description': row['condition_description'],
                'clause_id': row['clause_id'],
                'clause_version': row['clause_version'],
                'source': row['source'],
            })

        return [
            {'title': title, 'clauses': section_map[title]}
            for title in section_order
        ]

    def _evaluate_condition(self, condition_str):
        """
        Evaluate a condition string as an Odoo domain against self.contract.
        Returns True if the contract matches, False if it doesn't.
        Silently returns True (include clause) on parse errors to fail-open.

        Example condition: [('contract_type_id.code', '=', 'nda')]
        """
        if not condition_str:
            return True
        try:
            domain = safe_eval(condition_str, {'__builtins__': {}})
            if not isinstance(domain, list):
                return True
            # An unsaved contract (editor preview) is matched on its stored values.
            record_id = self.contract._origin.id
            if not record_id:
                return True
            # Check if the contract matches the domain
            match = self.contract.search_count(
                [('id', '=', record_id)] + domain
            )
            return match > 0
        except Exception:
            return True  # fail-open: include clause if condition can't be parsed

    @staticmethod
    def _substitute_variables(text, variables):
        """Replace {{variable_name}} placeholders with resolved values."""
        if not text:
            return text

        def replacer(match):
            key = match.group(1).strip()
            return variables.get(key, match.group(0))

        return PLACEHOLDER_PATTERN.sub(replacer, text)
