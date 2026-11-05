# Part of NextContract. See LICENSE file for full copyright and licensing details.
"""Test document builders.

Fixtures are generated rather than committed as binaries so they stay
readable and cannot silently rot. python-docx is a runtime requirement;
weasyprint is needed only to build the PDF fixtures, so tests that use
them are skipped where it is not installed.
"""

import base64
import io
import unittest


def _weasyprint_html():
    try:
        from weasyprint import HTML
    except ImportError:
        raise unittest.SkipTest('weasyprint is not installed; it builds the PDF test fixtures')
    return HTML


def build_docx(paragraphs):
    """Return base64 DOCX bytes containing the given paragraphs."""
    import docx
    document = docx.Document()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    buffer = io.BytesIO()
    document.save(buffer)
    return base64.b64encode(buffer.getvalue())


def build_text_pdf(body_text):
    """Return base64 bytes of a PDF that has a real, extractable text layer."""
    HTML = _weasyprint_html()
    html = '<html><body><p>%s</p></body></html>' % body_text
    return base64.b64encode(HTML(string=html).write_pdf())


def build_scanned_pdf():
    """Return base64 bytes of a PDF with no text layer.

    The page holds only an embedded raster image, which is what a scanned
    contract looks like to a text extractor: zero characters.
    """
    HTML = _weasyprint_html()
    # 1x1 transparent PNG, scaled to fill the page.
    png_b64 = (
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk'
        'YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=='
    )
    html = (
        '<html><body style="margin:0">'
        '<img src="data:image/png;base64,%s" style="width:100%%;height:400px">'
        '</body></html>' % png_b64
    )
    return base64.b64encode(HTML(string=html).write_pdf())


def attach(env, datas, name, mimetype):
    """Create an ir.attachment carrying the given base64 payload."""
    return env['ir.attachment'].create({
        'name': name,
        'type': 'binary',
        'datas': datas,
        'mimetype': mimetype,
    })
