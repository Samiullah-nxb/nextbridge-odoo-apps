# -*- coding: utf-8 -*-
import logging
import re

from . import main


class _HideKeyInLogs(logging.Filter):
    """Never write connector keys that appear in URLs to the server log."""
    _rx = re.compile(r"(/mcp(?:/db/[^/\s]+)?/key/)[^\s/\"?]+")

    def filter(self, record):
        try:
            if isinstance(record.msg, str) and "/key/" in record.msg:
                record.msg = self._rx.sub(r"\1***", record.msg)
            if record.args:
                record.args = tuple(self._rx.sub(r"\1***", a) if isinstance(a, str) else a for a in record.args)
        except Exception:  # noqa: BLE001
            pass
        return True


for _name in ("werkzeug", "odoo.http"):
    _lg = logging.getLogger(_name)
    if not any(isinstance(f, _HideKeyInLogs) for f in _lg.filters):
        _lg.addFilter(_HideKeyInLogs())
