# -*- coding: utf-8 -*-
# Copyright 2026 Nextbridge. License OPL-1.
"""Access control tools (administrators only)."""
from lxml import etree

from ..core.context import ToolError
from ..core.registry import S_DOMAIN, s_arr, s_bool, s_int, s_str, tool
from ..models.mcp_access import selector_to_xpath

S_USERS = s_arr("Users (login or name) the rule applies to.", {"type": "string"})
S_GROUPS = s_arr("Groups (full name like 'Sales / User: Own Documents Only', or xml id like "
                 "'sales_team.group_sale_salesman').", {"type": "string"})


def _env(ctx):
    env = ctx.require_env("Access control")
    if not env.user.has_group("base.group_system"):
        raise ToolError("Access control tools need an Odoo administrator.")
    return env


def _users(env, refs, allow_admin=False):
    out = env["res.users"].browse()
    for ref in refs or []:
        if isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit()):
            u = env["res.users"].browse(int(ref)).exists()
        else:
            u = env["res.users"].search([("login", "=ilike", ref)], limit=1) or \
                env["res.users"].search([("name", "ilike", ref)], limit=2)
            if len(u) > 1:
                raise ToolError("Several users match %r: %s. Use the login." % (ref, ", ".join(u.mapped("login"))))
        if not u:
            raise ToolError("No user matches %r." % ref)
        if not allow_admin and u.has_group("base.group_system"):
            raise ToolError("%s is an administrator; restrictions never apply to administrators." % u.name)
        out |= u
    return out


def _groups(env, refs):
    out = env["res.groups"].browse()
    for ref in refs or []:
        g = env.ref(ref, raise_if_not_found=False) if "." in ref and " " not in ref else None
        if not g or g._name != "res.groups":
            g = env["res.groups"].search([("full_name", "=ilike", ref)], limit=1) or \
                env["res.groups"].search([("full_name", "ilike", ref)], limit=2)
        if len(g) > 1:
            raise ToolError("Several groups match %r: %s." % (ref, "; ".join(g.mapped("full_name"))))
        if not g:
            raise ToolError("No group matches %r. Use access_groups_list." % ref)
        out |= g
    return out


def _targets(env, users, groups):
    u, g = _users(env, users), _groups(env, groups)
    if not u and not g:
        raise ToolError("Say who the rule applies to: users and/or groups.")
    if env.user in u:
        raise ToolError("You cannot restrict your own user.")
    return u, g


def _model(env, name):
    m = env["ir.model"]._get(name)
    if not m:
        raise ToolError("Model %r does not exist." % name)
    return m


def _new_rule(env, vals):
    vals["created_by_ai"] = True
    rule = env["nxb.mcp.access.rule"].create(vals)
    return {"created": rule._describe(), "note": "Takes effect when the user reloads Odoo."}


# ---------------------------------------------------------------------------- users & groups
@tool("access_users_find", "access", "Find users",
      "Find Odoo users by name or login and show their groups.",
      properties={"text": s_str("Name or login contains."), "limit": s_int("Maximum results.", 20, 1, 200),
                  "include_portal": s_bool("Include portal/public users.", False)})
def access_users_find(ctx, text=None, limit=20, include_portal=False):
    env = _env(ctx)
    dom = [] if include_portal else [("share", "=", False)]
    if text:
        dom += ["|", ("name", "ilike", text), ("login", "ilike", text)]
    users = env["res.users"].search(dom, limit=ctx.cap_limit(limit, 20))
    return [{"id": u.id, "name": u.name, "login": u.login, "active": u.active, "admin": u.has_group("base.group_system"),
             "groups": u.groups_id.filtered(lambda g: g.category_id).mapped("full_name")} for u in users]


@tool("access_groups_list", "access", "List groups",
      "List user groups (roles), optionally only one application, with the number of users.",
      properties={"text": s_str("Group or application name contains.")})
def access_groups_list(ctx, text=None):
    env = _env(ctx)
    dom = [("full_name", "ilike", text)] if text else [("category_id", "!=", False)]
    groups = env["res.groups"].search(dom, limit=300)
    data = {g.full_name: g for g in groups}
    return [{"name": n, "xml_id": g.get_external_id().get(g.id) or "", "users": len(g.users)}
            for n, g in sorted(data.items())]


@tool("access_user_summary", "access", "Explain a user's access",
      "Explain what a user can do: groups, restrictions from this app, record rules from their groups, and "
      "whether they can read/create/edit/delete a given model.",
      properties={"user": s_str("Login or name."), "model": s_str("Optional model to check, e.g. sale.order.")},
      required=("user",))
def access_user_summary(ctx, user, model=None):
    env = _env(ctx)
    u = _users(env, [user], allow_admin=True)
    out = {"user": u.name, "login": u.login, "administrator": u.has_group("base.group_system"),
           "groups": u.groups_id.filtered(lambda g: g.category_id).mapped("full_name")}
    rules = env["nxb.mcp.access.rule"].search(["|", ("user_ids", "in", u.ids), ("group_ids", "in", u.groups_id.ids)])
    out["restrictions_from_this_app"] = [r._describe() for r in rules]
    if model:
        _model(env, model)
        Access = env["ir.model.access"].with_user(u)
        out["model_access"] = {op: Access.check(model, op, raise_exception=False)
                               for op in ("read", "write", "create", "unlink")}
        irules = env["ir.rule"].search([("model_id.model", "=", model), "|", ("groups", "=", False),
                                        ("groups", "in", u.groups_id.ids)])
        out["record_rules"] = [{"name": r.name, "domain": r.domain_force, "global": r["global"]} for r in irules]
    return out


@tool("access_user_create", "access", "Create a user",
      "Create an internal user with a login and optional groups. The user sets a password via the invitation "
      "email (if outgoing mail is configured) or an administrator sets it in Odoo.",
      properties={"name": s_str("Full name."), "login": s_str("Login, usually the email."),
                  "email": s_str("Email (defaults to the login)."), "groups": S_GROUPS},
      required=("name", "login"), writes=True)
def access_user_create(ctx, name, login, email=None, groups=None):
    env = _env(ctx)
    if env["res.users"].with_context(active_test=False).search_count([("login", "=ilike", login)]):
        raise ToolError("A user with login %r already exists." % login)
    g = _groups(env, groups)
    if env.ref("base.group_system") in g:
        raise ToolError("Administrator rights must be granted by a person in Odoo, not by the assistant.")
    user = env["res.users"].with_context(no_reset_password=True).create({
        "name": name, "login": login, "email": email or (login if "@" in login else False),
        "groups_id": [(4, env.ref("base.group_user").id)] + [(4, x.id) for x in g]})
    return {"created": {"id": user.id, "name": user.name, "login": user.login,
                        "groups": user.groups_id.filtered(lambda x: x.category_id).mapped("full_name")}}


@tool("access_user_groups", "access", "Change a user's groups",
      "Add groups to a user or remove groups from them.",
      properties={"user": s_str("Login or name."), "add": S_GROUPS, "remove": S_GROUPS},
      required=("user",), writes=True)
def access_user_groups(ctx, user, add=None, remove=None):
    env = _env(ctx)
    u = _users(env, [user])
    a, r = _groups(env, add), _groups(env, remove)
    if env.ref("base.group_system") in a:
        raise ToolError("Administrator rights must be granted by a person in Odoo, not by the assistant.")
    if not a and not r:
        raise ToolError("Nothing to add or remove.")
    u.write({"groups_id": [(4, x.id) for x in a] + [(3, x.id) for x in r]})
    return {"user": u.login, "groups": u.groups_id.filtered(lambda x: x.category_id).mapped("full_name")}


# ---------------------------------------------------------------------------- menus
@tool("access_menus_find", "access", "Find menus",
      "Find menus by name and return their full path and id (use the ids in the menu rules).",
      properties={"text": s_str("Menu name contains."), "top_level_only": s_bool("Only application menus.", False)})
def access_menus_find(ctx, text=None, top_level_only=False):
    env = _env(ctx)
    dom = []
    if text:
        dom.append(("name", "ilike", text))
    if top_level_only:
        dom.append(("parent_id", "=", False))
    return [{"id": m.id, "path": m.complete_name} for m in env["ir.ui.menu"].search(dom, limit=100)]


def _menus(env, refs):
    out = env["ir.ui.menu"].browse()
    for ref in refs or []:
        if isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit()):
            m = env["ir.ui.menu"].browse(int(ref)).exists()
        else:
            m = env["ir.ui.menu"].search([("complete_name", "=ilike", ref)], limit=1) or \
                env["ir.ui.menu"].search([("name", "=ilike", ref)], limit=2)
            if len(m) > 1:
                raise ToolError("Several menus are named %r: %s. Use the id." % (ref, "; ".join(
                    "%s (%s)" % (x.complete_name, x.id) for x in m)))
        if not m:
            raise ToolError("Menu %r not found. Use access_menus_find." % ref)
        out |= m
    return out


@tool("access_menus_hide", "access", "Hide menus",
      "Hide menus (and everything under them) for some users or groups.",
      properties={"menus": s_arr("Menu ids or full paths like 'Sales/Configuration'.", {"type": "string"}),
                  "users": S_USERS, "groups": S_GROUPS, "name": s_str("Name for this rule.")},
      required=("menus",), writes=True)
def access_menus_hide(ctx, menus, users=None, groups=None, name=None):
    env = _env(ctx)
    u, g = _targets(env, users, groups)
    m = _menus(env, menus)
    return _new_rule(env, {"name": name or "Hide %s" % ", ".join(m.mapped("name")), "kind": "menu_hide",
                           "user_ids": [(6, 0, u.ids)], "group_ids": [(6, 0, g.ids)], "menu_ids": [(6, 0, m.ids)]})


@tool("access_menus_only", "access", "Only show some apps",
      "Limit users or groups to the given menus/apps: every other menu disappears for them.",
      properties={"menus": s_arr("Menus (usually apps) that stay visible.", {"type": "string"}),
                  "users": S_USERS, "groups": S_GROUPS, "name": s_str("Name for this rule.")},
      required=("menus",), writes=True)
def access_menus_only(ctx, menus, users=None, groups=None, name=None):
    env = _env(ctx)
    u, g = _targets(env, users, groups)
    m = _menus(env, menus)
    return _new_rule(env, {"name": name or "Only %s" % ", ".join(m.mapped("name")), "kind": "menu_only",
                           "user_ids": [(6, 0, u.ids)], "group_ids": [(6, 0, g.ids)], "menu_ids": [(6, 0, m.ids)]})


# ---------------------------------------------------------------------------- fields & elements
@tool("access_fields_restrict", "access", "Hide or lock fields",
      "Hide fields, or make them read-only, in the screens of a model for some users or groups. This changes the "
      "interface; for data security also use a model block or record rule.",
      properties={"model": s_str("Model, e.g. res.partner."),
                  "fields": s_arr("Field names.", {"type": "string"}),
                  "mode": s_str("hidden or readonly.", ["hidden", "readonly"]),
                  "users": S_USERS, "groups": S_GROUPS, "name": s_str("Name for this rule.")},
      required=("model", "fields", "mode"), writes=True)
def access_fields_restrict(ctx, model, fields, mode, users=None, groups=None, name=None):
    env = _env(ctx)
    u, g = _targets(env, users, groups)
    m = _model(env, model)
    flds = env["ir.model.fields"].search([("model", "=", model), ("name", "in", fields)])
    missing = set(fields) - set(flds.mapped("name"))
    if missing:
        raise ToolError("Unknown fields on %s: %s." % (model, ", ".join(sorted(missing))))
    return _new_rule(env, {"name": name or "%s %s on %s" % (mode.title(), ", ".join(fields), model), "kind": "field",
                           "model_id": m.id, "field_ids": [(6, 0, flds.ids)], "field_mode": mode,
                           "user_ids": [(6, 0, u.ids)], "group_ids": [(6, 0, g.ids)]})


@tool("access_view_elements", "access", "List buttons and tabs of a screen",
      "List the buttons, tabs, smart buttons and fields in a model's form or list screen, with the selector to use "
      "in access_elements_hide.",
      properties={"model": s_str("Model."), "view_type": s_str("form or list.", ["form", "list"], "form")},
      required=("model",))
def access_view_elements(ctx, model, view_type="form"):
    env = _env(ctx)
    _model(env, model)
    arch = etree.fromstring(env[model].sudo().get_view(view_type=view_type)["arch"])
    out = {"buttons": [], "tabs": [], "fields": []}
    for b in arch.xpath("//button"):
        key = b.get("name")
        if not key:
            continue
        out["buttons"].append({"label": b.get("string") or (b.xpath("string(.)") or "").strip()[:60] or key,
                               "selector": "button[name=%s]" % key})
    for p in arch.xpath("//page"):
        if p.get("name"):
            out["tabs"].append({"label": p.get("string") or p.get("name"), "selector": "page[name=%s]" % p.get("name")})
        elif p.get("string"):
            out["tabs"].append({"label": p.get("string"), "selector": "page[string=%s]" % p.get("string")})
    seen = set()
    for f in arch.xpath("//field[@name]"):
        if f.get("name") not in seen and not f.xpath("ancestor::field"):
            seen.add(f.get("name"))
            out["fields"].append(f.get("name"))
    out["has_chatter"] = bool(arch.xpath("//chatter"))
    return out


@tool("access_elements_hide", "access", "Hide buttons or tabs",
      "Hide buttons, tabs or other elements of a model's screens for some users or groups.",
      properties={"model": s_str("Model."),
                  "elements": s_arr("Selectors from access_view_elements, e.g. button[name=action_confirm].",
                                    {"type": "string"}),
                  "users": S_USERS, "groups": S_GROUPS, "name": s_str("Name for this rule.")},
      required=("model", "elements"), writes=True)
def access_elements_hide(ctx, model, elements, users=None, groups=None, name=None):
    env = _env(ctx)
    u, g = _targets(env, users, groups)
    m = _model(env, model)
    for sel in elements:
        try:
            selector_to_xpath(sel)
        except ValueError as e:
            raise ToolError(str(e))
    return _new_rule(env, {"name": name or "Hide elements on %s" % model, "kind": "element", "model_id": m.id,
                           "selectors": "\n".join(elements), "user_ids": [(6, 0, u.ids)],
                           "group_ids": [(6, 0, g.ids)]})


@tool("access_chatter_hide", "access", "Hide the chatter",
      "Hide the message/activity panel (chatter) of a model's form for some users or groups.",
      properties={"model": s_str("Model."), "users": S_USERS, "groups": S_GROUPS},
      required=("model",), writes=True)
def access_chatter_hide(ctx, model, users=None, groups=None):
    env = _env(ctx)
    u, g = _targets(env, users, groups)
    m = _model(env, model)
    return _new_rule(env, {"name": "Hide chatter on %s" % model, "kind": "chatter", "model_id": m.id,
                           "user_ids": [(6, 0, u.ids)], "group_ids": [(6, 0, g.ids)]})


# ---------------------------------------------------------------------------- data security
@tool("access_model_block", "access", "Block a model",
      "Forbid reading, editing, creating and/or deleting records of a model for some users or groups. Enforced "
      "everywhere (screens, imports, API), on top of Odoo's normal rights.",
      properties={"model": s_str("Model."),
                  "operations": s_arr("Operations to block.", {"type": "string",
                                                              "enum": ["read", "write", "create", "unlink"]}),
                  "users": S_USERS, "groups": S_GROUPS, "name": s_str("Name for this rule.")},
      required=("model", "operations"), writes=True)
def access_model_block(ctx, model, operations, users=None, groups=None, name=None):
    env = _env(ctx)
    u, g = _targets(env, users, groups)
    m = _model(env, model)
    if model.startswith(("ir.", "res.users", "res.groups", "nxb.mcp.")) and "read" in operations:
        raise ToolError("Blocking reads on technical models can break Odoo for these users.")
    ops = set(operations or [])
    return _new_rule(env, {"name": name or "Block %s on %s" % ("/".join(sorted(ops)), model), "kind": "model_block",
                           "model_id": m.id, "block_read": "read" in ops, "block_write": "write" in ops,
                           "block_create": "create" in ops, "block_unlink": "unlink" in ops,
                           "user_ids": [(6, 0, u.ids)], "group_ids": [(6, 0, g.ids)]})


@tool("access_record_rule_add", "access", "Add a record rule",
      "Limit which records of a model some groups can see or change, with an Odoo domain. Example: salespeople see "
      "only their own customers: model res.partner, domain [[\"user_id\", \"=\", \"user.id\"]]. Use the string "
      "'user.id' or 'company_ids' for the current user's values.",
      properties={"model": s_str("Model."), "domain": S_DOMAIN, "groups": S_GROUPS,
                  "operations": s_arr("Which operations it limits (default all).",
                                      {"type": "string", "enum": ["read", "write", "create", "unlink"]}),
                  "name": s_str("Name for this rule.")},
      required=("model", "domain", "groups"), writes=True)
def access_record_rule_add(ctx, model, domain, groups, operations=None, name=None):
    env = _env(ctx)
    g = _groups(env, groups)
    if not g:
        raise ToolError("Record rules must target groups (global rules would affect everyone).")
    if env.ref("base.group_system") in g:
        raise ToolError("Record rules cannot target administrators.")
    m = _model(env, model)
    dom = repr(domain).replace("'user.id'", "user.id").replace("'company_ids'", "company_ids")\
        .replace("\"user.id\"", "user.id")
    ops = set(operations or ["read", "write", "create", "unlink"])
    from odoo.osv import expression
    from odoo.tools.safe_eval import safe_eval
    try:
        evaluated = safe_eval(dom, env["ir.rule"]._eval_context())
        with env.cr.savepoint():
            env[model].search_count(expression.normalize_domain(evaluated))
    except Exception as e:  # noqa: BLE001
        raise ToolError("The domain is not valid for %s: %s" % (model, e))
    rule = env["ir.rule"].create({"name": "[AI] " + (name or "Rule on %s" % model), "model_id": m.id,
                                  "domain_force": dom, "groups": [(6, 0, g.ids)],
                                  "perm_read": "read" in ops, "perm_write": "write" in ops,
                                  "perm_create": "create" in ops, "perm_unlink": "unlink" in ops})
    return _new_rule(env, {"name": name or "Rule on %s" % model, "kind": "record_rule", "model_id": m.id,
                           "ir_rule_id": rule.id, "group_ids": [(6, 0, g.ids)]})


# ---------------------------------------------------------------------------- listing & removal
@tool("access_rules_list", "access", "List restrictions",
      "List the access restrictions created with this app (menus, fields, elements, chatter, model blocks, record "
      "rules), optionally for one model or user.",
      properties={"model": s_str("Only this model."), "user": s_str("Only rules that apply to this user."),
                  "include_inactive": s_bool("Include switched-off rules.", False)})
def access_rules_list(ctx, model=None, user=None, include_inactive=False):
    env = _env(ctx)
    dom = []
    if model:
        dom.append(("model", "=", model))
    if user:
        u = _users(env, [user], allow_admin=True)
        dom += ["|", ("user_ids", "in", u.ids), ("group_ids", "in", u.groups_id.ids)]
    rules = env["nxb.mcp.access.rule"].with_context(active_test=not include_inactive).search(dom)
    return [r._describe() for r in rules]


@tool("access_rule_switch", "access", "Switch a restriction on or off",
      "Temporarily disable or re-enable a restriction without deleting it.",
      properties={"rule_id": s_int("Restriction id."), "active": s_bool("true = on, false = off.")},
      required=("rule_id", "active"), writes=True)
def access_rule_switch(ctx, rule_id, active):
    env = _env(ctx)
    rule = env["nxb.mcp.access.rule"].with_context(active_test=False).browse(int(rule_id)).exists()
    if not rule:
        raise ToolError("Restriction %s does not exist." % rule_id)
    rule.active = bool(active)
    if rule.ir_rule_id:
        rule.ir_rule_id.active = bool(active)
    return rule._describe()


@tool("access_rule_remove", "access", "Remove a restriction",
      "Delete a restriction created with this app (and its record rule, if any).",
      properties={"rule_id": s_int("Restriction id.")}, required=("rule_id",), writes=True)
def access_rule_remove(ctx, rule_id):
    env = _env(ctx)
    rule = env["nxb.mcp.access.rule"].with_context(active_test=False).browse(int(rule_id)).exists()
    if not rule:
        raise ToolError("Restriction %s does not exist." % rule_id)
    name = rule.name
    rule.unlink()
    return {"removed": name}
