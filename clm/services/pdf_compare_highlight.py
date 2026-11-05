# Part of NextContract. See LICENSE file for full copyright and licensing details.
"""Paint red/green change marks into comparison PDFs.

Original PDF: red overlays on removed wording.
Revised PDF: green overlays on added wording.

Deleted/inserted clauses are located as *contiguous line blocks* so a full
Force Majeure removal paints end-to-end, without also marking the same wording
elsewhere (e.g. "neither Party is liable" under Limitation of Liability).
"""

from __future__ import annotations

import io
import logging
import re

_logger = logging.getLogger(__name__)

# Soft highlighter pastels painted *under* page text (see _burn_rects_into_pdf).
COLOR_DELETE = (1.0, 0.78, 0.78)   # soft pink
COLOR_ADD = (0.72, 0.93, 0.78)     # soft mint

_WS_RE = re.compile(r'\s+')

_SKIP_SINGLE_TOKENS = frozenset({
    'shall', 'which', 'under', 'their', 'there', 'these', 'those', 'about',
    'agreement', 'contract', 'party', 'parties', 'section', 'clause',
    'software', 'license', 'nextdocs', 'saas', 'provider', 'recipient',
    'company', 'client', 'vendor', 'services', 'service', 'between',
    'hereinafter', 'including', 'without', 'limitation', 'effective',
    'dated', 'page', 'appendix', 'schedule', 'exhibit', 'annex',
})

_SKIP_PHRASES = frozenset({
    'software license',
    'software / saas',
    'software license and agreement',
    'nextdocs',
})


def annotate_pdf_highlights(pdf_bytes, phrases, color=COLOR_DELETE, max_hits_per_phrase=1):
    """Return PDF bytes with marks for isolated word-level phrases (replacements)."""
    raw = bytes(pdf_bytes or b'')
    if not raw.startswith(b'%PDF'):
        return raw
    cleaned = []
    seen = set()
    for phrase in phrases or []:
        text = _normalize_phrase(phrase)
        if not text or text in seen or len(text) < 2:
            continue
        if not _is_highlightable_phrase(text):
            continue
        seen.add(text)
        cleaned.append(text)
    if not cleaned:
        return raw

    try:
        import pypdfium2 as pdfium
    except ImportError as error:
        _logger.warning('NextContract: PDF highlight deps missing: %s', error)
        return raw

    try:
        page_rects = _find_phrase_rects(pdfium, raw, cleaned, max_hits_per_phrase)
    except Exception:  # noqa: BLE001
        _logger.exception('NextContract: PDF text locate failed')
        return raw
    if not any(page_rects.values()):
        return raw
    try:
        return _burn_rects_into_pdf(raw, page_rects, color)
    except Exception:  # noqa: BLE001
        _logger.exception('NextContract: PDF highlight burn failed')
        return raw


def annotate_pdf_blocks(pdf_bytes, blocks, color=COLOR_DELETE):
    """Paint each contiguous deleted/inserted line block exactly once."""
    raw = bytes(pdf_bytes or b'')
    if not raw.startswith(b'%PDF'):
        return raw
    cleaned_blocks = []
    for block in blocks or []:
        lines = [_normalize_phrase(line) for line in (block or []) if _normalize_phrase(line)]
        if lines:
            cleaned_blocks.append(lines)
    if not cleaned_blocks:
        return raw

    try:
        import pypdfium2 as pdfium
    except ImportError as error:
        _logger.warning('NextContract: PDF highlight deps missing: %s', error)
        return raw

    try:
        page_rects = _find_block_rects(pdfium, raw, cleaned_blocks)
    except Exception:  # noqa: BLE001
        _logger.exception('NextContract: PDF block locate failed')
        return raw
    if not any(page_rects.values()):
        return raw
    try:
        return _burn_rects_into_pdf(raw, page_rects, color)
    except Exception:  # noqa: BLE001
        _logger.exception('NextContract: PDF block burn failed')
        return raw


def build_pdf_highlights(base_pdf_bytes, target_pdf_bytes, result=None):
    """Return (base_marked_pdf, target_marked_pdf, gutter_markers).

    ``gutter_markers`` drives the Draftable-style middle rail (red/green ticks).
    Each marker: ``{kind: 'del'|'add', top: 0-1, height: 0-1, page: int}``.

    Ticks are derived from the *same* rectangles that were burned into the PDF,
    so a green tick always means a green highlight exists in the Revised pane
    (and red for Original).
    """
    removed_blocks, added_blocks, rem_phrases, add_phrases = diff_pdf_changes(
        base_pdf_bytes, target_pdf_bytes,
    )
    if result and not result.get('identical'):
        eng_rem, eng_add = phrases_from_comparison_result(
            result, base_pdf_bytes=base_pdf_bytes, target_pdf_bytes=target_pdf_bytes,
        )
        rem_phrases = list(dict.fromkeys(list(rem_phrases) + list(eng_rem)))
        add_phrases = list(dict.fromkeys(list(add_phrases) + list(eng_add)))

    base_raw = bytes(base_pdf_bytes or b'')
    target_raw = bytes(target_pdf_bytes or b'')
    markers = []
    if removed_blocks:
        base_raw, block_markers = _paint_blocks(base_raw, removed_blocks, COLOR_DELETE, 'del')
        markers.extend(block_markers)
    if rem_phrases:
        base_raw, phrase_markers = _paint_phrases(base_raw, rem_phrases, COLOR_DELETE, 'del')
        markers.extend(phrase_markers)
    if added_blocks:
        target_raw, block_markers = _paint_blocks(target_raw, added_blocks, COLOR_ADD, 'add')
        markers.extend(block_markers)
    if add_phrases:
        target_raw, phrase_markers = _paint_phrases(target_raw, add_phrases, COLOR_ADD, 'add')
        markers.extend(phrase_markers)
    return base_raw, target_raw, _merge_gutter_markers(markers)


def _paint_blocks(pdf_bytes, blocks, color, kind):
    """Locate blocks once → burn highlights → return markers for those rects only."""
    raw = bytes(pdf_bytes or b'')
    if not raw.startswith(b'%PDF') or not blocks:
        return raw, []
    cleaned = []
    for block in blocks:
        lines = [_normalize_phrase(line) for line in (block or []) if _normalize_phrase(line)]
        if lines:
            cleaned.append(lines)
    if not cleaned:
        return raw, []
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return raw, []
    try:
        page_rects = _find_block_rects(pdfium, raw, cleaned)
    except Exception:  # noqa: BLE001
        _logger.exception('NextContract: PDF block locate failed')
        return raw, []
    if not any(page_rects.values()):
        return raw, []
    markers = _rects_to_gutter_markers(pdfium, raw, page_rects, kind)
    try:
        return _burn_rects_into_pdf(raw, page_rects, color), markers
    except Exception:  # noqa: BLE001
        _logger.exception('NextContract: PDF block burn failed')
        return raw, []


def _paint_phrases(pdf_bytes, phrases, color, kind, max_hits_per_phrase=1):
    """Locate phrases once → burn highlights → return markers for those rects only."""
    raw = bytes(pdf_bytes or b'')
    if not raw.startswith(b'%PDF') or not phrases:
        return raw, []
    cleaned = []
    seen = set()
    for phrase in phrases:
        text = _normalize_phrase(phrase)
        if not text or text in seen or len(text) < 2:
            continue
        if not _is_highlightable_phrase(text):
            continue
        seen.add(text)
        cleaned.append(text)
    if not cleaned:
        return raw, []
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return raw, []
    try:
        page_rects = _find_phrase_rects(pdfium, raw, cleaned, max_hits_per_phrase)
    except Exception:  # noqa: BLE001
        _logger.exception('NextContract: PDF phrase locate failed')
        return raw, []
    if not any(page_rects.values()):
        return raw, []
    markers = _rects_to_gutter_markers(pdfium, raw, page_rects, kind)
    try:
        return _burn_rects_into_pdf(raw, page_rects, color), markers
    except Exception:  # noqa: BLE001
        _logger.exception('NextContract: PDF phrase burn failed')
        return raw, []


def _rects_to_gutter_markers(pdfium, pdf_bytes, page_rects, kind):
    pdf = pdfium.PdfDocument(pdf_bytes)
    markers = []
    try:
        heights = []
        for index in range(len(pdf)):
            page = pdf[index]
            try:
                heights.append(float(page.get_height() or 0) or 1.0)
            finally:
                try:
                    page.close()
                except Exception:  # noqa: BLE001
                    pass
        total = sum(heights) or 1.0
        offset = 0.0
        for page_index, page_h in enumerate(heights):
            for x0, y0, x1, y1 in page_rects.get(page_index) or []:
                # PDF y grows upward; convert highlight to distance from page top.
                top_from_page = max(0.0, page_h - float(y1))
                bottom_from_page = max(top_from_page, page_h - float(y0))
                span = max(bottom_from_page - top_from_page, 10.0)
                markers.append({
                    'kind': kind,
                    'page': page_index,
                    'top': (offset + top_from_page) / total,
                    'height': span / total,
                })
            offset += page_h
    finally:
        try:
            pdf.close()
        except Exception:  # noqa: BLE001
            pass
    return markers


def _merge_gutter_markers(markers, gap=0.008):
    """Collapse near-overlapping same-kind ticks into solid Draftable-style bars."""
    by_kind = {'del': [], 'add': []}
    for marker in markers or []:
        kind = marker.get('kind')
        if kind not in by_kind:
            continue
        by_kind[kind].append(dict(marker))
    merged = []
    for kind, items in by_kind.items():
        items.sort(key=lambda m: (m.get('top') or 0))
        current = None
        for item in items:
            top = float(item.get('top') or 0)
            height = max(float(item.get('height') or 0.004), 0.004)
            bottom = top + height
            if current is None:
                current = {
                    'kind': kind,
                    'top': top,
                    'height': height,
                    'page': item.get('page', 0),
                }
                continue
            cur_bottom = current['top'] + current['height']
            if top <= cur_bottom + gap:
                new_bottom = max(cur_bottom, bottom)
                current['height'] = new_bottom - current['top']
            else:
                merged.append(current)
                current = {
                    'kind': kind,
                    'top': top,
                    'height': height,
                    'page': item.get('page', 0),
                }
        if current:
            merged.append(current)
    merged.sort(key=lambda m: (m.get('top') or 0))
    return merged


def diff_pdf_changes(base_pdf_bytes, target_pdf_bytes):
    """Return (removed_blocks, added_blocks, removed_phrases, added_phrases).

    Pure line deletes/inserts become contiguous blocks.
    In-place replacements become short unique phrases only.
    """
    import difflib

    base_lines = _pdf_text_lines(base_pdf_bytes)
    target_lines = _pdf_text_lines(target_pdf_bytes)
    if not base_lines and not target_lines:
        return [], [], [], []

    removed_blocks, added_blocks = [], []
    removed_phrases, added_phrases = [], []
    matcher = difflib.SequenceMatcher(None, base_lines, target_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'equal':
            continue
        if tag == 'delete':
            block = [_normalize_phrase(l) for l in base_lines[i1:i2] if _normalize_phrase(l)]
            if block and not _is_branding_only_block(block):
                removed_blocks.append(block)
        elif tag == 'insert':
            block = [_normalize_phrase(l) for l in target_lines[j1:j2] if _normalize_phrase(l)]
            if block and not _is_branding_only_block(block):
                added_blocks.append(block)
        else:
            rem, add = _word_diff_phrases(
                ' '.join(base_lines[i1:i2]),
                ' '.join(target_lines[j1:j2]),
            )
            removed_phrases.extend(rem)
            added_phrases.extend(add)

    removed_phrases, added_phrases = _filter_shared_phrases(
        removed_phrases, added_phrases, base_pdf_bytes, target_pdf_bytes,
    )
    return removed_blocks, added_blocks, removed_phrases, added_phrases


def phrases_from_pdf_bytes(base_pdf_bytes, target_pdf_bytes):
    """Compatibility helper — flattened phrases from PDF-layer diff."""
    removed_blocks, added_blocks, rem_phrases, add_phrases = diff_pdf_changes(
        base_pdf_bytes, target_pdf_bytes,
    )
    removed = list(rem_phrases)
    added = list(add_phrases)
    for block in removed_blocks:
        removed.extend(block)
    for block in added_blocks:
        added.extend(block)
    return removed, added


def phrases_from_comparison_result(result, base_pdf_bytes=None, target_pdf_bytes=None):
    """Split a redline engine result into unique removed/added phrases only."""
    removed_raw, added_raw = [], []
    for hunk in result.get('hunks') or []:
        for row in hunk.get('rows') or []:
            text = (row.get('text') or '').strip()
            if not text:
                continue
            if row.get('kind') == 'del':
                removed_raw.append(text)
            elif row.get('kind') == 'add':
                added_raw.append(text)
    for change in result.get('changes') or []:
        removed_raw.extend(change.get('removed') or [])
        added_raw.extend(change.get('added') or [])
    removed, added = _unique_word_diff_phrases(removed_raw, added_raw)
    return _filter_shared_phrases(
        removed, added, base_pdf_bytes, target_pdf_bytes,
    )


def merge_highlight_phrases(
    pdf_removed, pdf_added, eng_removed, eng_added,
    base_pdf_bytes=None, target_pdf_bytes=None,
):
    """Combine PDF-layer and engine phrases, then drop anything still shared."""
    removed = list(pdf_removed or []) + list(eng_removed or [])
    added = list(pdf_added or []) + list(eng_added or [])
    return _filter_shared_phrases(
        removed, added, base_pdf_bytes, target_pdf_bytes,
    )


def _is_branding_only_block(lines):
    joined = ' '.join(lines).lower()
    if joined in _SKIP_PHRASES:
        return True
    # Page chrome / logo strips that shift when body length changes.
    footer_markers = (
        'nextdocs', 'confidential', 'software / saas', '▤', 'page ',
    )
    if any(m in joined for m in footer_markers):
        bodyish = [
            ln for ln in lines
            if not any(m in ln.lower() for m in footer_markers)
            and len(ln) >= 24
        ]
        if not bodyish:
            return True
    tokens = [
        tok.lower().strip('.,;:()[]"\'|·•▤◆')
        for tok in _WS_RE.split(joined) if tok
    ]
    meaningful = [
        tok for tok in tokens
        if tok and tok not in _SKIP_SINGLE_TOKENS and tok not in _SKIP_PHRASES
        and not tok.isdigit()
        and len(tok) > 1
    ]
    brandish = {'nextdocs', 'software', 'saas', 'confidential', 'page', '|', '·'}
    if meaningful and all(
        any(b in tok for b in brandish) or tok in brandish for tok in meaningful
    ):
        return True
    return False


def _unique_word_diff_phrases(removed_raw, added_raw):
    rem_joined = ' '.join(_normalize_phrase(t) for t in removed_raw if t)
    add_joined = ' '.join(_normalize_phrase(t) for t in added_raw if t)
    if rem_joined and add_joined:
        return _word_diff_phrases(rem_joined, add_joined)
    removed, added = [], []
    if rem_joined and not add_joined:
        removed.extend(_phrases_from_unique_span(rem_joined))
    if add_joined and not rem_joined:
        added.extend(_phrases_from_unique_span(add_joined))
    return removed, added


def _word_diff_phrases(base_text, target_text):
    import difflib

    base_words = _tokenize(base_text)
    target_words = _tokenize(target_text)
    removed, added = [], []
    matcher = difflib.SequenceMatcher(None, base_words, target_words, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'equal':
            continue
        if tag in ('replace', 'delete') and i1 != i2:
            removed.extend(_phrases_from_unique_span(
                _span_with_context(base_words, i1, i2),
            ))
        if tag in ('replace', 'insert') and j1 != j2:
            added.extend(_phrases_from_unique_span(
                _span_with_context(target_words, j1, j2),
            ))
    return removed, added


def _span_with_context(words, start, end):
    span_words = words[start:end]
    if len(span_words) == 1 and len(span_words[0]) <= 4:
        if start > 0:
            span_words = [words[start - 1]] + span_words
        elif end < len(words):
            span_words = span_words + [words[end]]
    return ' '.join(span_words)


def _tokenize(text):
    return [tok for tok in _WS_RE.split(_normalize_phrase(text)) if tok]


def _phrases_from_unique_span(span):
    text = _normalize_phrase(span)
    if not text or not _is_highlightable_phrase(text):
        return []
    phrases = [text]
    words = text.split(' ')
    if len(words) >= 4:
        for size in (4, 3):
            if len(words) < size:
                continue
            for index in range(0, len(words) - size + 1):
                window = ' '.join(words[index:index + size])
                if _is_highlightable_phrase(window):
                    phrases.append(window)
            break
    elif len(words) == 1:
        token = text.lower().strip('.,;:()[]"\'')
        if any(ch.isdigit() for ch in token) or len(token) >= 12:
            phrases.append(text)
        else:
            return []
    return phrases


def _is_highlightable_phrase(text):
    cleaned = _normalize_phrase(text)
    if not cleaned:
        return False
    words = cleaned.split(' ')
    if len(words) == 1:
        token = words[0].lower().strip('.,;:()[]"\'')
        if not token or not any(ch.isalnum() for ch in token):
            return False
        if any(ch.isdigit() for ch in token):
            return True
        if len(token) < 4 or token in _SKIP_SINGLE_TOKENS:
            return False
        return True
    joined = cleaned.lower()
    if joined in _SKIP_PHRASES:
        return False
    if any(skip in joined for skip in _SKIP_PHRASES):
        return False
    if len(cleaned) < 3:
        return False
    significant = [
        w for w in words
        if w.lower().strip('.,;:()[]"\'') not in _SKIP_SINGLE_TOKENS
        and any(ch.isalnum() for ch in w)
    ]
    return bool(significant)


def _filter_shared_phrases(removed, added, base_pdf_bytes=None, target_pdf_bytes=None):
    rem = list(dict.fromkeys(
        _normalize_phrase(p) for p in removed if _is_highlightable_phrase(p)
    ))
    add = list(dict.fromkeys(
        _normalize_phrase(p) for p in added if _is_highlightable_phrase(p)
    ))
    shared = {p.lower() for p in rem} & {p.lower() for p in add}
    rem = [p for p in rem if p.lower() not in shared]
    add = [p for p in add if p.lower() not in shared]

    base_lines = {_normalize_phrase(l).lower() for l in _pdf_text_lines(base_pdf_bytes)}
    target_lines = {_normalize_phrase(l).lower() for l in _pdf_text_lines(target_pdf_bytes)}
    base_hay = _pdf_haystack(base_pdf_bytes)
    target_hay = _pdf_haystack(target_pdf_bytes)
    if target_pdf_bytes:
        rem = [
            p for p in rem
            if not _phrase_still_on_other_side(
                p, target_lines, target_hay, own_lines=base_lines,
            )
        ]
    if base_pdf_bytes:
        add = [
            p for p in add
            if not _phrase_still_on_other_side(
                p, base_lines, base_hay, own_lines=target_lines,
            )
        ]
    return rem, add


def _phrase_still_on_other_side(phrase, other_lines, other_hay, own_lines=None):
    needle = _normalize_phrase(phrase).lower()
    if not needle:
        return True
    if needle in other_lines:
        return True
    if own_lines is not None and needle in own_lines:
        return False
    words = needle.split(' ')
    if len(words) >= 6 or len(needle) >= 40:
        return False
    return _phrase_in_haystack(phrase, other_hay)


def _pdf_haystack(pdf_bytes):
    if not pdf_bytes:
        return ''
    lines = _pdf_text_lines(pdf_bytes)
    return ' '.join(lines).lower() if lines else ''


def _phrase_in_haystack(phrase, haystack):
    needle = _normalize_phrase(phrase).lower()
    if not needle or not haystack:
        return False
    pattern = r'(?<!\w)%s(?!\w)' % re.escape(needle)
    return re.search(pattern, haystack) is not None


def _pdf_text_lines(pdf_bytes):
    raw = bytes(pdf_bytes or b'')
    if not raw.startswith(b'%PDF'):
        return []
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return []
    lines = []
    pdf = pdfium.PdfDocument(raw)
    try:
        for page_index in range(len(pdf)):
            page = pdf[page_index]
            try:
                textpage = page.get_textpage()
                try:
                    text = textpage.get_text_range() or ''
                finally:
                    textpage.close()
            except Exception:  # noqa: BLE001
                text = ''
            finally:
                try:
                    page.close()
                except Exception:  # noqa: BLE001
                    pass
            for line in text.splitlines():
                cleaned = _WS_RE.sub(' ', line).strip()
                if cleaned:
                    lines.append(cleaned)
    finally:
        try:
            pdf.close()
        except Exception:  # noqa: BLE001
            pass
    return lines


def _normalize_phrase(phrase):
    if not phrase:
        return ''
    return _WS_RE.sub(' ', str(phrase)).strip()


def _find_block_rects(pdfium, pdf_bytes, blocks):
    """Locate each line-block as an ordered sequence; one hit per block."""
    pdf = pdfium.PdfDocument(pdf_bytes)
    found = {index: [] for index in range(len(pdf))}
    try:
        for page_index in range(len(pdf)):
            page = pdf[page_index]
            try:
                textpage = page.get_textpage()
            except Exception:  # noqa: BLE001
                try:
                    page.close()
                except Exception:  # noqa: BLE001
                    pass
                continue
            try:
                for block in blocks:
                    rects = _locate_ordered_lines(textpage, block)
                    if rects:
                        found[page_index].extend(rects)
            finally:
                try:
                    textpage.close()
                except Exception:  # noqa: BLE001
                    pass
                try:
                    page.close()
                except Exception:  # noqa: BLE001
                    pass
    finally:
        try:
            pdf.close()
        except Exception:  # noqa: BLE001
            pass
    return found


def _locate_ordered_lines(textpage, lines):
    """Find lines in order on this page and return one rect per line.

    Searching is anchored: each next line must start at/after the previous
    match ended. That keeps a Force Majeure block together and avoids painting
    the same wording earlier under Limitation of Liability.
    """
    if not lines:
        return []
    rects = []
    cursor = 0
    for line in lines:
        hit = _search_line_after(textpage, line, cursor)
        if not hit:
            # Soft fallback: allow a slightly earlier overlap for hyphen/wrap noise.
            hit = _search_line_after(textpage, line, max(0, cursor - 40))
        if not hit:
            # Block broken — still keep what we already locked in order.
            break
        start, count, rect = hit
        rects.append(rect)
        cursor = start + count
    return rects


def _search_line_after(textpage, line, min_start):
    """Return (start, count, rect) for the first match at/after min_start."""
    candidates = _line_search_variants(line)
    best = None
    for needle in candidates:
        if len(needle) < 3:
            continue
        try:
            searcher = textpage.search(needle, match_case=False)
        except Exception:  # noqa: BLE001
            continue
        try:
            while True:
                occ = searcher.get_next()
                if not occ:
                    break
                start, count = occ
                if count <= 0 or start < min_start:
                    continue
                rect = _union_charboxes(textpage, start, count)
                if not rect:
                    continue
                # Prefer the earliest match for this needle.
                if best is None or start < best[0]:
                    best = (start, count, rect)
                break
        finally:
            try:
                searcher.close()
            except Exception:  # noqa: BLE001
                pass
        if best:
            return best
    return None


def _line_search_variants(line):
    """Full line first, then leading/trailing windows if PDF search is picky."""
    text = _normalize_phrase(line)
    if not text:
        return []
    variants = [text]
    words = text.split(' ')
    if len(words) >= 6:
        variants.append(' '.join(words[:8]))
        variants.append(' '.join(words[-8:]))
        variants.append(' '.join(words[:6]))
    elif len(words) >= 3:
        variants.append(' '.join(words[:3]))
    # Dedupe preserve order
    out, seen = [], set()
    for item in variants:
        key = item.lower()
        if key in seen or len(item) < 3:
            continue
        seen.add(key)
        out.append(item)
    return out


def _find_phrase_rects(pdfium, pdf_bytes, phrases, max_hits_per_phrase):
    pdf = pdfium.PdfDocument(pdf_bytes)
    found = {index: [] for index in range(len(pdf))}
    try:
        for page_index in range(len(pdf)):
            page = pdf[page_index]
            try:
                textpage = page.get_textpage()
            except Exception:  # noqa: BLE001
                try:
                    page.close()
                except Exception:  # noqa: BLE001
                    pass
                continue
            try:
                for phrase in phrases:
                    try:
                        searcher = textpage.search(phrase, match_case=False)
                    except Exception:  # noqa: BLE001
                        continue
                    try:
                        hits = 0
                        while hits < max_hits_per_phrase:
                            occ = searcher.get_next()
                            if not occ:
                                break
                            start, count = occ
                            if count <= 0:
                                continue
                            rect = _union_charboxes(textpage, start, count)
                            if rect:
                                found[page_index].append(rect)
                                hits += 1
                    finally:
                        try:
                            searcher.close()
                        except Exception:  # noqa: BLE001
                            pass
            finally:
                try:
                    textpage.close()
                except Exception:  # noqa: BLE001
                    pass
                try:
                    page.close()
                except Exception:  # noqa: BLE001
                    pass
    finally:
        try:
            pdf.close()
        except Exception:  # noqa: BLE001
            pass
    return found


def _union_charboxes(textpage, start, count):
    xs, ys = [], []
    for index in range(start, start + count):
        try:
            box = textpage.get_charbox(index)
        except Exception:  # noqa: BLE001
            continue
        if not box or len(box) < 4:
            continue
        left, bottom, right, top = box[0], box[1], box[2], box[3]
        xs.extend([left, right])
        ys.extend([bottom, top])
    if not xs or not ys:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def _merge_rects(rects, pad=1.2, iou_threshold=0.35):
    if not rects:
        return []
    boxes = []
    for x0, y0, x1, y1 in rects:
        boxes.append((x0 - pad, y0 - pad, x1 + pad, y1 + pad))
    boxes.sort(key=lambda b: (b[1], b[0], -(b[2] - b[0]), -(b[3] - b[1])))
    merged = []
    for box in boxes:
        absorbed = False
        for index, existing in enumerate(merged):
            if _rects_overlap(box, existing, iou_threshold):
                merged[index] = (
                    min(box[0], existing[0]),
                    min(box[1], existing[1]),
                    max(box[2], existing[2]),
                    max(box[3], existing[3]),
                )
                absorbed = True
                break
        if not absorbed:
            merged.append(box)
    changed = True
    while changed:
        changed = False
        next_merged = []
        for box in merged:
            absorbed = False
            for index, existing in enumerate(next_merged):
                if _rects_same_line_touch(box, existing) or _rects_vertically_stack(box, existing):
                    next_merged[index] = (
                        min(box[0], existing[0]),
                        min(box[1], existing[1]),
                        max(box[2], existing[2]),
                        max(box[3], existing[3]),
                    )
                    absorbed = True
                    changed = True
                    break
            if not absorbed:
                next_merged.append(box)
        merged = next_merged
    return merged


def _rects_overlap(a, b, iou_threshold):
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    if ix1 <= ix0 or iy1 <= iy0:
        return False
    inter = (ix1 - ix0) * (iy1 - iy0)
    area_a = max(1e-6, (ax1 - ax0) * (ay1 - ay0))
    area_b = max(1e-6, (bx1 - bx0) * (by1 - by0))
    return inter / min(area_a, area_b) >= iou_threshold


def _rects_same_line_touch(a, b, y_tol=2.5, x_gap=4.0):
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    a_mid = (ay0 + ay1) / 2.0
    b_mid = (by0 + by1) / 2.0
    if abs(a_mid - b_mid) > y_tol:
        return False
    return not (ax1 + x_gap < bx0 or bx1 + x_gap < ax0)


def _rects_vertically_stack(a, b, y_gap=14.0, min_x_overlap=0.25):
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    if ay1 < by0:
        gap = by0 - ay1
    elif by1 < ay0:
        gap = ay0 - by1
    else:
        gap = 0.0
    if gap > y_gap:
        return False
    overlap = min(ax1, bx1) - max(ax0, bx0)
    width = min(ax1 - ax0, bx1 - bx0)
    if width <= 0:
        return False
    return (overlap / width) >= min_x_overlap


def _burn_rects_into_pdf(pdf_bytes, page_rects, color_rgb):
    """Paint pastel marks under page content so black text stays fully readable."""
    from PyPDF2 import PageObject, PdfReader, PdfWriter
    from reportlab.pdfgen import canvas
    from reportlab.lib.colors import Color

    reader = PdfReader(io.BytesIO(pdf_bytes))
    writer = PdfWriter()
    fill = Color(color_rgb[0], color_rgb[1], color_rgb[2], alpha=1)

    for page_index, page in enumerate(reader.pages):
        rects = _merge_rects(page_rects.get(page_index) or [])
        if not rects:
            writer.add_page(page)
            continue
        mediabox = page.mediabox
        width = float(mediabox.width)
        height = float(mediabox.height)
        packet = io.BytesIO()
        pdf = canvas.Canvas(packet, pagesize=(width, height))
        pdf.setFillColor(fill)
        for x0, y0, x1, y1 in rects:
            pdf.rect(
                max(0, x0),
                max(0, y0),
                max(1.0, x1 - x0),
                max(1.0, y1 - y0),
                stroke=0,
                fill=1,
            )
        pdf.save()
        highlight = PdfReader(io.BytesIO(packet.getvalue())).pages[0]
        combined = PageObject.create_blank_page(width=width, height=height)
        combined.merge_page(highlight)
        combined.merge_page(page)
        writer.add_page(combined)

    out = io.BytesIO()
    writer.write(out)
    data = out.getvalue()
    return data if data.startswith(b'%PDF') else pdf_bytes
