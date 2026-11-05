# Part of NextContract. See LICENSE file for full copyright and licensing details.
"""Native browser PDF Preview helpers.

Preview must display the actual final PDF via the browser's PDF viewer
(iframe / embed) — never rasterise pages to images.
"""

from __future__ import annotations

import logging
from urllib.parse import quote

from markupsafe import Markup, escape

_logger = logging.getLogger(__name__)


_PDFJS_NAMED_ZOOMS = frozenset({
    'auto', 'page-actual', 'page-fit', 'page-width',
})


def _pdfjs_zoom_values(zoom):
    """Return (hash_zoom, data-clm-pdf-zoom scale value) for PDF.js."""
    if isinstance(zoom, str):
        named = zoom.strip().lower()
        if named in _PDFJS_NAMED_ZOOMS:
            return named, named
    try:
        zoom_pct = max(10, min(400, int(zoom or 100)))
    except (TypeError, ValueError):
        zoom_pct = 100
    scale_value = ('%.4f' % (zoom_pct / 100.0)).rstrip('0').rstrip('.') or '1'
    return str(zoom_pct), scale_value


def pdf_attachment_preview_html(
    attachment_id, filename='document.pdf', cache_key=None, access_token=None,
    zoom=100,
):
    """Return Markup for in-app PDF preview via Odoo's PDF.js viewer.

    Direct ``/web/content`` iframes often fail in modal dialogs (empty body,
    304 cache). PDF.js loads the same bytes as Download with reliable rendering.

    :param zoom: Initial zoom. Percent int (100, 50) or PDF.js named mode
        (``page-width``, ``page-fit``, ``auto``, ``page-actual``). Comparison
        uses ``page-width`` so zoom tracks the pane/screen size.
    """
    if not attachment_id:
        raise ValueError('PDF attachment id is required for native preview.')
    safe_name = quote(filename or 'document.pdf', safe='')
    query = ['filename=%s' % safe_name]
    if cache_key:
        query.append('unique=%s' % quote(str(cache_key), safe=''))
    if access_token:
        query.append('access_token=%s' % quote(access_token, safe=''))
    content_url = '/web/content/%s?%s' % (int(attachment_id), '&'.join(query))
    encoded_route = quote(content_url, safe='')
    hash_zoom, scale_value = _pdfjs_zoom_values(zoom)
    # Prefer zoom before pagemode. PDF.js history may still override this; the
    # backend assets script re-applies the intended scale after the iframe loads.
    src = (
        '/web/static/lib/pdfjs/web/viewer.html?file=%s#zoom=%s&pagemode=none'
        % (encoded_route, quote(hash_zoom, safe='-'))
    )
    return Markup(
        '<div class="o_clm_pdf_preview" data-attachment-id="%s" data-clm-pdf-zoom="%s">'
        '<div class="o_clm_pdf_toolbar">'
        '<button type="button" class="btn btn-secondary btn-sm o_clm_pdf_fullscreen_btn" '
        'aria-pressed="false" title="Fullscreen preview">'
        '<i class="fa fa-expand me-1" aria-hidden="true"></i>'
        '<span class="o_clm_pdf_fullscreen_label">Fullscreen</span>'
        '</button>'
        '<a class="btn btn-link btn-sm o_clm_pdf_open_tab" href="%s" target="_blank" '
        'rel="noopener noreferrer">Open in new tab</a>'
        '</div>'
        '<div class="o_clm_pdf_viewer_frame">'
        '<iframe class="o_clm_pdf_iframe" src="%s" title="PDF Preview" '
        'allow="fullscreen" allowfullscreen="allowfullscreen">'
        '</iframe>'
        '</div>'
        '</div>'
    ) % (int(attachment_id), escape(scale_value), escape(content_url), escape(src))


def pdf_bytes_text_layer(pdf_bytes):
    """Extract plain text from PDF for accessibility / test markers (no images)."""
    if not pdf_bytes or not bytes(pdf_bytes).startswith(b'%PDF'):
        return ''
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return ''
    pdf = pdfium.PdfDocument(bytes(pdf_bytes))
    parts = []
    try:
        for index in range(len(pdf)):
            page = pdf[index]
            try:
                textpage = page.get_textpage()
                try:
                    parts.append(textpage.get_text_range() or '')
                finally:
                    textpage.close()
            except Exception:  # noqa: BLE001
                pass
            finally:
                try:
                    page.close()
                except Exception:  # noqa: BLE001
                    pass
    finally:
        try:
            pdf.close()
        except Exception:  # noqa: BLE001
            pass
    return '\n\n'.join(part for part in parts if part)
