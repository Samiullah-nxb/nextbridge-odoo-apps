# -*- coding: utf-8 -*-
# Copyright 2026 Nextbridge. License OPL-1.
from . import core
from . import tools
from . import models
from . import controllers
from . import wizard


def _post_init(env):
    env["nxb.mcp.connection"]._ensure_this()


def _uninstall(env):
    """Remove every access restriction first: record rules created by the assistant live in ir.rule and
    would otherwise survive the uninstallation."""
    rules = env["nxb.mcp.access.rule"].with_context(active_test=False).sudo().search([])
    rules.unlink()  # also deletes the linked ir.rule records
    env.registry.clear_cache()
