# Part of NextContract. See LICENSE file for full copyright and licensing details.
"""Convert Word (.doc / .docx) bytes to PDF via LibreOffice."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile

from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

_SOFFICE_CANDIDATES = (
    'soffice',
    'libreoffice',
    '/usr/bin/soffice',
    '/usr/bin/libreoffice',
    '/usr/lib/libreoffice/program/soffice',
    '/usr/lib/libreoffice/program/soffice.bin',
)

# OLE Compound Document — legacy .doc / older Office binaries.
_OLE_MAGIC = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'


def find_soffice():
    """Return path to LibreOffice / soffice, or None."""
    for name in _SOFFICE_CANDIDATES:
        path = shutil.which(name) if os.path.sep not in name else (
            name if os.path.isfile(name) and os.access(name, os.X_OK) else None
        )
        if path:
            return path
    return None


def guess_word_extension(raw_bytes, filename=None):
    """Return ``.docx`` or ``.doc`` from magic bytes / filename, or ``None``."""
    name = (filename or '').lower()
    if raw_bytes and raw_bytes[:2] == b'PK':
        return '.docx'
    if raw_bytes and raw_bytes[:8] == _OLE_MAGIC:
        return '.doc'
    if name.endswith('.docx'):
        return '.docx'
    if name.endswith('.doc'):
        return '.doc'
    return None


def docx_bytes_to_pdf(docx_bytes, timeout=180):
    """
    Convert DOCX bytes → PDF bytes using LibreOffice headless.

    Raises UserError when LibreOffice is missing or conversion fails.
    """
    return word_bytes_to_pdf(docx_bytes, filename='document.docx', timeout=timeout)


def word_bytes_to_pdf(raw_bytes, filename=None, timeout=180):
    """
    Convert ``.doc`` / ``.docx`` bytes → PDF via LibreOffice headless.

    Raises UserError when the file is not Word, LibreOffice is missing, or
    conversion fails.
    """
    if not raw_bytes:
        raise UserError("Cannot convert to PDF: the signed document is empty.")

    ext = guess_word_extension(raw_bytes, filename=filename)
    if not ext:
        raise UserError(
            "Cannot convert to PDF: the signed document is not a Word "
            "(.doc / .docx) file."
        )
    if ext == '.docx' and raw_bytes[:2] != b'PK':
        raise UserError("Cannot convert to PDF: the document is not a valid .docx file.")

    soffice = find_soffice()
    if not soffice:
        raise UserError(
            "Previewing a signed Word document as PDF requires LibreOffice "
            "on the Odoo server.\n\n"
            "Install it (Debian/Ubuntu): apt-get install -y libreoffice-writer-nogui\n"
            "Then restart Odoo and try View Signed PDF again."
        )

    with tempfile.TemporaryDirectory(prefix='clm_word2pdf_') as tmp:
        input_path = os.path.join(tmp, 'document' + ext)
        with open(input_path, 'wb') as handle:
            handle.write(raw_bytes)

        # Isolated user profile — required when soffice runs as non-root / in Docker.
        profile_dir = os.path.join(tmp, 'lo_profile')
        os.makedirs(profile_dir, exist_ok=True)
        profile_uri = 'file://%s' % profile_dir

        cmd = [
            soffice,
            '--headless',
            '--nologo',
            '--nofirststartwizard',
            '--norestore',
            '-env:UserInstallation=%s' % profile_uri,
            '--convert-to', 'pdf:writer_pdf_Export',
            '--outdir', tmp,
            input_path,
        ]
        try:
            completed = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                timeout=timeout,
                env={
                    **os.environ,
                    'HOME': tmp,
                    'SAL_USE_VCLPLUGIN': 'svp',
                },
            )
        except subprocess.TimeoutExpired as error:
            raise UserError(
                "LibreOffice timed out while converting the signed document to PDF."
            ) from error
        except OSError as error:
            raise UserError(
                "Could not start LibreOffice to convert the signed document to PDF: %s"
                % error
            ) from error

        pdf_path = os.path.join(tmp, 'document.pdf')
        if completed.returncode != 0 or not os.path.isfile(pdf_path):
            stderr = (completed.stderr or b'').decode('utf-8', errors='replace')[:800]
            stdout = (completed.stdout or b'').decode('utf-8', errors='replace')[:400]
            _logger.error(
                'LibreOffice Word→PDF failed (code=%s). stdout=%s stderr=%s',
                completed.returncode, stdout, stderr,
            )
            raise UserError(
                "LibreOffice could not convert the signed document to PDF.\n%s"
                % (stderr or stdout or 'No details from LibreOffice.')
            )

        with open(pdf_path, 'rb') as handle:
            pdf = handle.read()
        if not pdf or pdf[:4] != b'%PDF':
            raise UserError(
                "LibreOffice produced an invalid PDF from the signed document."
            )
        return pdf
