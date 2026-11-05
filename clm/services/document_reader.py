# Part of NextContract. See LICENSE file for full copyright and licensing details.
"""Document reading: attachment in, DocumentPayload out.

This module is the single owner of file-type detection and content
extraction. AI engines must never sniff mimetypes or parse documents
themselves — they receive a payload and reason about its contents.

Replaces the former ocr_engine.py, whose responsibility this is.
"""

import base64
import io
import logging
from dataclasses import dataclass, field
from typing import Optional

from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

TEXT_SOURCES = ('native', 'ocr', 'vlm', 'none')

_DOCX_MIMETYPE = (
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
)

# Below this many characters, a PDF's "text layer" is almost certainly
# incidental artefacts rather than real content — treat it as a scan.
_MIN_MEANINGFUL_TEXT_CHARS = 20


@dataclass
class DocumentPage:
    page_number: int
    image: bytes
    mimetype: str


@dataclass
class DocumentPayload:
    mimetype: str
    page_count: int = 0
    text: Optional[str] = None
    text_source: str = 'none'
    pages: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


class DocumentReader:
    """Base interface for all document readers."""

    def __init__(self, env):
        self.env = env

    def can_read(self, attachment):
        raise NotImplementedError

    def read(self, attachment, want_images=False):
        raise NotImplementedError

    @staticmethod
    def _decoded(attachment):
        return base64.b64decode(attachment.datas)


class NativeDocumentReader(DocumentReader):
    """DOCX, plain text, and PDFs that carry their own text layer."""

    def can_read(self, attachment):
        mimetype = attachment.mimetype or ''
        return (
            _DOCX_MIMETYPE in mimetype
            or mimetype.startswith('text/')
            or mimetype == 'application/pdf'
        )

    def read(self, attachment, want_images=False):
        mimetype = attachment.mimetype or ''
        if _DOCX_MIMETYPE in mimetype:
            return self._read_docx(attachment)
        if mimetype.startswith('text/'):
            return DocumentPayload(
                mimetype=mimetype, page_count=1, text_source='native',
                text=self._decoded(attachment).decode('utf-8', errors='replace'),
            )
        return self._read_pdf(attachment, want_images)

    def _read_docx(self, attachment):
        try:
            import docx
        except ImportError:
            raise UserError(
                "Reading DOCX files requires the 'python-docx' library. "
                "Install it on the Odoo server: pip install python-docx"
            )
        document = docx.Document(io.BytesIO(self._decoded(attachment)))
        text = '\n'.join(p.text for p in document.paragraphs if p.text.strip())
        return DocumentPayload(
            mimetype=attachment.mimetype, page_count=0,
            text=text, text_source='native' if text else 'none',
        )

    def _read_pdf(self, attachment, want_images):
        pdf = _open_pdf(self._decoded(attachment))
        page_count = len(pdf)

        extracted = []
        for index in range(page_count):
            textpage = pdf[index].get_textpage()
            extracted.append(textpage.get_text_range())
        text = '\n'.join(part for part in extracted if part.strip())

        has_text_layer = len(text.strip()) >= _MIN_MEANINGFUL_TEXT_CHARS
        payload = DocumentPayload(
            mimetype=attachment.mimetype,
            page_count=page_count,
            text=text if has_text_layer else None,
            text_source='native' if has_text_layer else 'none',
        )
        if want_images:
            payload.pages = _render_pages(pdf)
        return payload


class RasterDocumentReader(DocumentReader):
    """Scanned PDFs consumed as page images by a multimodal engine."""

    def can_read(self, attachment):
        return (attachment.mimetype or '') == 'application/pdf'

    def read(self, attachment, want_images=True):
        pdf = _open_pdf(self._decoded(attachment))
        return DocumentPayload(
            mimetype=attachment.mimetype,
            page_count=len(pdf),
            text=None,
            text_source='none',
            pages=_render_pages(pdf) if want_images else [],
        )


class TesseractDocumentReader(DocumentReader):
    """Self-hosted OCR path. Requires the Tesseract system binary."""

    def can_read(self, attachment):
        return (attachment.mimetype or '') in (
            'application/pdf', 'image/png', 'image/jpeg', 'image/tiff',
        )

    def read(self, attachment, want_images=False):
        try:
            import pytesseract
            from PIL import Image
        except ImportError:
            raise UserError(
                "The self-hosted OCR path requires 'pytesseract' and 'Pillow', "
                "plus the Tesseract system binary (apt-get install tesseract-ocr).\n"
                "Either install them or configure a cloud AI provider instead."
            )
        pdf = _open_pdf(self._decoded(attachment))
        pages = _render_pages(pdf)
        chunks = [
            pytesseract.image_to_string(Image.open(io.BytesIO(page.image)))
            for page in pages
        ]
        text = '\n'.join(chunk for chunk in chunks if chunk.strip())
        return DocumentPayload(
            mimetype=attachment.mimetype,
            page_count=len(pages),
            text=text or None,
            text_source='ocr' if text else 'none',
            pages=pages if want_images else [],
        )


def _open_pdf(raw_bytes):
    try:
        import pypdfium2
    except ImportError:
        raise UserError(
            "Reading PDF files requires the 'pypdfium2' library. "
            "Install it on the Odoo server: pip install pypdfium2"
        )
    try:
        return pypdfium2.PdfDocument(raw_bytes)
    except Exception as error:
        raise UserError("Could not open the PDF document: %s" % error)


def _render_pages(pdf, scale=2):
    """Rasterise every page to PNG bytes.

    scale=2 gives roughly 144 DPI, which is the practical floor for reading
    small print in a scanned contract.
    """
    pages = []
    for index in range(len(pdf)):
        bitmap = pdf[index].render(scale=scale)
        buffer = io.BytesIO()
        bitmap.to_pil().save(buffer, format='PNG')
        pages.append(DocumentPage(
            page_number=index + 1,
            image=buffer.getvalue(),
            mimetype='image/png',
        ))
    return pages


def resolve_reader(env, attachment, want_images=False):
    """Pick the reader for this attachment.

    Order matters. Native handles anything with real text cheaply; Raster is
    the multimodal path for scans; Tesseract is only reached when an engine
    needs text and the document has none.
    """
    for reader_class in (NativeDocumentReader, RasterDocumentReader):
        reader = reader_class(env)
        if reader.can_read(attachment):
            return reader
    raise UserError(
        "Cannot read '%s' (%s).\n\n"
        "Supported formats are DOCX, PDF, and plain text."
        % (attachment.name, attachment.mimetype or 'unknown type')
    )
