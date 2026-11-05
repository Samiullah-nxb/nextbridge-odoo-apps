# Part of NextContract. See LICENSE file for full copyright and licensing details.
"""
Contract Text Parser

Recovers the clause structure of a contract from its own text.

A contract that was uploaded or negotiated as a file (rather than assembled
from the clause library) carries its structure only as typography: numbered
headings, article titles, lettered sub-paragraphs. A PDF text layer flattens
all of that into hard-wrapped lines. This module reads those lines back into
headings, paragraphs and list items so the final export can render the real
contract rather than a summary of it.

It never adds a heading the source does not contain, never reorders content and
never rewrites wording. Where no headings can be recognised the text is
returned as one continuous body — an unstructured contract stays unstructured
rather than being given an invented skeleton.
"""

import re
from collections import Counter

# "1.", "2.3", "10.1.4" followed by a title.
_NUMBERED_HEADING = re.compile(r'^(\d+(?:\.\d+)*)[.)]?\s+(\S.*?)\s*$')

# "ARTICLE IV — Confidentiality", "SCHEDULE 2: Fees", "Section 5."
_KEYWORD_HEADING = re.compile(
    r'^(ARTICLE|SECTION|CLAUSE|SCHEDULE|ANNEX|ANNEXURE|APPENDIX|EXHIBIT|PART)'
    r'\s+([IVXLCDM]+|\d+(?:\.\d+)*)\b[.:)\-–—]?\s*(.*?)\s*$',
    re.IGNORECASE,
)

# "(a)", "(i)", "1)", "-", "•" at the start of a line.
_LIST_MARKER = re.compile(
    r'^(?:[-–—•*·]|\(\s*[a-zA-Z]\s*\)|\(\s*[ivxlcIVXLC]+\s*\)|[a-zA-Z]\)|\d+\))\s+'
)

# Running headers / footers that survive text extraction.
_PAGE_NOISE = re.compile(
    r'^(?:page\s*)?\d+\s*(?:/|of|\|)?\s*\d*\s*$|^-\s*\d+\s*-$', re.IGNORECASE
)

# Words allowed to stay lowercase inside a heading.
_HEADING_STOPWORDS = frozenset({
    'a', 'an', 'and', 'as', 'at', 'by', 'for', 'from', 'in', 'of', 'on', 'or',
    'the', 'to', 'with', 'under', 'upon', 'per', 'via', 'vs',
})

_RECITAL_MARKERS = (
    'whereas', 'this agreement', 'this deed', 'is entered', 'is made',
    'agree as follows', 'in consideration of', 'background',
)

_MAX_HEADING_CHARS = 90
# ALL-CAPS titles are short; shouted contract prose runs long.
_MAX_SHOUTED_HEADING_CHARS = 60
_MIN_HEADINGS_FOR_STRUCTURE = 2


def parse_contract_text(text):
    """Turn extracted contract text into ordered structural blocks.

    Returns a list of sections:
        [{'heading': str, 'level': int, 'blocks': [{'kind': 'p'|'li',
                                                    'text': str}]}]

    `heading` is '' for content that appears before the first recognised
    heading. `level` follows the source's own numbering depth.
    """
    lines = _clean_lines(text)
    if not lines:
        return []

    headings = _find_headings(lines)
    if len(headings) < _MIN_HEADINGS_FOR_STRUCTURE:
        blocks = _collect_blocks(lines)
        return [{'heading': '', 'level': 1, 'blocks': blocks}] if blocks else []

    sections = []
    current = {'heading': '', 'level': 1, 'lines': []}
    for index, line in enumerate(lines):
        heading = headings.get(index)
        if heading:
            if current['lines'] or current['heading']:
                sections.append(current)
            current = {'heading': heading[0], 'level': heading[1], 'lines': []}
        else:
            current['lines'].append(line)
    if current['lines'] or current['heading']:
        sections.append(current)

    parsed = []
    for section in sections:
        blocks = _collect_blocks(section['lines'])
        if not blocks and not section['heading']:
            continue
        heading = section['heading']
        if not heading and _looks_like_recitals(blocks):
            heading = 'Recitals'
        parsed.append({
            'heading': heading,
            'level': section['level'],
            'blocks': blocks,
        })
    return parsed


# -----------------------------------------------------------------------------
# Line hygiene
# -----------------------------------------------------------------------------

def _clean_lines(text):
    """Drop extraction artefacts: page numbers and repeated running titles."""
    if not text:
        return []
    raw = [line.replace('\f', ' ').rstrip() for line in text.splitlines()]
    repeated = {
        line.strip() for line, count in Counter(
            line.strip() for line in raw if 0 < len(line.strip()) <= 60
        ).items() if count >= 3
    }
    cleaned = []
    for line in raw:
        stripped = line.strip()
        if _PAGE_NOISE.match(stripped):
            continue
        # A repeated short line is a running header unless it is a heading,
        # which a contract may legitimately repeat in a table of contents.
        if stripped in repeated and not _match_heading(stripped):
            continue
        cleaned.append(stripped)
    while cleaned and not cleaned[0]:
        cleaned.pop(0)
    while cleaned and not cleaned[-1]:
        cleaned.pop()
    return cleaned


# -----------------------------------------------------------------------------
# Heading recognition
# -----------------------------------------------------------------------------

def _find_headings(lines):
    """Map line index → (heading_text, level) for every heading in `lines`."""
    headings = {}
    for index, line in enumerate(lines):
        heading = _numbered_or_keyword_heading(line)
        if heading:
            headings[index] = heading
    # ALL-CAPS lines are ambiguous — a title and a shouted disclaimer look the
    # same — so they are resolved with the surrounding lines in hand.
    for index, line in enumerate(lines):
        if index in headings:
            continue
        if _is_shouted_heading(lines, index, headings):
            headings[index] = (line.strip().rstrip('.'), 1)
    return headings


def _match_heading(line):
    """Heading test for a line considered on its own (used by line hygiene)."""
    return _numbered_or_keyword_heading(line)


def _numbered_or_keyword_heading(line):
    stripped = line.strip()
    if not stripped or len(stripped) > _MAX_HEADING_CHARS:
        return None

    keyword = _KEYWORD_HEADING.match(stripped)
    if keyword:
        label, number, title = keyword.groups()
        heading = '%s %s' % (label.upper(), number.upper())
        # The source may already separate the title ("ARTICLE IV — Scope"); the
        # regex only consumes one separator, so drop any that remain.
        title = title.lstrip(' .:)-–—')
        if title:
            heading = '%s — %s' % (heading, title.rstrip('.'))
        return heading, 1

    numbered = _NUMBERED_HEADING.match(stripped)
    if numbered:
        number, title = numbered.groups()
        if _is_heading_phrase(title):
            return '%s. %s' % (number, title.rstrip('.')), number.count('.') + 1
    return None


def _is_heading_phrase(title):
    """A heading names a thing; it does not make a statement."""
    title = title.strip()
    if not title or len(title) > _MAX_HEADING_CHARS:
        return False
    # A sentence that continues past its own full stop is body text.
    if '. ' in title.rstrip('.'):
        return False
    if title.endswith((',', ';', ':')):
        return False
    words = [word for word in re.split(r'\s+', title) if word]
    if not words or len(words) > 12:
        return False
    significant = [
        word for word in words
        if word.strip('.,;:()"\'').lower() not in _HEADING_STOPWORDS
        and any(char.isalpha() for char in word)
    ]
    if not significant:
        return False
    capitalised = sum(1 for word in significant if word.lstrip('("\'')[:1].isupper())
    return capitalised == len(significant)


def _is_shouted_heading(lines, index, headings):
    """Is the ALL-CAPS line at `index` a title, or a shouted clause?

    Legal drafting shouts whole disclaimers in capitals ("NO WARRANTIES ARE
    MADE BY EITHER PARTY..."), which a text layer wraps into short lines that
    look exactly like titles. Two contextual rules separate them:

      * a title never immediately follows another heading — text directly under
        "10. Warranty." is that clause's body, however loudly it is set;
      * a title starts a block, so the line before it is blank or has ended its
        sentence.
    """
    line = lines[index].strip()
    if len(line) > _MAX_SHOUTED_HEADING_CHARS or _LIST_MARKER.match(line):
        return False
    letters = [char for char in line if char.isalpha()]
    if len(letters) < 3 or not all(char.isupper() for char in letters):
        return False
    if line.endswith((',', ';', ':')):
        return False

    previous_index = index - 1
    while previous_index >= 0 and not lines[previous_index].strip():
        previous_index -= 1
    if previous_index < 0:
        return True
    if previous_index in headings:
        return False
    return lines[previous_index].rstrip().endswith(('.', '!', '?'))


# -----------------------------------------------------------------------------
# Paragraph assembly
# -----------------------------------------------------------------------------

def _collect_blocks(lines):
    """Reflow hard-wrapped lines into paragraphs and list items."""
    blocks = []
    buffer = []
    kind = 'p'

    def flush():
        if buffer:
            text = _join_wrapped(buffer)
            if text:
                blocks.append({'kind': kind, 'text': text})
        buffer.clear()

    for line in lines:
        if not line:
            flush()
            kind = 'p'
            continue
        if _LIST_MARKER.match(line):
            flush()
            kind = 'li'
            buffer.append(_LIST_MARKER.sub('', line, count=1))
            continue
        buffer.append(line)
    flush()
    return blocks


def _join_wrapped(lines):
    """Join wrapped lines, repairing words split across a line break."""
    joined = ''
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if not joined:
            joined = line
        elif joined.endswith('-') and joined[-2:-1].isalpha():
            joined = joined[:-1] + line
        else:
            joined = '%s %s' % (joined, line)
    return re.sub(r'\s{2,}', ' ', joined).strip()


def _looks_like_recitals(blocks):
    opening = ' '.join(block['text'] for block in blocks[:2]).lower()
    return any(marker in opening for marker in _RECITAL_MARKERS)
