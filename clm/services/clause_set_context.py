# Part of NextContract. See LICENSE file for full copyright and licensing details.
"""
Controlled Clause Set Jinja/docxtpl context + catalog helpers.

Supports:
  {{ clause_set }}
  {{ clause_set.clauses.<code>.title|content|description|... }}
  {% for clause in clause_set.clauses %} ... {% endfor %}

Does not replace the existing ``sections`` / ``section.clauses`` loop.
"""

from __future__ import annotations

import re

CLAUSE_SET_PROPERTIES = frozenset({
    'key',
    'title',
    'content',
    'description',
    'category',
    'editability',
    'risk_level',
    'required',
    'is_locked',
    'is_restricted',
})

_CODE_RE = re.compile(r'^[a-z][a-z0-9_]*$')
_CLAUSE_SET_EXPR_RE = re.compile(
    r'^clause_set'
    r'(?:'
    r'\.clauses'
    r'(?:'
    r'\.([a-z][a-z0-9_]*)'
    r'(?:\.([a-z][a-z0-9_]*))?'
    r')?'
    r')?$'
)


def slugify_clause_code(value):
    text = (value or '').strip().lower()
    text = re.sub(r'[^a-z0-9]+', '_', text)
    text = re.sub(r'_+', '_', text).strip('_')
    if not text:
        return 'clause'
    if text[0].isdigit():
        text = 'c_%s' % text
    return text[:64]


def is_valid_clause_code(code):
    return bool(code and _CODE_RE.match(code))


class ClauseProps(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as err:
            raise AttributeError(name) from err


class ClauseMap(dict):
    def __iter__(self):
        return iter(self.values())

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as err:
            raise AttributeError(name) from err


class ClauseSetContext:
    def __init__(self, clauses, full_text=''):
        self.clauses = clauses if isinstance(clauses, ClauseMap) else ClauseMap(clauses)
        self._full_text = full_text or ''

    def __str__(self):
        return self._full_text

    def __html__(self):
        return self._full_text

    def __bool__(self):
        return bool(self.clauses)


def clause_category_label(clause):
    """Human-readable category label for templates (not the technical key)."""
    if not clause or not clause.category:
        return ''
    selection = clause._fields['category'].selection
    if callable(selection):
        selection = selection(clause)
    return dict(selection or {}).get(clause.category, clause.category)


def clause_props_from_row(row, *, strip_html=None):
    code = (row.get('code') or '').strip()
    content = row.get('content') or ''
    if strip_html:
        content = strip_html(content)
    editability = row.get('editability') or 'editable'
    risk = row.get('risk_level') or ''
    return ClauseProps({
        'key': code,
        'title': row.get('title') or '',
        'content': content,
        'description': row.get('description') or '',
        'category': row.get('category') or '',
        'editability': editability,
        'risk_level': risk,
        'required': bool(row.get('required')),
        'is_locked': editability == 'locked',
        'is_restricted': editability == 'restricted',
    })


def build_clause_set_from_sections(sections, *, strip_html=None):
    clause_map = ClauseMap()
    parts = []
    for section in sections or []:
        for row in section.get('clauses') or []:
            props = clause_props_from_row(row, strip_html=strip_html)
            parts.append('%s\n%s' % (props['title'], props['content']))
            if props['key']:
                clause_map[props['key']] = props
    return ClauseSetContext(clause_map, '\n\n'.join(p for p in parts if p.strip()))


def parse_clause_set_expression(expr):
    expr = (expr or '').strip()
    match = _CLAUSE_SET_EXPR_RE.match(expr)
    if not match:
        if expr == 'clause_set' or expr.startswith('clause_set.'):
            return 'invalid', None, None
        return None, None, None
    key, prop = match.group(1), match.group(2)
    if key is None and prop is None:
        if expr == 'clause_set':
            return 'root', None, None
        return 'clauses', None, None
    if prop is None:
        return 'clause', key, None
    return 'property', key, prop


def validate_clause_set_expression(expr, available_keys=None):
    kind, key, prop = parse_clause_set_expression(expr)
    if kind is None:
        return None
    if kind == 'invalid':
        return (
            'Unsupported Clause Set expression {{ %s }}. '
            'Use {{ clause_set }} or '
            '{{ clause_set.clauses.<clause_key>.<property> }}.'
        ) % expr
    if kind in ('root', 'clauses'):
        return None
    if available_keys is not None and key not in available_keys:
        return 'Unknown clause key: "%s".' % key
    if kind == 'clause':
        return None
    if prop not in CLAUSE_SET_PROPERTIES:
        return 'Unsupported clause property: "%s".' % prop
    return None


def _line_clause_code(line):
    clause = line.clause_id
    if not clause:
        return ''
    if 'code' in clause._fields and (clause.code or '').strip():
        return clause.code.strip()
    return (clause.category or '').strip()


def catalog_entries_for_lines(clause_lines):
    """UI rows for the Clause Set sidebar (group: Clause Set)."""
    seen = set()
    for line in clause_lines:
        code = _line_clause_code(line)
        if not code or code in seen:
            continue
        seen.add(code)
        title = line.label or line.clause_id.name or code
        group = 'Clause Set'
        base = 'clause_set.clauses.%s' % code
        yield {
            'key': base,
            'label': '%s (specific clause)' % title,
            'insert': '{{ %s }}' % base,
            'group': group,
            'clause_key': code,
            'clause_title': title,
        }
        for prop, label in (
            ('title', 'Title'),
            ('category', 'Category'),
            ('content', 'Content'),
            ('description', 'Description'),
            ('risk_level', 'Risk level'),
            ('key', 'Key'),
        ):
            expr = '%s.%s' % (base, prop)
            yield {
                'key': expr,
                'label': '%s → %s' % (title, label),
                'insert': '{{ %s }}' % expr,
                'group': group,
                'clause_key': code,
                'clause_title': title,
                'property': prop,
            }


def compact_insert_lines_for_lines(clause_lines):
    """
    Compact dump per enabled clause: title, category, content
    (blank line between clauses). Used by \"Insert all clauses\".
    """
    lines = []
    seen = set()
    for line in clause_lines:
        code = _line_clause_code(line)
        if not code or code in seen:
            continue
        seen.add(code)
        if lines:
            lines.append('')
        lines.append('{{ clause_set.clauses.%s.title }}' % code)
        lines.append('{{ clause_set.clauses.%s.category }}' % code)
        lines.append('{{ clause_set.clauses.%s.content }}' % code)
    return lines
