# -*- coding: utf-8 -*-
# Copyright 2026 Nextbridge. License OPL-1.
"""Access control: interface restrictions (menus, fields, view elements, chatter), model blocks and record rules.

Menus/fields/elements/chatter change what the web interface shows. Model blocks are enforced by Odoo's access
check for every request (interface and API). Record rules are regular Odoo record rules.
"""
import re

from lxml import etree

from odoo import _, api, fields, models, tools
from odoo.exceptions import ValidationError

RULE_KINDS = [
    ("menu_hide", "Hide menus"),
    ("menu_only", "Only show these menus"),
    ("field", "Restrict fields"),
    ("element", "Hide buttons / tabs / elements"),
    ("chatter", "Hide chatter"),
    ("model_block", "Block a model"),
    ("record_rule", "Record rule"),
]


class McpAccessRule(models.Model):
    _name = "nxb.mcp.access.rule"
    _description = "AI Connector: access restriction"
    _order = "id desc"

    name = fields.Char(required=True)
    kind = fields.Selection(RULE_KINDS, required=True)
    active = fields.Boolean(default=True)
    user_ids = fields.Many2many("res.users", "nxb_mcp_access_rule_user_rel", string="Users")
    group_ids = fields.Many2many("res.groups", "nxb_mcp_access_rule_group_rel", string="Groups")
    menu_ids = fields.Many2many("ir.ui.menu", "nxb_mcp_access_rule_menu_rel", string="Menus")
    model_id = fields.Many2one("ir.model", ondelete="cascade")
    model = fields.Char("Model name", related="model_id.model", store=True)
    field_ids = fields.Many2many("ir.model.fields", "nxb_mcp_access_rule_field_rel", string="Fields")
    field_mode = fields.Selection([("hidden", "Hidden"), ("readonly", "Read-only")], default="hidden")
    selectors = fields.Text("Elements", help="One per line, e.g. button[name=action_confirm] or page[name=sales].")
    block_read = fields.Boolean("No reading")
    block_write = fields.Boolean("No editing")
    block_create = fields.Boolean("No creating")
    block_unlink = fields.Boolean("No deleting")
    ir_rule_id = fields.Many2one("ir.rule", "Record rule", ondelete="cascade")
    domain = fields.Text(related="ir_rule_id.domain_force", readonly=False)
    created_by_ai = fields.Boolean("Created by the assistant", readonly=True)
    note = fields.Text()

    @api.constrains("kind", "user_ids", "group_ids", "menu_ids", "model_id", "field_ids", "selectors")
    def _check_rule(self):
        admin_group = self.env.ref("base.group_system")
        for rec in self:
            if rec.kind != "record_rule" and not (rec.user_ids or rec.group_ids):
                raise ValidationError(_("Choose the users or groups this restriction applies to."))
            if admin_group in rec.group_ids or rec.group_ids.filtered(lambda g: admin_group in g.trans_implied_ids):
                raise ValidationError(_("Restrictions cannot target the Administrator group."))
            if rec.kind in ("menu_hide", "menu_only") and not rec.menu_ids:
                raise ValidationError(_("Choose at least one menu."))
            if rec.kind in ("field", "element", "chatter", "model_block") and not rec.model_id:
                raise ValidationError(_("Choose a model."))
            if rec.kind == "field" and not rec.field_ids:
                raise ValidationError(_("Choose at least one field."))
            if rec.kind == "element" and not rec._selector_list():
                raise ValidationError(_("Give at least one element."))
            if rec.kind == "model_block" and not (rec.block_read or rec.block_write or rec.block_create
                                                   or rec.block_unlink):
                raise ValidationError(_("Choose at least one operation to block."))

    def _selector_list(self):
        return [s.strip() for s in (self.selectors or "").splitlines() if s.strip()]

    # ------------------------------------------------------------------ caches
    @api.model_create_multi
    def create(self, vals_list):
        res = super().create(vals_list)
        self.env.registry.clear_cache()
        return res

    def write(self, vals):
        res = super().write(vals)
        self.env.registry.clear_cache()
        return res

    def unlink(self):
        rules = self.mapped("ir_rule_id")
        res = super().unlink()
        rules.sudo().unlink()
        self.env.registry.clear_cache()
        return res

    @api.model
    @tools.ormcache("uid")
    def _rules_for_uid(self, uid):
        """All active restrictions that apply to a user, as plain data (cached per user)."""
        user = self.env["res.users"].sudo().browse(uid)
        if not user.exists() or user._is_superuser() or user.has_group("base.group_system"):
            return ()
        groups = set(user.groups_id.ids)
        out = []
        for r in self.sudo().search([("active", "=", True), ("kind", "!=", "record_rule")]):
            if uid in r.user_ids.ids or groups & set(r.group_ids.ids):
                out.append({
                    "kind": r.kind, "model": r.model or "",
                    "menus": tuple(r.menu_ids.ids),
                    "fields": tuple(r.field_ids.mapped("name")), "mode": r.field_mode,
                    "selectors": tuple(r._selector_list()),
                    "block": tuple(op for op, on in (("read", r.block_read), ("write", r.block_write),
                                                     ("create", r.block_create), ("unlink", r.block_unlink)) if on),
                })
        return tuple(out)

    def _describe(self):
        self.ensure_one()
        d = {"rule_id": self.id, "name": self.name, "kind": self.kind, "active": self.active,
             "users": self.user_ids.mapped("login"), "groups": self.group_ids.mapped("full_name")}
        if self.menu_ids:
            d["menus"] = self.menu_ids.mapped("complete_name")
        if self.model:
            d["model"] = self.model
        if self.field_ids:
            d["fields"] = self.field_ids.mapped("name")
            d["mode"] = self.field_mode
        if self.selectors:
            d["elements"] = self._selector_list()
        if self.kind == "model_block":
            d["blocked"] = [op for op, on in (("read", self.block_read), ("write", self.block_write),
                                              ("create", self.block_create), ("unlink", self.block_unlink)) if on]
        if self.ir_rule_id:
            d["domain"] = self.ir_rule_id.domain_force
            d["record_rule_groups"] = self.ir_rule_id.groups.mapped("full_name")
        return d


# ---------------------------------------------------------------------------- enforcement: menus
class IrUiMenu(models.Model):
    _inherit = "ir.ui.menu"

    def _visible_menu_ids(self, debug=False):
        visible = super()._visible_menu_ids(debug=debug)
        rules = self.env["nxb.mcp.access.rule"]._rules_for_uid(self.env.uid)
        if not rules:
            return visible
        visible = set(visible)
        only = [r for r in rules if r["kind"] == "menu_only"]
        if only:
            allowed_roots = self.sudo().browse([m for r in only for m in r["menus"]])
            allowed = set(self._nxb_descendants(allowed_roots.ids))
            for m in allowed_roots:
                parent = m.parent_id
                while parent:
                    allowed.add(parent.id)
                    parent = parent.parent_id
            visible &= allowed
        hidden_roots = [m for r in rules if r["kind"] == "menu_hide" for m in r["menus"]]
        if hidden_roots:
            visible -= set(self._nxb_descendants(hidden_roots))
        return frozenset(visible)

    def _nxb_descendants(self, root_ids):
        """Ids of the given menus and all their sub-menus (no visibility filtering, no recursion)."""
        Menu = self.sudo().with_context(**{"ir.ui.menu.full_list": True, "active_test": False})
        return Menu.search([("id", "child_of", list(root_ids))]).ids


# ---------------------------------------------------------------------------- enforcement: model blocks
class IrModelAccess(models.Model):
    _inherit = "ir.model.access"

    @tools.ormcache("self.env.uid", "mode")
    def _get_allowed_models(self, mode="read"):
        allowed = super()._get_allowed_models(mode)
        blocked = {r["model"] for r in self.env["nxb.mcp.access.rule"]._rules_for_uid(self.env.uid)
                   if r["kind"] == "model_block" and mode in r["block"]}
        return frozenset(allowed - blocked) if blocked else allowed


# ---------------------------------------------------------------------------- enforcement: views
_SEL = re.compile(r"^\s*([a-zA-Z_][\w\-]*)\s*\[\s*([\w\-:]+)\s*=\s*['\"]?([^'\"\]]*)['\"]?\s*\]\s*$")


def selector_to_xpath(sel):
    sel = (sel or "").strip()
    if sel.startswith("/") or sel.startswith("("):
        return sel
    m = _SEL.match(sel)
    if not m:
        if re.match(r"^[a-zA-Z_][\w\-]*$", sel):
            return "//%s" % sel
        raise ValueError("Unsupported element selector %r. Use tag[attribute=value]." % sel)
    tag, attr, value = m.groups()
    if "'" in value:
        raise ValueError("Quotes are not allowed in selector values.")
    return "//%s[@%s='%s']" % (tag, attr, value)


class Base(models.AbstractModel):
    _inherit = "base"

    @api.model
    def get_view(self, view_id=None, view_type="form", **options):
        result = super().get_view(view_id, view_type, **options)
        if self.env.su or not self.env.uid:
            return result
        try:
            rules = self.env["nxb.mcp.access.rule"]._rules_for_uid(self.env.uid)
        except KeyError:  # registry loading before our model exists
            return result
        rules = [r for r in rules if r["model"] == self._name and r["kind"] in ("field", "element", "chatter")]
        if not rules or not result.get("arch"):
            return result
        try:
            arch = etree.fromstring(result["arch"])
        except etree.XMLSyntaxError:
            return result
        is_list = arch.tag in ("list", "tree")
        for r in rules:
            if r["kind"] == "field":
                for fname in r["fields"]:
                    for node in arch.xpath("//field[@name='%s']" % fname):
                        if r["mode"] == "readonly":
                            node.set("readonly", "1")
                            node.set("force_save", "1")
                        else:
                            parent_is_list = node.getparent() is not None and node.getparent().tag in ("list", "tree")
                            node.set("column_invisible" if (is_list or parent_is_list) else "invisible", "1")
                            if node.get("required"):
                                node.set("required", "0")
            elif r["kind"] == "element":
                for sel in r["selectors"]:
                    try:
                        nodes = arch.xpath(selector_to_xpath(sel))
                    except (ValueError, etree.XPathError):
                        continue
                    for node in nodes:
                        node.set("invisible", "1")
            elif r["kind"] == "chatter":
                for node in arch.xpath("//chatter | //div[contains(concat(' ', normalize-space(@class), ' '), "
                                       "' oe_chatter ')]"):
                    node.getparent().remove(node)
        result["arch"] = etree.tostring(arch, encoding="unicode")
        return result
