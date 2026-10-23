# -*- coding: utf-8 -*-
import logging
import re

from . import mcp
from . import portal


class _MaskConnectorToken(logging.Filter):
    """Blank the connector token out of Odoo's request log."""
    _rx = re.compile(r"(/li/mcp/)[A-Za-z0-9_\-]+")

    def filter(self, record):
        try:
            if isinstance(record.msg, str) and "/li/mcp/" in record.msg:
                record.msg = self._rx.sub(r"\1***", record.msg)
            if record.args:
                record.args = tuple(self._rx.sub(r"\1***", a) if isinstance(a, str) else a for a in record.args)
        except Exception:  # noqa: BLE001
            pass
        return True


for _name in ("werkzeug", "odoo.http", "odoo.service.server"):
    _log = logging.getLogger(_name)
    if not any(isinstance(f, _MaskConnectorToken) for f in _log.filters):
        _log.addFilter(_MaskConnectorToken())
