# -*- coding: utf-8 -*-
from . import models
from . import controllers


def _uninstall(env):
    env["ir.config_parameter"].sudo().search([("key", "=like", "nxb_shared_user_login.%")]).unlink()
