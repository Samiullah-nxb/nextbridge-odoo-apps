# Part of NextContract. See LICENSE file for full copyright and licensing details.
"""
Plain Text Renderer (Phase 3)

Converts a DraftAssemblyEngine context into a human-readable plain-text string.

Phase 4 will add DocxRenderer and PdfRenderer using the same context interface.
The assembly engine and renderer are intentionally decoupled:

    DraftAssemblyEngine  →  context  →  PlainTextRenderer   (Phase 3)
                                    →  DocxRenderer         (Phase 4)
                                    →  PdfRenderer          (Phase 4)
"""

import re


class PlainTextRenderer:
    """Renders an assembly context as a plain-text contract preview."""

    LOCK_NOTICE = '[LOCKED CLAUSE — Modification requires Legal Admin authorisation]'
    RESTRICTED_NOTICE = '[RESTRICTED CLAUSE]'

    def render(self, context):
        """
        Accept a context dict from DraftAssemblyEngine and return a plain-text string.
        """
        contract = context['contract']
        template = context['template']
        sections = context['sections']

        lines = []
        lines.append('=' * 72)
        lines.append(f'CONTRACT DRAFT PREVIEW')
        lines.append(f'Template: {template.name} (v{template.version_number})')
        lines.append(f'Contract: {contract.name}')

        # Variable summary
        variables = context.get('variables', {})
        if variables:
            lines.append('')
            lines.append('VARIABLES')
            lines.append('-' * 40)
            for k, v in variables.items():
                lines.append(f'  {k}: {v}')

        lines.append('=' * 72)
        lines.append('')

        # Sections and clauses
        for section in sections:
            lines.append(section['title'].upper())
            lines.append('-' * len(section['title']))
            lines.append('')
            for clause in section['clauses']:
                title_prefix = '[OPTIONAL] ' if not clause['required'] else ''
                lines.append(f'{title_prefix}{clause["title"]}')
                if clause['editability'] == 'locked':
                    lines.append(f'  {self.LOCK_NOTICE}')
                elif clause['editability'] == 'restricted':
                    lines.append(f'  {self.RESTRICTED_NOTICE}')
                # Strip HTML tags for plain-text output
                plain_content = self._strip_html(clause['content'])
                if plain_content:
                    for para in plain_content.strip().split('\n'):
                        if para.strip():
                            lines.append(f'  {para.strip()}')
                lines.append('')
            lines.append('')

        lines.append('=' * 72)
        lines.append('[END OF DRAFT PREVIEW — Phase 3 Plain Text]')
        lines.append('[Phase 4 will produce a formatted DOCX/PDF document]')
        lines.append('=' * 72)

        return '\n'.join(lines)

    @staticmethod
    def _strip_html(html):
        """Remove HTML tags, returning plain text."""
        import html as html_lib
        if not html:
            return ''
        text = str(html)
        if '&lt;' in text and re.search(
            r'&lt;/?\s*(?:p|div|li|ul|ol|br|h[1-6]|span|blockquote)\b', text, re.I
        ):
            text = html_lib.unescape(text)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'&nbsp;', ' ', text)
        text = re.sub(r'&amp;', '&', text)
        text = re.sub(r'&lt;', '<', text)
        text = re.sub(r'&gt;', '>', text)
        text = re.sub(r'\s+', ' ', text)
        return text.strip()
