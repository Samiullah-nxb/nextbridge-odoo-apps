# -*- coding: utf-8 -*-
# Copyright 2026 Nextbridge. License OPL-1.
"""Hub, data and SQL tools."""
import re

from odoo.tools import mute_logger

from ..core.context import ToolError, to_json
from ..core.registry import (PACKS, S_CONFIRM, S_DOMAIN, S_FIELDS, S_IDS, S_MODEL, S_VALUES, TOOLS,
                             s_arr, s_bool, s_int, s_obj, s_str, tool)

IMPORTANT_FIELDS = ("name", "display_name", "state", "stage_id", "partner_id", "user_id", "email", "phone",
                    "date", "date_order", "invoice_date", "create_date", "amount_total", "amount_residual",
                    "list_price", "qty_available", "expected_revenue", "probability", "company_id", "active",
                    "default_code", "ref", "origin", "payment_state", "type", "move_type")
SKIP_TYPES = ("binary", "html", "properties", "properties_definition", "json")


def _default_fields(ctx, model):
    info = ctx.orm.fields_get(model, ["type", "store", "required"])
    chosen = [f for f in IMPORTANT_FIELDS if f in info]
    if len(chosen) < 6:
        for fname, meta in info.items():
            if len(chosen) >= 10:
                break
            if fname not in chosen and meta.get("required") and meta.get("type") not in SKIP_TYPES \
                    and not fname.startswith(("message_", "activity_", "website_message")):
                chosen.append(fname)
    return chosen[:14] or ["display_name"]


def _norm_domain(domain):
    if domain in (None, "", False):
        return []
    if isinstance(domain, str):
        import ast
        try:
            domain = ast.literal_eval(domain)
        except (ValueError, SyntaxError):
            raise ToolError("The domain must be a list such as [[\"name\", \"ilike\", \"acme\"]].")
    if not isinstance(domain, (list, tuple)):
        raise ToolError("The domain must be a list.")
    return [list(d) if isinstance(d, (list, tuple)) else d for d in domain]


# ============================================================================ hub
@tool("hub_whoami", "hub", "Who am I connected as",
      "Return the Odoo user, database, access level and the tool packs that are switched on. Call this "
      "first if you are unsure what you may do.", remote_ok=True)
def hub_whoami(ctx):
    from ..core.server import pack_enabled
    info = ctx.orm.user_info()
    p = ctx.caller
    return {
        "user": info["name"], "login": info["login"], "company": info["company"],
        "database": ctx.connection["code"],
        "access": "read only" if (p.readonly or ctx.settings["strict_readonly"]) else "read and write",
        "administrator": p.is_admin,
        "packs_on": [PACKS[k].title for k in PACKS if pack_enabled(ctx.hub_env, k)],
    }


@tool("hub_databases", "hub", "List connected databases",
      "List every Odoo database you can work with, with its code (use it as the 'database' argument of the "
      "other tools), what it is, and whether all tool packs or only basic record tools work there.",
      properties={"check": s_bool("Also test each connection now (slower).", False)}, remote_ok=True)
def hub_databases(ctx, check=False):
    server = ctx.server
    conns = server.connections()
    default = server.default_connection()
    out = []
    for c in conns:
        if check:
            c.sudo()._test_connection()
        out.append({
            "code": c.code, "name": c.name, "where": c._kind_label(), "default": c == default,
            "database_name": c.db_name or ctx.hub_env.cr.dbname,
            "all_packs": c._full_support(),
            "status": c.state, "note": c.note or "",
            **({"status_message": c.state_message} if check else {}),
        })
    return {"databases": out}


@tool("hub_capabilities", "hub", "What can you do here",
      "Explain which features are available in a database: switched-on tool packs, installed business apps "
      "(Sales, CRM, Invoicing, Inventory, Website, ...) and any limits. Use it to answer 'what can you do?'.",
      properties={"database": s_str("Database code (omit for the default).")}, remote_ok=True)
def hub_capabilities(ctx, database=None):
    from ..core.server import pack_enabled
    server = ctx.server
    conn = server._resolve_connection(database)
    target = server._run(TOOLS["hub_installed_apps"], conn, {})
    packs = []
    for key, pack in PACKS.items():
        packs.append({"pack": pack.title, "on": pack_enabled(ctx.hub_env, key), "about": pack.summary,
                      "needs_app": pack.requires or None,
                      "tools": [t.name for t in TOOLS.values() if t.pack == key]})
    return {"database": conn.code, "installed_apps": target, "packs": packs,
            "read_only": bool(ctx.caller.readonly or ctx.settings["strict_readonly"]),
            "deleting_allowed": ctx.settings["allow_delete"]}


@tool("hub_installed_apps", "read", "List installed apps",
      "List the business applications installed in a database (technical name and title).", remote_ok=True)
def hub_installed_apps(ctx):
    rows = ctx.orm.search_read("ir.module.module", [("state", "=", "installed"), ("application", "=", True)],
                               ["name", "shortdesc"], 200, order="shortdesc")
    return [{"name": r["name"], "title": r["shortdesc"]} for r in rows]


@tool("hub_search_all", "hub", "Search every database",
      "Run the same search on several connected databases at once and return the results per database. "
      "Ideal for comparisons such as 'open invoices in every company database'.",
      properties={"model": S_MODEL, "domain": S_DOMAIN, "fields": S_FIELDS,
                  "limit": s_int("Maximum records per database.", 20, 1, 200),
                  "count_only": s_bool("Only return the number of matching records per database.", False),
                  "databases": s_arr("Database codes to include (default: all).", {"type": "string"})},
      required=("model",), remote_ok=True)
def hub_search_all(ctx, model, domain=None, fields=None, limit=20, count_only=False, databases=None):
    server = ctx.server
    conns = server.connections()
    if databases:
        conns = conns.filtered(lambda c: c.code in databases)
    results = []
    for conn in conns:
        try:
            if count_only:
                res = server._run(TOOLS["data_count"], conn, {"model": model, "domain": domain or []})
            else:
                res = server._run(TOOLS["data_search"], conn, {"model": model, "domain": domain or [],
                                                               "fields": fields, "limit": limit})
            results.append({"database": conn.code, "name": conn.name, "result": res})
        except Exception as e:  # noqa: BLE001
            results.append({"database": conn.code, "name": conn.name, "error": str(e)})
    return {"model": model, "per_database": results}


# ============================================================================ read
@tool("data_models", "read", "Find models",
      "Find the technical model name for a business object (e.g. 'invoice' → account.move, 'customer' → "
      "res.partner). Searches model names and descriptions.",
      properties={"search": s_str("Words to look for, e.g. 'sale order' or 'stock'."),
                  "limit": s_int("Maximum results.", 30, 1, 200)}, remote_ok=True)
def data_models(ctx, search=None, limit=30):
    domain = [("transient", "=", False)]
    if search:
        for word in search.split():
            domain += ["|", ("model", "ilike", word), ("name", "ilike", word)]
    rows = ctx.orm.search_read("ir.model", domain, ["model", "name", "info"], ctx.cap_limit(limit, 30),
                               order="model")
    return [{"model": r["model"], "name": r["name"], "about": (r.get("info") or "")[:160]} for r in rows]


@tool("data_fields", "read", "Describe fields",
      "List the fields of a model with type, label, whether they are required/read-only, the related model "
      "for links and the allowed values for selections. Use before creating or filtering records.",
      properties={"model": S_MODEL, "search": s_str("Only fields whose name or label contains this text."),
                  "required_only": s_bool("Only required fields.", False),
                  "include_technical": s_bool("Include message_*, activity_* and similar technical fields.",
                                              False)},
      required=("model",), remote_ok=True)
def data_fields(ctx, model, search=None, required_only=False, include_technical=False):
    info = ctx.orm.fields_get(model, ["string", "type", "required", "readonly", "relation", "selection",
                                      "help", "store"])
    out = []
    for name, meta in sorted(info.items()):
        if not include_technical and name.startswith(("message_", "activity_", "website_message", "__")):
            continue
        if required_only and not meta.get("required"):
            continue
        if search and search.lower() not in (name + " " + str(meta.get("string", ""))).lower():
            continue
        row = {"name": name, "label": meta.get("string"), "type": meta.get("type")}
        if meta.get("required"):
            row["required"] = True
        if meta.get("readonly"):
            row["readonly"] = True
        if meta.get("relation"):
            row["relation"] = meta["relation"]
        if meta.get("selection") and isinstance(meta["selection"], list):
            row["values"] = [s[0] for s in meta["selection"]][:30]
        if meta.get("store") is False:
            row["computed"] = True
        out.append(row)
    return {"model": model, "count": len(out), "fields": out}


@tool("data_search", "read", "Search records",
      "Search records of any model with an Odoo domain and return the chosen fields plus the total number of "
      "matches. Relations come back as [id, name]. Use small limits and page with offset.",
      properties={"model": S_MODEL, "domain": S_DOMAIN, "fields": S_FIELDS,
                  "limit": s_int("Maximum records (default 50).", 50, 1, 500),
                  "offset": s_int("Records to skip, for paging.", 0, 0),
                  "order": s_str("Sort order, e.g. 'date_order desc, id'.")},
      required=("model",), remote_ok=True)
def data_search(ctx, model, domain=None, fields=None, limit=50, offset=0, order=None):
    domain = _norm_domain(domain)
    fields = fields or _default_fields(ctx, model)
    limit = ctx.cap_limit(limit)
    records = ctx.orm.search_read(model, domain, fields, limit, offset=offset or 0, order=order or None)
    total = ctx.orm.search_count(model, domain)
    return {"model": model, "total": total, "returned": len(records), "offset": offset or 0,
            "records": records}


@tool("data_count", "read", "Count records",
      "Count the records matching a domain.",
      properties={"model": S_MODEL, "domain": S_DOMAIN}, required=("model",), remote_ok=True)
def data_count(ctx, model, domain=None):
    return {"model": model, "count": ctx.orm.search_count(model, _norm_domain(domain))}


@tool("data_read", "read", "Read records by id",
      "Read specific records by id with the chosen fields (all readable fields if none are given).",
      properties={"model": S_MODEL, "ids": S_IDS, "fields": S_FIELDS}, required=("model", "ids"),
      remote_ok=True)
def data_read(ctx, model, ids, fields=None):
    if not ids:
        raise ToolError("Give at least one id.")
    if not fields:
        info = ctx.orm.fields_get(model, ["type"])
        fields = [f for f, m in info.items() if m.get("type") not in SKIP_TYPES
                  and not f.startswith(("message_", "activity_", "website_message", "__"))]
    return {"model": model, "records": ctx.orm.read(model, [int(i) for i in ids][:200], fields)}


@tool("data_lookup", "read", "Find a record by name",
      "Find records by (part of) their name, the way Odoo's search boxes do. Returns [id, name] pairs. Use it "
      "to get the id of a customer, product, user, etc. before creating or updating linked records.",
      properties={"model": S_MODEL, "text": s_str("Name or part of it."),
                  "limit": s_int("Maximum results.", 10, 1, 100)}, required=("model", "text"), remote_ok=True)
def data_lookup(ctx, model, text, limit=10):
    res = ctx.orm.call(model, "name_search", None, [text], {"limit": ctx.cap_limit(limit, 10)})
    return [{"id": r[0], "name": r[1]} for r in res]


@tool("data_summarise", "read", "Summarise / group records",
      "Aggregate records: totals, averages, counts grouped by fields or by date periods. Examples: revenue "
      "per month (group_by ['date_order:month'], measures ['amount_total:sum']), leads per stage.",
      properties={"model": S_MODEL, "domain": S_DOMAIN,
                  "group_by": s_arr("Fields to group by; dates accept :day :week :month :quarter :year.",
                                    {"type": "string"}),
                  "measures": s_arr("Aggregates like 'amount_total:sum', 'id:count', 'price:avg', 'qty:max'.",
                                    {"type": "string"}),
                  "order": s_str("Order, e.g. 'amount_total desc'."),
                  "limit": s_int("Maximum groups.", 50, 1, 500)},
      required=("model",), remote_ok=True)
def data_summarise(ctx, model, domain=None, group_by=None, measures=None, order=None, limit=50):
    measures = measures or ["__count"]
    fields = [m if ":" in m or m == "__count" else "%s:sum" % m for m in measures]
    fields = ["__count" if f in ("id:count", "__count") else f for f in fields]
    groups = ctx.orm.read_group(model, _norm_domain(domain), [f for f in fields if f != "__count"],
                                group_by or [], limit=ctx.cap_limit(limit), orderby=order or False, lazy=False)
    out = []
    for g in groups:
        row = {k: v for k, v in g.items() if not k.startswith("__") or k == "__count"}
        if "__count" in row:
            row["count"] = row.pop("__count")
        out.append(row)
    return {"model": model, "groups": out}


@tool("data_companies", "read", "List companies",
      "List the companies the user can work in (multi-company databases).", remote_ok=True)
def data_companies(ctx):
    return to_json(ctx.orm.companies())


# ============================================================================ write
@tool("data_create", "write", "Create records",
      "Create one or more records. For links use ids (many2one: 7; many2many/one2many: [[6, 0, [ids]]] or "
      "[[0, 0, {values}]] to create lines). Check required fields with the field tool first.",
      properties={"model": S_MODEL, "values": S_VALUES,
                  "records": s_arr("Several records to create at once (list of value objects).", {"type": "object"})},
      required=("model",), writes=True, remote_ok=True)
def data_create(ctx, model, values=None, records=None):
    vals_list = records or ([values] if values else [])
    if not vals_list:
        raise ToolError("Give 'values' (one record) or 'records' (several).")
    if len(vals_list) > 200:
        raise ToolError("Create at most 200 records per call.")
    ids = ctx.orm.create(model, vals_list)
    names = ctx.orm.read(model, ids, ["display_name"])
    return {"model": model, "created": [{"id": r["id"], "name": r["display_name"]} for r in names]}


@tool("data_update", "write", "Update records",
      "Change field values on existing records.",
      properties={"model": S_MODEL, "ids": S_IDS, "values": S_VALUES}, required=("model", "ids", "values"),
      writes=True, remote_ok=True)
def data_update(ctx, model, ids, values):
    if not values:
        raise ToolError("Nothing to change: 'values' is empty.")
    return ctx.orm.write(model, [int(i) for i in ids][:500], values)


@tool("data_delete", "write", "Delete records",
      "Permanently delete records. Prefer archiving (set active=false) when the model supports it. You must "
      "first tell the user exactly what will be deleted, then call with confirm=true.",
      properties={"model": S_MODEL, "ids": S_IDS, "confirm": S_CONFIRM}, required=("model", "ids", "confirm"),
      writes=True, destructive=True, remote_ok=True)
def data_delete(ctx, model, ids, confirm=False):
    return ctx.orm.unlink(model, [int(i) for i in ids][:200])


@tool("data_run_action", "write", "Run a model action",
      "Call a public method of a model, e.g. action_confirm on sale.order, action_post on account.move, "
      "button_validate on stock.picking. Private methods (starting with _) are not allowed.",
      properties={"model": S_MODEL, "method": s_str("Method name, e.g. action_confirm."),
                  "ids": s_arr("Records to run it on (omit for model-level methods).", {"type": "integer"}),
                  "args": s_arr("Extra positional arguments."),
                  "kwargs": s_obj("Extra keyword arguments.")},
      required=("model", "method"), writes=True, remote_ok=True)
def data_run_action(ctx, model, method, ids=None, args=None, kwargs=None):
    if not method or method.startswith("_"):
        raise ToolError("Private methods (starting with _) cannot be called.")
    if method in ("unlink", "sudo", "with_user", "with_env", "invalidate_cache", "init"):
        raise ToolError("Method %r is not allowed here; use the dedicated tools." % method)
    res = ctx.orm.call(model, method, [int(i) for i in ids] if ids else None, args, kwargs)
    if isinstance(res, dict) and res.get("type", "").startswith("ir.actions"):
        return {"done": True, "odoo_opened_a_window": {k: res.get(k) for k in ("name", "res_model", "res_id", "type")
                                                         if res.get(k)}}
    return {"done": True, "result": res if res is not True else "ok"}


@tool("data_add_note", "write", "Post a note or message",
      "Post an internal note (default) or a message to followers in the chatter of a record.",
      properties={"model": S_MODEL, "id": s_int("Record id."), "body": s_str("Text of the note."),
                  "send_to_followers": s_bool("Send as a message to followers instead of an internal note.",
                                              False)},
      required=("model", "id", "body"), writes=True, remote_ok=True)
def data_add_note(ctx, model, id, body, send_to_followers=False):
    subtype = "mail.mt_comment" if send_to_followers else "mail.mt_note"
    try:
        res = ctx.orm.call(model, "message_post", [int(id)], [], {"body": body, "message_type": "comment",
                                                                  "subtype_xmlid": subtype})
    except ToolError as e:
        if "message_post" in str(e) and "does not exist" in str(e):
            raise ToolError("Records of %s have no chatter here (the Discuss app is not installed, or this model "
                            "does not keep messages), so a note cannot be added." % model)
        raise
    return {"posted": True, "message": res}


# ============================================================================ SQL
_SQL_FORBIDDEN = re.compile(
    r"\b(copy|pg_read_file|pg_read_binary_file|pg_ls_dir|pg_stat_file|lo_import|lo_export|lo_get|lo_put|"
    r"dblink\w*|pg_terminate_backend|pg_cancel_backend|set_config|pg_sleep|pg_reload_conf|"
    r"pg_logdir_ls|query_to_xml)\b", re.I)


def _ro_cursor(ctx):
    env = ctx.require_env("SQL tools")
    cr = env.registry.cursor()
    cr.execute("SET TRANSACTION READ ONLY")
    cr.execute("SET LOCAL statement_timeout = 15000")
    return cr


@tool("sql_tables", "sql", "List database tables",
      "List PostgreSQL tables of the database with their approximate row count (administrators only).",
      properties={"search": s_str("Only tables whose name contains this text.")}, admin_only=True)
def sql_tables(ctx, search=None):
    cr = _ro_cursor(ctx)
    try:
        cr.execute("""SELECT c.relname, c.reltuples::bigint FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                      WHERE c.relkind='r' AND n.nspname='public' AND (%s IS NULL OR c.relname ILIKE %s)
                      ORDER BY c.relname LIMIT 500""", (search, "%%%s%%" % (search or "")))
        return [{"table": r[0], "approx_rows": max(int(r[1]), 0)} for r in cr.fetchall()]
    finally:
        cr.rollback()
        cr.close()


@tool("sql_table_info", "sql", "Describe a table",
      "Columns, types, nullability and foreign keys of a PostgreSQL table, plus its exact row count.",
      properties={"table": s_str("Table name, e.g. sale_order.")}, required=("table",), admin_only=True)
def sql_table_info(ctx, table):
    cr = _ro_cursor(ctx)
    try:
        cr.execute("""SELECT column_name, data_type, is_nullable, column_default FROM information_schema.columns
                      WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position""", (table,))
        cols = [{"column": r[0], "type": r[1], "nullable": r[2] == "YES", "default": r[3]} for r in cr.fetchall()]
        if not cols:
            raise ToolError("Table %r does not exist." % table)
        cr.execute("""SELECT kcu.column_name, ccu.table_name FROM information_schema.table_constraints tc
                      JOIN information_schema.key_column_usage kcu ON tc.constraint_name=kcu.constraint_name
                      JOIN information_schema.constraint_column_usage ccu ON ccu.constraint_name=tc.constraint_name
                      WHERE tc.constraint_type='FOREIGN KEY' AND tc.table_name=%s""", (table,))
        fks = {r[0]: r[1] for r in cr.fetchall()}
        for c in cols:
            if c["column"] in fks:
                c["references"] = fks[c["column"]]
        from psycopg2 import sql as psql
        cr.execute(psql.SQL("SELECT count(*) FROM {}").format(psql.Identifier(table)))
        return {"table": table, "rows": cr.fetchone()[0], "columns": cols}
    finally:
        cr.rollback()
        cr.close()


@tool("sql_query", "sql", "Run a read-only SQL query",
      "Run one SELECT (or WITH ... SELECT) query. The database enforces read-only: anything that would change "
      "data fails. 15 second time limit. Prefer the record tools; they respect access rights, SQL does not.",
      properties={"query": s_str("A single SELECT statement."),
                  "limit": s_int("Maximum rows returned.", 200, 1, 2000)},
      required=("query",), admin_only=True)
def sql_query(ctx, query, limit=200):
    q = (query or "").strip().rstrip(";").strip()
    if not re.match(r"^(select|with)\b", q, re.I):
        raise ToolError("Only SELECT queries are allowed.")
    if ";" in q:
        raise ToolError("Only one statement is allowed.")
    if _SQL_FORBIDDEN.search(q):
        raise ToolError("This query uses a function or command that is not allowed.")
    cr = _ro_cursor(ctx)
    try:
        try:
            with mute_logger("odoo.sql_db"):
                cr.execute(q)
        except Exception as e:  # noqa: BLE001
            raise ToolError("SQL error: %s" % str(e).strip().splitlines()[0])
        cols = [d[0] for d in cr.description or []]
        rows = cr.fetchmany(int(limit or 200))
        return {"columns": cols, "rows": to_json([list(r) for r in rows]), "returned": len(rows)}
    finally:
        cr.rollback()
        cr.close()
