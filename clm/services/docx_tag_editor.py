# Part of NextContract. See LICENSE file for full copyright and licensing details.
"""Parse / rewrite Document Template body paragraphs for the in-form tag editor.

v1 only touches body paragraphs (not headers/footers). Display preserves
run-level formatting (font, size, bold, color, alignment). Saves rewrite
paragraph text while copying formatting from the first existing run so
tags stay intact as a single run.
"""

from __future__ import annotations

import base64
import html as html_lib
import io
import logging
import re

from odoo.exceptions import UserError

from .placeholder_catalog import (
    CLAUSE_LOOP_MARKER,
    CLAUSE_LOOP_SNIPPET,
    known_expression_names,
)
from .clause_set_context import validate_clause_set_expression

_logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r'\{\{\s*([^{}]+?)\s*\}\}')
# Identifier used in placeholder-like broken tags (partner_name, clause_set.x, …).
_PLACEHOLDER_IDENT_RE = re.compile(r'[a-zA-Z_][\w.]*')
_ALIGN_MAP = {
    'left': 'left',
    'start': 'left',
    'center': 'center',
    'right': 'right',
    'end': 'right',
    'justify': 'justify',
    'both': 'justify',
    'distribute': 'justify',
}


def _safe_paragraph_alignment_name(paragraph):
    """Read paragraph alignment without crashing on OOXML values python-docx rejects.

    Newer Word files often use ``w:jc w:val="start"`` / ``"end"``. Accessing
    ``paragraph.alignment`` then raises ``WD_PARAGRAPH_ALIGNMENT has no XML
    mapping for 'start'``, which broke Version Preview for counterparty uploads.
    """
    try:
        alignment = paragraph.alignment
    except (ValueError, KeyError):
        alignment = None
    if alignment is not None:
        return getattr(alignment, 'name', str(alignment).split('.')[-1]).lower()
    try:
        from docx.oxml.ns import qn
        pPr = paragraph._element.pPr
        if pPr is None:
            return None
        jc = pPr.find(qn('w:jc'))
        if jc is None:
            return None
        return (jc.get(qn('w:val')) or '').lower() or None
    except Exception:
        return None


def _require_docx():
    try:
        from docx import Document
    except ImportError as error:
        raise UserError(
            "Editing Document Template tags requires the 'python-docx' package. "
            "Install it on the Odoo server: pip install python-docx"
        ) from error
    return Document


def load_document_bytes(raw_bytes):
    Document = _require_docx()
    if not raw_bytes:
        raise UserError("The Document Template File has no content.")
    return Document(io.BytesIO(raw_bytes))


def iter_body_paragraphs(document):
    """Return body paragraphs in document order (tables excluded in v1)."""
    return list(document.paragraphs)


def _pt(value):
    if value is None:
        return None
    try:
        return float(value.pt)
    except Exception:
        return None


def _is_near_white_hex(hex_str):
    """True when a colour would vanish on white paper (near-white / paper bg)."""
    if not hex_str:
        return False
    value = str(hex_str).strip().lstrip('#').lower()
    if len(value) == 3:
        value = ''.join(ch * 2 for ch in value)
    if len(value) != 6:
        return False
    try:
        r, g, b = int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    except ValueError:
        return False
    # Perceived luminance — drop colours that read as white on #fff paper.
    return (0.299 * r + 0.587 * g + 0.114 * b) >= 230


def _color_css(color):
    if not color:
        return None
    try:
        rgb = color.rgb
        if rgb is None:
            return None
        hex_str = str(rgb)
        # Theme / mistaken white run colours become invisible on the editor
        # paper; treat them as auto so the page's black ink wins.
        if _is_near_white_hex(hex_str):
            return None
        return '#%s' % hex_str
    except Exception:
        return None


def _font_family_css(font_name):
    """CSS font-family with a correct generic fallback (Calibri is sans-serif)."""
    name = (font_name or '').strip().replace("'", '')
    if not name:
        return "Calibri, Carlito, 'Liberation Sans', Arial, sans-serif"
    lower = name.lower()
    serif_names = (
        'times', 'times new roman', 'georgia', 'garamond', 'palatino',
        'cambria', 'liberation serif', 'liberation serif',
    )
    generic = 'serif' if any(lower.startswith(s) or s in lower for s in serif_names) else 'sans-serif'
    # Always offer stack so Preview (no Calibri) matches LibreOffice PDF substitution.
    if generic == 'sans-serif':
        return "'%s', Calibri, Carlito, 'Liberation Sans', Arial, sans-serif" % name
    return "'%s', 'Times New Roman', Times, serif" % name


def _run_inline_css(run):
    """Build inline CSS for a Word run."""
    parts = []
    font = run.font
    if run.bold:
        parts.append('font-weight:700')
    if run.italic:
        parts.append('font-style:italic')
    if run.underline:
        parts.append('text-decoration:underline')
    if font.name:
        parts.append('font-family:%s' % _font_family_css(font.name))
    size_pt = _pt(font.size)
    if size_pt:
        parts.append('font-size:%spt' % size_pt)
    color = _color_css(font.color)
    if color:
        parts.append('color:%s' % color)
    return ';'.join(parts)


def _paragraph_bottom_border_css(paragraph):
    """CSS for a paragraph bottom border from direct pPr or the paragraph style."""
    from docx.oxml.ns import qn

    def _from_pBdr(pBdr):
        if pBdr is None:
            return None
        bottom = pBdr.find(qn('w:bottom'))
        if bottom is None:
            return None
        val = (bottom.get(qn('w:val')) or '').lower()
        if val in ('nil', 'none', ''):
            return None
        try:
            sz_eighths = int(bottom.get(qn('w:sz')) or 8)
        except (TypeError, ValueError):
            sz_eighths = 8
        try:
            space_pt = int(bottom.get(qn('w:space')) or 0)
        except (TypeError, ValueError):
            space_pt = 0
        color = (bottom.get(qn('w:color')) or '4F81BD').strip()
        if not color or color.lower() == 'auto':
            color = '4F81BD'
        # w:sz is in eighths of a point.
        width_pt = max(sz_eighths / 8.0, 0.75)
        return (
            'border-bottom:%spt solid #%s;padding-bottom:%spt'
            % (width_pt, color, space_pt)
        )

    pPr = paragraph._p.find(qn('w:pPr'))
    if pPr is not None:
        css = _from_pBdr(pPr.find(qn('w:pBdr')))
        if css:
            return css
    try:
        style = paragraph.style
        if style is not None and style._element is not None:
            sp = style._element.find(qn('w:pPr'))
            if sp is not None:
                return _from_pBdr(sp.find(qn('w:pBdr')))
    except Exception:
        pass
    return None


def _paragraph_block_css(paragraph):
    """Build CSS for the paragraph container (alignment, spacing, indent, borders)."""
    parts = []
    name = _safe_paragraph_alignment_name(paragraph)
    if name:
        css_align = _ALIGN_MAP.get(name)
        if css_align:
            parts.append('text-align:%s' % css_align)

    fmt = paragraph.paragraph_format
    before = _pt(fmt.space_before)
    after = _pt(fmt.space_after)
    if before:
        parts.append('margin-top:%spt' % before)
    if after is not None:
        parts.append('margin-bottom:%spt' % (after if after else 0))
    else:
        parts.append('margin-bottom:6pt')

    # Preserve explicit line spacing from DOCX (single / multiple / exact).
    try:
        line = fmt.line_spacing
        if line is not None:
            # python-docx: float => multiplier; Length => exact height.
            if isinstance(line, float):
                parts.append('line-height:%s' % line)
            else:
                line_pt = _pt(line)
                if line_pt:
                    parts.append('line-height:%spt' % line_pt)
    except Exception:
        pass

    left = _pt(fmt.left_indent)
    first = _pt(fmt.first_line_indent)
    if left:
        parts.append('padding-left:%spt' % left)
    if first:
        parts.append('text-indent:%spt' % first)

    style_name = ''
    try:
        style_name = (paragraph.style.name or '') if paragraph.style else ''
    except Exception:
        style_name = ''
    if style_name == 'Title':
        parts.append('font-weight:700')
        parts.append('color:#162233')
        parts.append('font-size:18pt')
        parts.append('font-family:%s' % _font_family_css('Calibri'))
    elif style_name.startswith('Heading'):
        parts.append('font-weight:700')
        parts.append('color:#162233' if '1' in style_name else '#354456')
        if '1' in style_name:
            parts.append('font-size:13pt')
        elif '2' in style_name:
            parts.append('font-size:10.5pt')
        elif '3' in style_name:
            parts.append('font-size:9.5pt')
        parts.append('font-family:%s' % _font_family_css('Calibri'))

    border_css = _paragraph_bottom_border_css(paragraph)
    if border_css:
        parts.append(border_css)

    return ';'.join(parts)


def _image_data_uri(document_part, r_id):
    """Resolve a relationship id to a data-URI for preview."""
    if not r_id or document_part is None:
        return None
    try:
        image_part = document_part.related_parts[r_id]
    except KeyError:
        return None
    blob = getattr(image_part, 'blob', None)
    if not blob:
        return None
    content_type = getattr(image_part, 'content_type', None) or 'image/png'
    return 'data:%s;base64,%s' % (
        content_type,
        base64.b64encode(blob).decode('ascii'),
    )


def _extent_max_width_px(drawing_el, default=320):
    """Read floating/inline extent (EMU) and convert to a sensible CSS max-width."""
    try:
        from docx.oxml.ns import qn
        extent = drawing_el.find('.//' + qn('wp:extent'))
        if extent is None:
            return default
        cx = extent.get('cx')
        if not cx:
            return default
        # 914400 EMU = 1 inch ≈ 96 CSS px
        px = int(int(cx) / 914400 * 96)
        return max(40, min(px, 640))
    except Exception:
        return default


def _images_html_from_element(element, document_part):
    """Extract <img> tags for drawings (a:blip) and VML pictures (v:imagedata)."""
    if element is None or document_part is None:
        return []
    from docx.oxml.ns import qn

    imgs = []
    seen = set()

    for blip in element.findall('.//' + qn('a:blip')):
        r_id = blip.get(qn('r:embed')) or blip.get(
            '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed'
        )
        if not r_id or r_id in seen:
            continue
        uri = _image_data_uri(document_part, r_id)
        if not uri:
            continue
        seen.add(r_id)
        # Find nearest drawing ancestor for size hint
        drawing = blip.getparent()
        while drawing is not None and drawing.tag != qn('w:drawing'):
            drawing = drawing.getparent()
        max_w = _extent_max_width_px(drawing) if drawing is not None else 320
        imgs.append(
            '<img class="o_clm_docx_img" src="%s" alt="Image" '
            'style="max-width:%spx;max-height:180px;height:auto;width:auto;'
            'display:inline-block;vertical-align:middle;"/>'
            % (uri, max_w)
        )

    # Legacy VML images (common in older Word logos)
    for imagedata in element.findall('.//{urn:schemas-microsoft-com:vml}imagedata'):
        r_id = imagedata.get(qn('r:id')) or imagedata.get(
            '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'
        )
        if not r_id or r_id in seen:
            continue
        uri = _image_data_uri(document_part, r_id)
        if not uri:
            continue
        seen.add(r_id)
        imgs.append(
            '<img class="o_clm_docx_img" src="%s" alt="Image" '
            'style="max-width:320px;max-height:180px;height:auto;width:auto;'
            'display:inline-block;vertical-align:middle;"/>'
            % uri
        )
    return imgs


def _run_has_drawing(run):
    from docx.oxml.ns import qn
    el = run._element
    return bool(
        el.findall('.//' + qn('w:drawing'))
        or el.findall('.//' + qn('w:pict'))
        or el.findall('.//{urn:schemas-microsoft-com:vml}imagedata')
    )


_DATA_URI_RE = re.compile(
    r'^data:([^;,]+)?(;base64)?,(.*)$',
    re.DOTALL,
)


def _css_max_width_px(style_css, default=None):
    """Parse max-width / width from an img style into CSS pixels."""
    styles = _parse_style_declaration(style_css or '')
    for key in ('max-width', 'width'):
        raw = (styles.get(key) or '').strip().lower()
        if not raw or raw == 'auto':
            continue
        match = re.match(r'^([\d.]+)\s*px$', raw)
        if match:
            try:
                return max(24, min(int(float(match.group(1))), 640))
            except (TypeError, ValueError):
                continue
    return default


def _add_picture_from_data_uri(run, data_uri, width_px=None):
    """Embed a data:image/...;base64,... URI into a python-docx run."""
    match = _DATA_URI_RE.match((data_uri or '').strip())
    if not match or not match.group(2):
        return False
    try:
        blob = base64.b64decode(match.group(3) or '', validate=False)
    except Exception:
        return False
    if not blob:
        return False
    from docx.shared import Inches

    # Prefer CSS max-width when present; otherwise ~1.5".
    if width_px:
        width = Inches(max(0.25, min(float(width_px) / 96.0, 6.5)))
    else:
        width = Inches(1.5)
    try:
        run.add_picture(io.BytesIO(blob), width=width)
        return True
    except Exception as error:
        _logger.warning('Failed to re-embed DOCX image from data URI: %s', error)
        return False


def _paragraph_html(paragraph, document_part=None):
    """Render a paragraph's runs as HTML (text formatting + embedded images)."""
    runs = list(paragraph.runs)
    chunks = []

    for run in runs:
        chunks.extend(_images_html_from_element(run._element, document_part))
        text = run.text or ''
        if text == '':
            continue
        css = _run_inline_css(run)
        escaped = html_lib.escape(text).replace('\n', '<br/>')
        if css:
            chunks.append('<span style="%s">%s</span>' % (css, escaped))
        else:
            chunks.append('<span>%s</span>' % escaped)

    # Fallback: drawings attached outside normal runs (rare).
    if document_part is not None and not any('o_clm_docx_img' in c for c in chunks):
        chunks[0:0] = _images_html_from_element(paragraph._p, document_part)

    if not chunks:
        text = paragraph.text or ''
        if not text:
            return '<br/>'
        return '<span>%s</span>' % html_lib.escape(text)
    return ''.join(chunks)


def _paragraph_has_visual(paragraph, document_part=None):
    if (paragraph.text or '').strip():
        return True
    html = _paragraph_html(paragraph, document_part)
    return 'o_clm_docx_img' in html


def extract_header_blocks(document):
    """Header paragraphs (logos often live here) — display only in the editor."""
    blocks = []
    seen_header_ids = set()
    for section in document.sections:
        try:
            header = section.header
        except Exception:
            continue
        header_id = id(header._element)
        if header_id in seen_header_ids:
            continue
        seen_header_ids.add(header_id)
        # Skip truly empty default headers
        doc_part = header.part
        for paragraph in header.paragraphs:
            if not _paragraph_has_visual(paragraph, doc_part):
                continue
            blocks.append({
                'html': _paragraph_html(paragraph, doc_part),
                'style_css': _paragraph_block_css(paragraph),
                'text': paragraph.text or '',
            })
    return blocks


def extract_paragraphs(raw_bytes):
    """Return body paragraphs with plain text + formatted HTML + block CSS."""
    return extract_document_preview(raw_bytes)['paragraphs']


def extract_document_preview(raw_bytes):
    """Full preview payload: header blocks (logos) + editable body paragraphs."""
    document = load_document_bytes(raw_bytes)
    doc_part = document.part
    headers = extract_header_blocks(document)
    paragraphs = []
    for index, paragraph in enumerate(iter_body_paragraphs(document)):
        html = _paragraph_html(paragraph, doc_part)
        paragraphs.append({
            'index': index,
            'text': paragraph.text or '',
            'html': html,
            'style_css': _paragraph_block_css(paragraph),
            'has_image': 'o_clm_docx_img' in html,
        })
    return {
        'headers': headers,
        'paragraphs': paragraphs,
        'document_html': build_editor_html(paragraphs),
    }


def _copy_run_format(source_run, target_run):
    if source_run is None or target_run is None:
        return
    try:
        target_run.bold = source_run.bold
        target_run.italic = source_run.italic
        target_run.underline = source_run.underline
        if source_run.font.name:
            target_run.font.name = source_run.font.name
        if source_run.font.size:
            target_run.font.size = source_run.font.size
        try:
            if source_run.font.color and source_run.font.color.rgb:
                target_run.font.color.rgb = source_run.font.color.rgb
        except Exception:
            pass
    except Exception:
        _logger.debug('Could not copy run formatting', exc_info=True)


def _set_paragraph_text(paragraph, text):
    """
    Update paragraph text while preserving embedded images/logos.

    Prefer append-only when the new text extends the old text (tag insert).
    Otherwise rebuild text runs but keep any drawing/pict runs.
    """
    from copy import deepcopy

    old_text = paragraph.text or ''
    new_text = text or ''

    if old_text == new_text:
        return

    # Tag / suffix append — keep all existing runs (including images).
    if new_text.startswith(old_text):
        suffix = new_text[len(old_text):]
        if not suffix:
            return
        sample = None
        for run in reversed(list(paragraph.runs)):
            if run.text:
                sample = run
                break
        new_run = paragraph.add_run(suffix)
        _copy_run_format(sample, new_run)
        return

    sample = None
    drawing_elements = []
    for run in paragraph.runs:
        if _run_has_drawing(run):
            drawing_elements.append(deepcopy(run._element))
        elif run.text and sample is None:
            sample = run

    for run in list(paragraph.runs):
        run._element.getparent().remove(run._element)

    for el in drawing_elements:
        paragraph._p.append(el)

    new_run = paragraph.add_run(new_text)
    _copy_run_format(sample, new_run)


def _parse_style_declaration(style_text):
    """Parse inline CSS into a dict of lowercase properties."""
    result = {}
    for part in (style_text or '').split(';'):
        if ':' not in part:
            continue
        key, value = part.split(':', 1)
        result[key.strip().lower()] = value.strip()
    return result


def _css_font_size_pt(value):
    if not value:
        return None
    value = value.strip().lower()
    try:
        if value.endswith('pt'):
            return float(value[:-2])
        if value.endswith('px'):
            return float(value[:-2]) * 0.75
        if value.endswith('em'):
            return float(value[:-2]) * 12.0
        return float(value)
    except Exception:
        return None


def _css_color_hex(value):
    if not value:
        return None
    value = value.strip()
    hex_str = None
    if value.startswith('#') and len(value) in (4, 7):
        if len(value) == 4:
            hex_str = '#' + ''.join(ch * 2 for ch in value[1:])
        else:
            hex_str = value.lower()
    else:
        # rgb(r,g,b)
        match = re.match(r'rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)', value, re.I)
        if match:
            hex_str = '#%02x%02x%02x' % tuple(int(x) for x in match.groups())
    if hex_str and _is_near_white_hex(hex_str):
        return None
    return hex_str


def _merge_format(base, extra):
    out = dict(base or {})
    out.update({k: v for k, v in (extra or {}).items() if v is not None})
    return out


def _format_from_css(style_map, tag_name=''):
    fmt = {}
    tag = (tag_name or '').lower()
    if tag in ('b', 'strong') or style_map.get('font-weight', '').startswith('7') \
            or style_map.get('font-weight') in ('bold', '700', '800', '900'):
        fmt['bold'] = True
    if tag in ('i', 'em') or style_map.get('font-style') == 'italic':
        fmt['italic'] = True
    deco = style_map.get('text-decoration', '')
    if tag == 'u' or 'underline' in deco:
        fmt['underline'] = True
    family = style_map.get('font-family')
    if family:
        fmt['font_name'] = family.split(',')[0].strip().strip('"\'')
    size = _css_font_size_pt(style_map.get('font-size'))
    if size:
        fmt['font_size_pt'] = size
    color = _css_color_hex(style_map.get('color'))
    if color:
        fmt['color'] = color
    return fmt


def _walk_html_runs(node, inherited=None):
    """Yield (text, format_dict) from an lxml/html node tree."""
    inherited = inherited or {}
    try:
        from lxml import html as lxml_html
    except ImportError:
        lxml_html = None

    if node is None:
        return

    # Text node
    if getattr(node, 'tag', None) is None or isinstance(node, str):
        text = str(node) if isinstance(node, str) else (node.text or '')
        if text:
            yield text, inherited
        return

    tag = (node.tag or '').lower() if isinstance(node.tag, str) else ''
    if tag in ('script', 'style'):
        return
    if tag == 'br':
        yield '\n', inherited
        return
    if tag == 'img':
        # Prefer live data: src; fall back to the original URI the editor
        # stashed before materializing blob: URLs for display.
        src = (node.get('src') or '').strip()
        if not src.startswith('data:'):
            src = (
                node.get('data-clm-original-src')
                or node.get('data-original-src')
                or ''
            ).strip()
        if src.startswith('data:'):
            style = node.get('style') or ''
            yield '', {
                'image_src': src,
                'image_style': style,
                'image_width_px': _css_max_width_px(style),
            }
        return

    style_map = _parse_style_declaration(node.get('style'))
    fmt = _merge_format(inherited, _format_from_css(style_map, tag))

    if node.text:
        yield node.text, fmt
    for child in node:
        yield from _walk_html_runs(child, fmt)
        if child.tail:
            yield child.tail, inherited


def parse_html_to_runs(html_fragment):
    """
    Convert an HTML fragment (contenteditable output) into run descriptors.

    Returns list of dicts: text, bold, italic, underline, font_name, font_size_pt,
    color, and optionally image_src / image_width_px for embedded logos.
    """
    html_fragment = (html_fragment or '').strip()
    if not html_fragment or html_fragment == '<br/>' or html_fragment == '<br>':
        return []

    try:
        from lxml import html as lxml_html
    except ImportError as error:
        raise UserError("HTML formatting save requires the 'lxml' package.") from error

    wrapper = lxml_html.fragment_fromstring(
        '<div>%s</div>' % html_fragment,
        create_parent=False,
    )
    # fragment_fromstring may return the div or a list
    if isinstance(wrapper, list):
        nodes = wrapper
    else:
        nodes = [wrapper]

    runs = []
    for node in nodes:
        for text, fmt in _walk_html_runs(node, {}):
            if fmt.get('image_src'):
                runs.append({
                    'text': '',
                    'image_src': fmt['image_src'],
                    'image_width_px': fmt.get('image_width_px'),
                    'bold': False,
                    'italic': False,
                    'underline': False,
                    'font_name': None,
                    'font_size_pt': None,
                    'color': None,
                })
                continue
            if text is None:
                continue
            # Don't drop whitespace-only — it matters for spacing.
            if text == '':
                continue
            runs.append({
                'text': text.replace('\xa0', ' '),
                'bold': bool(fmt.get('bold')),
                'italic': bool(fmt.get('italic')),
                'underline': bool(fmt.get('underline')),
                'font_name': fmt.get('font_name'),
                'font_size_pt': fmt.get('font_size_pt'),
                'color': fmt.get('color'),
            })
    return runs


def _apply_run_format(run, fmt):
    from docx.shared import Pt, RGBColor
    if not fmt:
        return
    if fmt.get('bold'):
        run.bold = True
    if fmt.get('italic'):
        run.italic = True
    if fmt.get('underline'):
        run.underline = True
    if fmt.get('font_name'):
        run.font.name = fmt['font_name']
    if fmt.get('font_size_pt'):
        try:
            run.font.size = Pt(float(fmt['font_size_pt']))
        except Exception:
            pass
    if fmt.get('color'):
        try:
            hex_color = fmt['color'].lstrip('#')
            run.font.color.rgb = RGBColor.from_string(hex_color)
        except Exception:
            pass


def _apply_paragraph_alignment(paragraph, style_css):
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt

    styles = _parse_style_declaration(style_css)
    align = (styles.get('text-align') or '').lower()
    mapping = {
        'left': WD_ALIGN_PARAGRAPH.LEFT,
        'center': WD_ALIGN_PARAGRAPH.CENTER,
        'right': WD_ALIGN_PARAGRAPH.RIGHT,
        'justify': WD_ALIGN_PARAGRAPH.JUSTIFY,
    }
    if align in mapping:
        paragraph.alignment = mapping[align]

    # Compact clause blocks: honor margin-top / margin-bottom from the HTML editor.
    fmt = paragraph.paragraph_format
    top = _css_font_size_pt(styles.get('margin-top'))
    bottom = _css_font_size_pt(styles.get('margin-bottom'))
    if top is not None:
        fmt.space_before = Pt(max(0.0, top))
    if bottom is not None:
        fmt.space_after = Pt(max(0.0, bottom))


def style_clause_loop_placeholders(docx_bytes):
    """Tighten spacing and bold ``{{ clause.title }}`` in the Clause Collection block.

    The Legal Consulting Base DOCX inherits document-default ``space_after`` (~10pt)
    on every paragraph, which opens large gaps between title / category / wording.
    Call this on Base DOCX bytes before docxtpl render (and when saving templates).
    """
    if not docx_bytes:
        return docx_bytes
    try:
        from docx import Document
        from docx.shared import Pt
    except ImportError:
        return docx_bytes

    try:
        document = Document(io.BytesIO(docx_bytes))
    except Exception:
        return docx_bytes

    changed = False
    for paragraph in document.paragraphs:
        text = (paragraph.text or '').strip()
        fmt = paragraph.paragraph_format
        if text == '{{ clause.title }}':
            for run in paragraph.runs:
                run.bold = True
            fmt.space_before = Pt(8)
            fmt.space_after = Pt(0)
            fmt.line_spacing = 1.0
            changed = True
        elif text == '{{ clause.category }}':
            for run in paragraph.runs:
                run.italic = True
            # One line of space under the title so category stays distinct.
            fmt.space_before = Pt(12)
            fmt.space_after = Pt(0)
            fmt.line_spacing = 1.0
            changed = True
        elif text == '{{ clause.content }}':
            # One line of space above wording so category stays distinct.
            fmt.space_before = Pt(12)
            fmt.space_after = Pt(10)
            fmt.line_spacing = 1.0
            changed = True
        elif text.startswith('{%') and text.endswith('%}'):
            fmt.space_before = Pt(0)
            fmt.space_after = Pt(0)
            fmt.line_spacing = 1.0
            changed = True

    if not changed:
        return docx_bytes
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


def _set_paragraph_from_html(paragraph, html, style_css=None, plain_text=None):
    """
    Rebuild paragraph text runs from HTML formatting.

    Re-embeds <img src="data:..."> from the editor HTML. When the HTML has no
    images, keep any existing drawing/pict runs so logos are not dropped on
    in-place paragraph updates.
    """
    from copy import deepcopy

    run_specs = parse_html_to_runs(html)
    plain = (
        plain_text
        if plain_text is not None
        else ''.join(r.get('text') or '' for r in run_specs)
    )
    html_has_images = any(r.get('image_src') for r in run_specs)

    # Fast path: unchanged plain text and no HTML provided → skip.
    if not html and (paragraph.text or '') == (plain or ''):
        return

    drawing_elements = []
    if not html_has_images:
        for run in paragraph.runs:
            if _run_has_drawing(run):
                drawing_elements.append(deepcopy(run._element))

    for run in list(paragraph.runs):
        run._element.getparent().remove(run._element)

    for el in drawing_elements:
        paragraph._p.append(el)

    if not run_specs and plain:
        paragraph.add_run(plain)
    else:
        for spec in run_specs:
            if spec.get('image_src'):
                img_run = paragraph.add_run()
                _add_picture_from_data_uri(
                    img_run,
                    spec['image_src'],
                    width_px=spec.get('image_width_px'),
                )
                continue
            new_run = paragraph.add_run(spec.get('text') or '')
            _apply_run_format(new_run, spec)

    if style_css:
        _apply_paragraph_alignment(paragraph, style_css)


def apply_paragraph_texts(raw_bytes, texts_by_index):
    """
    Rewrite body paragraph texts.

    Unchanged paragraphs keep their original split-run formatting.
    """
    document = load_document_bytes(raw_bytes)
    paragraphs = iter_body_paragraphs(document)
    max_existing = len(paragraphs)

    for index, paragraph in enumerate(paragraphs):
        if index in texts_by_index:
            new_text = texts_by_index[index]
            if (paragraph.text or '') == (new_text or ''):
                continue
            _set_paragraph_text(paragraph, new_text)

    for index in sorted(i for i in texts_by_index if i >= max_existing):
        paragraph = document.add_paragraph()
        _set_paragraph_text(paragraph, texts_by_index[index])

    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def apply_paragraph_payloads(raw_bytes, payloads_by_index):
    """
    Apply editor payloads {index: {text, html, style_css}} to the DOCX.

    When `html` is present, formatting is written back as Word runs.
    Images/logos already in the paragraph are preserved.
    """
    document = load_document_bytes(raw_bytes)
    paragraphs = iter_body_paragraphs(document)
    max_existing = len(paragraphs)

    for index, paragraph in enumerate(paragraphs):
        if index not in payloads_by_index:
            continue
        payload = payloads_by_index[index] or {}
        if not isinstance(payload, dict):
            payload = {'text': payload}
        html = payload.get('html')
        text = payload.get('text')
        style_css = payload.get('style_css') or payload.get('styleCss') or ''
        if html:
            derived = ''.join(r['text'] for r in parse_html_to_runs(html))
            # If plain text was updated without refreshing html (or they drift),
            # prefer the text path so tag inserts are not lost.
            if text is not None and derived != (text or ''):
                if (paragraph.text or '') != (text or ''):
                    _set_paragraph_text(paragraph, text)
                if style_css:
                    _apply_paragraph_alignment(paragraph, style_css)
            else:
                _set_paragraph_from_html(
                    paragraph, html, style_css=style_css, plain_text=text
                )
        elif text is not None:
            if (paragraph.text or '') != (text or ''):
                _set_paragraph_text(paragraph, text)
            if style_css:
                _apply_paragraph_alignment(paragraph, style_css)

    for index in sorted(i for i in payloads_by_index if i >= max_existing):
        payload = payloads_by_index[index] or {}
        if not isinstance(payload, dict):
            payload = {'text': payload}
        paragraph = document.add_paragraph()
        html = payload.get('html')
        text = payload.get('text') or ''
        style_css = payload.get('style_css') or payload.get('styleCss') or ''
        if html:
            _set_paragraph_from_html(paragraph, html, style_css=style_css, plain_text=text)
        else:
            _set_paragraph_text(paragraph, text)
            if style_css:
                _apply_paragraph_alignment(paragraph, style_css)

    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def append_tag_to_text(text, insert):
    text = text or ''
    insert = insert or ''
    if not insert:
        return text
    if insert in text:
        return text
    if not text.strip():
        return insert
    if text.endswith((' ', '\n', '\t')):
        return text + insert
    return text + ' ' + insert


def ensure_clause_loop(texts):
    """Append the standard clause loop when missing."""
    joined = '\n'.join(texts or [])
    if CLAUSE_LOOP_MARKER in joined:
        return list(texts or [])
    result = list(texts or [])
    if result and result[-1].strip():
        result.append('')
    result.extend(CLAUSE_LOOP_SNIPPET)
    return result


def _normalize_expression(expr):
    expr = (expr or '').strip()
    if '|' in expr:
        expr = expr.split('|', 1)[0].strip()
    return expr


def validate_document_text(
    full_text,
    known_expressions=None,
    require_clause_loop=False,
    clause_keys=None,
):
    """Validate Jinja tags. Returns (errors, warnings, issue_spans)."""
    known = known_expressions or known_expression_names()
    errors = []
    warnings = []
    issue_spans = []
    text = full_text or ''

    brace_errors, brace_spans = _scan_malformed_var_braces(text)
    errors.extend(brace_errors)
    issue_spans.extend(brace_spans)

    if text.count('{%') != text.count('%}'):
        errors.append("Unbalanced {% %} block tags — a loop tag may be split or incomplete.")
        issue_spans.extend(_orphan_delimiter_spans(text, '{%', '%}'))

    for match in _TAG_RE.finditer(text):
        expr = _normalize_expression(match.group(1))
        if expr == 'clause_set' or expr.startswith('clause_set.'):
            msg = validate_clause_set_expression(expr, available_keys=clause_keys)
            if msg:
                errors.append(msg)
                issue_spans.append({
                    'text': match.group(0),
                    'severity': 'error',
                    'kind': 'clause_set',
                    'start': match.start(),
                    'end': match.end(),
                })
            continue
        if expr not in known and not any(
            expr == name or expr.startswith(name + '.') for name in known
        ):
            errors.append("Unknown placeholder {{ %s }}." % expr)
            issue_spans.append({
                'text': match.group(0),
                'severity': 'error',
                'kind': 'unknown',
                'start': match.start(),
                'end': match.end(),
            })

    has_clause_placement = (
        CLAUSE_LOOP_MARKER in text
        or '{{ clause_set }}' in text
        or 'clause_set.clauses.' in text
    )
    if require_clause_loop and not has_clause_placement:
        warnings.append(
            "This template has clause lines, but the Document Template has no "
            "clause placement. Use Insert Clause Collection or Insert & Customize Clauses "
            "so Generate Draft DOCX can place clauses."
        )

    return errors, warnings, issue_spans


def _scan_malformed_var_braces(text):
    """Detect broken ``{{ }}`` forms that a simple open/close count can miss.

    Examples that cancel out in a raw count but are still invalid:
    - ``{ partner_name }}``
    - ``{{ contract_name }``
    - ``partner_address }}``
    - ``{{ partner_address``
    """
    errors = []
    spans = []
    seen_msgs = set()
    i = 0
    n = len(text)

    def _add(msg, start, end, kind):
        if msg not in seen_msgs:
            errors.append(msg)
            seen_msgs.add(msg)
        spans.append({
            'text': text[start:end],
            'severity': 'error',
            'kind': kind,
            'start': start,
            'end': end,
        })

    while i < n:
        # Skip intact block tags {% ... %}
        if text.startswith('{%', i):
            end = text.find('%}', i + 2)
            if end == -1:
                _add(
                    "Unbalanced {% %} block tags — a loop tag may be split or incomplete.",
                    i,
                    min(i + 2, n),
                    'orphan_block_open',
                )
                break
            i = end + 2
            continue

        # Variable open {{
        if text.startswith('{{', i):
            j = i + 2
            closed = False
            while j < n:
                if text.startswith('}}', j):
                    # Intact {{ ... }}
                    i = j + 2
                    closed = True
                    break
                if text.startswith('{{', j) or text.startswith('{%', j):
                    _add(
                        "Incomplete placeholder starting at '{{' — missing closing '}}'.",
                        i,
                        j,
                        'incomplete_open',
                    )
                    i = j
                    closed = True
                    break
                # Single } that is not part of }}
                if text[j] == '}':
                    _add(
                        "Malformed placeholder — '{{ … }' should be '{{ … }}'.",
                        i,
                        j + 1,
                        'single_close',
                    )
                    i = j + 1
                    closed = True
                    break
                j += 1
            if not closed:
                _add(
                    "Incomplete placeholder starting at '{{' — missing closing '}}'.",
                    i,
                    n,
                    'incomplete_open',
                )
                break
            continue

        # Orphan }}
        if text.startswith('}}', i):
            # Prefer highlighting "{ expr }}" or "expr }}" when present.
            lookback = text[max(0, i - 80):i]
            broken_open = re.search(
                r'(?<!\{)\{(?!%)\s*(' + _PLACEHOLDER_IDENT_RE.pattern + r')\s*$',
                lookback,
            )
            ident_only = re.search(
                r'(?<![{%\w.])(' + _PLACEHOLDER_IDENT_RE.pattern + r')\s*$',
                lookback,
            )
            if broken_open:
                start = i - len(broken_open.group(0))
                _add(
                    "Malformed placeholder — '{ … }}' should be '{{ … }}'.",
                    start,
                    i + 2,
                    'single_open',
                )
            elif ident_only:
                start = i - len(ident_only.group(0))
                _add(
                    "Orphan '}}' after '%s' — missing opening '{{'."
                    % ident_only.group(1),
                    start,
                    i + 2,
                    'orphan_close',
                )
            else:
                _add(
                    "Orphan '}}' — missing opening '{{'.",
                    i,
                    i + 2,
                    'orphan_close',
                )
            i += 2
            continue

        # Single { that looks like a broken tag opener: { ident }}
        if text[i] == '{' and not text.startswith('{{', i) and not text.startswith('{%', i):
            m = re.match(
                r'\{\s*(' + _PLACEHOLDER_IDENT_RE.pattern + r')\s*\}\}',
                text[i:],
            )
            if m:
                _add(
                    "Malformed placeholder — '{ … }}' should be '{{ … }}'.",
                    i,
                    i + m.end(),
                    'single_open',
                )
                i += m.end()
                continue

        i += 1

    # Keep a high-level count message when opens/closes still disagree and we
    # did not already report a more specific brace problem.
    if text.count('{{') != text.count('}}') and not any(
        s.get('kind') in (
            'incomplete_open',
            'single_close',
            'single_open',
            'orphan_close',
        )
        for s in spans
    ):
        errors.append("Unbalanced {{ }} placeholders — a tag may be split or incomplete.")
        spans.extend(_orphan_delimiter_spans(text, '{{', '}}'))

    return errors, spans


def _orphan_delimiter_spans(text, open_tok, close_tok):
    """Return highlight spans for unmatched open/close delimiters."""
    spans = []
    stack = []
    i = 0
    open_len = len(open_tok)
    close_len = len(close_tok)
    while i < len(text):
        if text.startswith(open_tok, i):
            stack.append(i)
            i += open_len
            continue
        if text.startswith(close_tok, i):
            if stack:
                stack.pop()
            else:
                spans.append({
                    'text': close_tok,
                    'severity': 'error',
                    'kind': 'orphan_close',
                    'start': i,
                    'end': i + close_len,
                })
            i += close_len
            continue
        i += 1
    for start in stack:
        spans.append({
            'text': open_tok,
            'severity': 'error',
            'kind': 'orphan_open',
            'start': start,
            'end': start + open_len,
        })
    return spans


def validate_paragraphs(
    paragraph_texts,
    known_expressions=None,
    require_clause_loop=False,
    clause_keys=None,
):
    return validate_document_text(
        '\n'.join(paragraph_texts or []),
        known_expressions=known_expressions,
        require_clause_loop=require_clause_loop,
        clause_keys=clause_keys,
    )


def bytes_from_b64(datas):
    """Decode Odoo Binary / base64 payload to raw bytes.

    Guards against ``bin_size=True`` context where ``datas`` is a size label
    like ``b'43.63 Kb'`` instead of file content.
    """
    if not datas:
        return b''
    if isinstance(datas, memoryview):
        datas = datas.tobytes()
    if isinstance(datas, bytes):
        if datas[:2] == b'PK':
            return datas
        # Human-readable size from bin_size context — not real file data.
        if b' ' in datas and datas.rstrip().endswith((b'b', b'B')) and len(datas) < 32:
            return b''
        try:
            return _safe_b64decode(datas)
        except Exception:
            return datas
    text = str(datas).strip()
    if text.endswith(('b', 'B')) and ' ' in text and len(text) < 32:
        # e.g. "43.63 Kb"
        return b''
    try:
        return _safe_b64decode(text)
    except Exception:
        return b''


def _safe_b64decode(payload):
    """base64-decode with missing-padding tolerance."""
    if isinstance(payload, str):
        payload = payload.encode('ascii', errors='ignore')
    payload = payload.strip().replace(b'\n', b'').replace(b'\r', b'')
    pad = (-len(payload)) % 4
    if pad:
        payload = payload + (b'=' * pad)
    return base64.b64decode(payload, validate=False)


def build_editor_html(paragraphs):
    """Build a single HTML document string for the Odoo Wysiwyg editor."""
    parts = []
    for row in paragraphs or []:
        index = row.get('index', 0)
        style = row.get('style_css') or ''
        style_attr = (
            ' style="%s"' % html_lib.escape(style, quote=True) if style else ''
        )
        inner = row.get('html') or '<br/>'
        parts.append(
            '<p data-clm-index="%s"%s>%s</p>' % (index, style_attr, inner)
        )
    if not parts:
        parts.append('<p data-clm-index="0"><br/></p>')
    return ''.join(parts)


def build_full_preview_html(headers=None, paragraphs=None):
    """Read-only HTML including header logos + body paragraphs."""
    parts = []
    header_rows = headers or []
    if header_rows:
        parts.append('<div class="o_clm_docx_header o_clm_docx_hdr">')
        for index, row in enumerate(header_rows):
            style = row.get('style_css') or ''
            style_attr = (
                ' style="%s"' % html_lib.escape(style, quote=True) if style else ''
            )
            inner = row.get('html') or '<br/>'
            parts.append(
                '<div class="o_clm_docx_header_line o_clm_docx_hdr" '
                'data-header-index="%s"%s>%s</div>'
                % (index, style_attr, inner)
            )
        parts.append('</div>')
    parts.append(build_editor_html(paragraphs))
    return ''.join(parts)


def iter_document_block_items(document):
    """Yield body paragraphs and tables in document order.

    Unlike ``document.paragraphs`` (tables omitted), this walks ``w:body``
    children so read-only Preview can show cover / parties / terms tables.
    """
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    body = document.element.body
    for child in body.iterchildren():
        if child.tag == qn('w:p'):
            yield ('p', Paragraph(child, document))
        elif child.tag == qn('w:tbl'):
            yield ('tbl', Table(child, document))


def _cell_shading_fill(cell):
    """Return hex fill from ``w:shd`` (e.g. ``415A77``), or None."""
    from docx.oxml.ns import qn
    tcPr = cell._tc.tcPr
    if tcPr is None:
        return None
    shd = tcPr.find(qn('w:shd'))
    if shd is None:
        return None
    fill = (shd.get(qn('w:fill')) or '').strip()
    if not fill or fill.lower() in ('auto', 'ffffff', 'FFFFFF'):
        # Treat pure white / auto as no fill for preview.
        if fill.lower() in ('auto',):
            return None
        if fill.upper() == 'FFFFFF':
            return None
    return fill or None


def _cell_border_color(cell, default='CCCCCC'):
    """Pick a representative cell border colour from ``w:tcBorders``."""
    from docx.oxml.ns import qn
    tcPr = cell._tc.tcPr
    if tcPr is None:
        return default
    borders = tcPr.find(qn('w:tcBorders'))
    if borders is None:
        return default
    for edge in ('top', 'bottom', 'left', 'right'):
        element = borders.find(qn('w:%s' % edge))
        if element is None:
            continue
        color = (element.get(qn('w:color')) or '').strip()
        if color and color.lower() not in ('auto',):
            return color
    return default


def _row_exact_height_pt(row):
    """Return exact row height in points when ``w:trHeight`` is set, else None."""
    from docx.oxml.ns import qn
    trPr = row._tr.find(qn('w:trPr'))
    if trPr is None:
        return None
    trHeight = trPr.find(qn('w:trHeight'))
    if trHeight is None:
        return None
    try:
        twips = int(trHeight.get(qn('w:val')) or 0)
    except (TypeError, ValueError):
        return None
    if twips <= 0:
        return None
    # 20 twips = 1 pt — keep accent rules visible in HTML preview.
    return max(twips / 20.0, 5.0)


def _table_preview_html(table, document_part=None):
    """HTML table for read-only DOCX Preview (cell text, fills, and borders)."""
    # Professional accent rule: one empty shaded cell with fixed row height.
    # Render as a <div> so Preview cannot collapse it into a grey empty box.
    if len(table.rows) == 1 and len(table.columns) == 1:
        cell = table.rows[0].cells[0]
        fill = _cell_shading_fill(cell)
        text_blob = ' '.join(
            (p.text or '').strip() for p in cell.paragraphs
        ).strip()
        row_height_pt = _row_exact_height_pt(table.rows[0])
        if fill and not text_blob:
            height = row_height_pt or 5.0
            # Slightly taller in HTML so the rule stays visible on screen.
            height = max(height, 6.0)
            return (
                '<div class="o_clm_docx_accent_rule" '
                'style="display:block;width:100%%;height:%spt;'
                'margin:10pt 0 14pt;padding:0;border:none;'
                'background-color:#%s;line-height:0;font-size:0;">'
                '&nbsp;</div>'
            ) % (height, fill)

    rows_html = []
    for row in table.rows:
        cells_html = []
        row_height_pt = _row_exact_height_pt(row)
        for cell in row.cells:
            cell_parts = []
            for paragraph in cell.paragraphs:
                inner = _paragraph_html(paragraph, document_part)
                style = _paragraph_block_css(paragraph)
                style_attr = (
                    ' style="%s"' % html_lib.escape(style, quote=True) if style else ''
                )
                cell_parts.append('<div%s>%s</div>' % (style_attr, inner))
            fill = _cell_shading_fill(cell)
            border = _cell_border_color(cell)
            text_blob = ' '.join(
                (p.text or '').strip() for p in cell.paragraphs
            ).strip()
            css_parts = [
                'border:0.5pt solid #%s' % border,
                'padding:4px 6px',
                'vertical-align:top',
            ]
            if fill:
                css_parts.append('background-color:#%s' % fill)
            if row_height_pt and not text_blob:
                css_parts = [
                    'border:none',
                    'padding:0',
                    'height:%spt' % max(row_height_pt, 5.0),
                    'line-height:%spt' % max(row_height_pt, 5.0),
                    'font-size:1pt',
                ]
                if fill:
                    css_parts.append('background-color:#%s' % fill)
                cell_parts = ['&nbsp;']
            style_css = ';'.join(css_parts)
            cells_html.append(
                '<td style="%s">%s</td>'
                % (style_css, ''.join(cell_parts) or '&nbsp;')
            )
        rows_html.append('<tr>%s</tr>' % ''.join(cells_html))
    # Escape literal % in CSS (width:100%%) so %-formatting below is safe.
    return (
        '<table class="o_clm_docx_table" style="border-collapse:collapse;'
        'width:100%%;margin:0 0 12pt;">%s</table>' % ''.join(rows_html)
    )


def build_ordered_document_preview_html(raw_bytes):
    """Read-only HTML of a DOCX with paragraphs and tables in document order.

    Used by Contract Preview / Version Preview so Format 1 and Format 4 show
    the same structure as the Word file (including Parties / Terms tables).
    Does not change the tag editor, which still uses paragraph-index payloads.
    """
    document = load_document_bytes(raw_bytes)
    doc_part = document.part
    parts = []
    headers = extract_header_blocks(document)
    if headers:
        parts.append('<div class="o_clm_docx_header o_clm_docx_hdr">')
        for index, row in enumerate(headers):
            style = row.get('style_css') or ''
            style_attr = (
                ' style="%s"' % html_lib.escape(style, quote=True) if style else ''
            )
            inner = row.get('html') or '<br/>'
            parts.append(
                '<div class="o_clm_docx_header_line o_clm_docx_hdr" '
                'data-header-index="%s"%s>%s</div>'
                % (index, style_attr, inner)
            )
        # No <hr> here — DOCX/PDF have no grey rule under the running header.
        # A separator used to make Preview diverge from the downloaded files.
        parts.append('</div>')

    para_index = 0
    for kind, block in iter_document_block_items(document):
        if kind == 'p':
            style = _paragraph_block_css(block)
            style_attr = (
                ' style="%s"' % html_lib.escape(style, quote=True) if style else ''
            )
            inner = _paragraph_html(block, doc_part) or '<br/>'
            parts.append(
                '<p data-clm-index="%s"%s>%s</p>' % (para_index, style_attr, inner)
            )
            para_index += 1
        else:
            parts.append(_table_preview_html(block, doc_part))

    if not parts:
        parts.append('<p data-clm-index="0"><br/></p>')
    return ''.join(parts)


def payloads_from_editor_html(html_document):
    """
    Parse Wysiwyg HTML into ordered paragraph payloads.

    Skips protected header blocks. Maps each top-level block to one DOCX paragraph.
    """
    try:
        from lxml import html as lxml_html
    except ImportError as error:
        raise UserError("HTML editor save requires the 'lxml' package.") from error

    html_document = (html_document or '').strip() or '<p><br/></p>'
    try:
        root = lxml_html.fragment_fromstring(html_document, create_parent='div')
    except Exception:
        root = lxml_html.fromstring('<div>%s</div>' % html_document)

    block_tags = {
        'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'pre', 'blockquote',
        'div', 'table', 'ul', 'ol',
    }

    def _iter_blocks(node):
        for child in node:
            if not isinstance(child.tag, str):
                continue
            tag = child.tag.lower()
            if tag in ('script', 'style'):
                continue
            classes = child.get('class') or ''
            if 'o_clm_docx_hdr' in classes or 'o_clm_docx_header' in classes:
                continue
            if tag in block_tags:
                # Unwrap nested editor wrappers that only contain blocks
                if tag == 'div' and not (child.text or '').strip():
                    nested = [c for c in child if isinstance(c.tag, str) and c.tag.lower() in block_tags]
                    if nested and len(nested) == len(list(child)):
                        yield from _iter_blocks(child)
                        continue
                yield child
            elif tag == 'img':
                # Bare images (logo-only paragraphs) still become a body payload.
                yield child

    payloads = []
    for child in _iter_blocks(root):
        tag = child.tag.lower()
        style_css = child.get('style') or ''
        if tag == 'img':
            payloads.append({
                'text': '',
                'html': lxml_html.tostring(child, encoding='unicode'),
                'style_css': '',
            })
            continue
        if tag in ('ul', 'ol'):
            # Expand list items as separate paragraphs (DOCX body v1).
            for li in child.findall('.//li'):
                inner_parts = []
                if li.text:
                    inner_parts.append(html_lib.escape(li.text))
                for sub in li:
                    inner_parts.append(
                        lxml_html.tostring(sub, encoding='unicode', with_tail=True)
                    )
                payloads.append({
                    'text': (li.text_content() or '').replace('\xa0', ' '),
                    'html': ''.join(inner_parts).strip() or '<br/>',
                    'style_css': style_css,
                })
            continue
        if tag == 'table':
            # Keep table markup as a single paragraph HTML blob (best-effort).
            payloads.append({
                'text': (child.text_content() or '').replace('\xa0', ' '),
                'html': lxml_html.tostring(child, encoding='unicode'),
                'style_css': style_css,
            })
            continue

        inner_parts = []
        if child.text:
            inner_parts.append(html_lib.escape(child.text))
        for sub in child:
            inner_parts.append(
                lxml_html.tostring(sub, encoding='unicode', with_tail=True)
            )
        inner_html = ''.join(inner_parts).strip() or '<br/>'
        payloads.append({
            'text': (child.text_content() or '').replace('\xa0', ' '),
            'html': inner_html,
            'style_css': style_css,
        })

    if not payloads:
        payloads.append({'text': '', 'html': '<br/>', 'style_css': ''})
    return payloads


def _snapshot_body_drawing_runs(document):
    """Deep-copy drawing/pict runs per body paragraph before a body rebuild.

    Image parts stay in the package; restoring these runs by rId keeps logos
    when the HTML editor drops ``<img>`` or leaves non-data blob: URLs.
    """
    from copy import deepcopy

    snapshots = []
    for paragraph in iter_body_paragraphs(document):
        drawings = []
        for run in paragraph.runs:
            if _run_has_drawing(run):
                drawings.append(deepcopy(run._element))
        # Rare: drawing markup hanging directly under the paragraph.
        if not drawings:
            from docx.oxml.ns import qn
            p_el = paragraph._p
            for child in list(p_el):
                if child.tag != qn('w:r'):
                    continue
                if (
                    child.findall('.//' + qn('w:drawing'))
                    or child.findall('.//' + qn('w:pict'))
                    or child.findall('.//{urn:schemas-microsoft-com:vml}imagedata')
                ):
                    drawings.append(deepcopy(child))
        snapshots.append(drawings)
    return snapshots


def _paragraph_has_drawing(paragraph):
    if any(_run_has_drawing(run) for run in paragraph.runs):
        return True
    from docx.oxml.ns import qn
    p_el = paragraph._p
    return bool(
        p_el.findall('.//' + qn('w:drawing'))
        or p_el.findall('.//' + qn('w:pict'))
        or p_el.findall('.//{urn:schemas-microsoft-com:vml}imagedata')
    )


def _restore_drawing_runs(paragraph, drawing_elements):
    """Append preserved drawing runs onto a paragraph (package rIds still valid)."""
    if not drawing_elements:
        return
    for el in drawing_elements:
        paragraph._p.append(el)


def replace_body_from_payloads(raw_bytes, payloads):
    """Replace all body paragraphs with the given ordered payloads (headers untouched).

    Logos/images are preserved even when the Wysiwyg HTML loses ``data:`` URIs:
    original body drawing runs are snapshotted and restored onto matching
    paragraph indexes that end up without images after the HTML rebuild.
    """
    from docx.oxml.ns import qn

    document = load_document_bytes(raw_bytes)
    drawing_snapshots = _snapshot_body_drawing_runs(document)
    body = document.element.body
    for child in list(body):
        if child.tag == qn('w:sectPr'):
            continue
        body.remove(child)

    for index, payload in enumerate(payloads or []):
        if not isinstance(payload, dict):
            payload = {'text': payload or ''}
        paragraph = document.add_paragraph()
        html = payload.get('html')
        text = payload.get('text') or ''
        style_css = payload.get('style_css') or payload.get('styleCss') or ''
        if html:
            _set_paragraph_from_html(
                paragraph, html, style_css=style_css, plain_text=text
            )
        else:
            _set_paragraph_text(paragraph, text)
            if style_css:
                _apply_paragraph_alignment(paragraph, style_css)

        # HTML rebuild often drops logos (blob: src, sanitizer, etc.). Put the
        # original drawings back when this paragraph index still has no image.
        if (
            index < len(drawing_snapshots)
            and drawing_snapshots[index]
            and not _paragraph_has_drawing(paragraph)
        ):
            _restore_drawing_runs(paragraph, drawing_snapshots[index])

    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def b64_from_bytes(raw):
    return base64.b64encode(raw)
