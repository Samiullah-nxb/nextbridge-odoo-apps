# -*- coding: utf-8 -*-
# Copyright 2026 Nextbridge. License OPL-1.
"""Execution context handed to every tool, and a small ORM adapter that works the same
on a database of this server (direct ORM) and on a remote Odoo server (XML-RPC)."""
import xmlrpc.client
from datetime import date, datetime


class ToolError(Exception):
    """An error the assistant should read and act on (shown as a tool error, not a crash)."""


def to_json(value):
    """Make ORM results JSON-safe."""
    if isinstance(value, dict):
        return {k: to_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_json(v) for v in value]
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, bytes):
        return "<%d bytes>" % len(value)
    return value


class LocalOrm(object):
    """Adapter over an Odoo environment (this server)."""
    kind = "local"

    def __init__(self, env):
        self.env = env

    def model(self, name):
        if name not in self.env:
            raise ToolError("Model %r does not exist in this database. Use the model search tool to find "
                            "the right technical name." % name)
        return self.env[name]

    def fields_get(self, model, attributes=None):
        return self.model(model).fields_get(attributes=attributes)

    def search_read(self, model, domain, fields, limit, offset=0, order=None):
        return to_json(self.model(model).search_read(domain, fields or None, offset=offset, limit=limit,
                                                     order=order))

    def search_count(self, model, domain):
        return self.model(model).search_count(domain)

    def read(self, model, ids, fields):
        return to_json(self.model(model).browse(ids).exists().read(fields or None))

    def read_group(self, model, domain, fields, groupby, limit=None, orderby=False, lazy=False):
        """Grouped totals in the classic read_group shape ({group fields, measure: value, __count})."""
        aggs = ["__count"] + [f if ":" in f else "%s:sum" % f for f in fields]
        rows = self.model(model).formatted_read_group(domain, list(groupby or []), aggs, limit=limit,
                                                      order=orderby or None)
        out = []
        for row in rows:
            item = {}
            for key, value in row.items():
                if key == "__extra_domain":
                    continue
                if key in aggs and key != "__count":
                    key = key.split(":")[0]
                item[key] = value
            out.append(item)
        return to_json(out)

    def create(self, model, vals_list):
        return self.model(model).create(vals_list).ids

    def write(self, model, ids, vals):
        recs = self.model(model).browse(ids).exists()
        missing = sorted(set(ids) - set(recs.ids))
        recs.write(vals)
        return {"updated": recs.ids, "not_found": missing}

    def unlink(self, model, ids):
        recs = self.model(model).browse(ids).exists()
        missing = sorted(set(ids) - set(recs.ids))
        done = recs.ids
        recs.unlink()
        return {"deleted": done, "not_found": missing}

    def call(self, model, method, ids, args, kwargs):
        from odoo.service.model import get_public_method
        target = self.model(model).browse(ids) if ids else self.model(model)
        try:
            get_public_method(target, method)
        except AttributeError:
            raise ToolError("Method %r does not exist on %s." % (method, model))
        func = getattr(target, method)
        return to_json(_serialisable(func(*(args or []), **(kwargs or {}))))

    def companies(self):
        return self.env.user.company_ids.read(["name", "currency_id", "country_id"])

    def user_info(self):
        u = self.env.user
        return {"id": u.id, "name": u.name, "login": u.login, "company": u.company_id.name,
                "lang": u.lang, "tz": u.tz}

    def installed(self, module):
        return bool(self.env["ir.module.module"].sudo().search_count(
            [("name", "=", module), ("state", "=", "installed")]))


def _serialisable(value):
    """Convert recordsets returned by model methods into something readable."""
    try:
        from odoo.models import BaseModel
    except ImportError:  # pragma: no cover
        BaseModel = ()
    if isinstance(value, BaseModel):
        return {"model": value._name, "ids": value.ids}
    if isinstance(value, dict):
        return {k: _serialisable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialisable(v) for v in value]
    return value


class RemoteOrm(object):
    """Adapter over XML-RPC to another Odoo server (API key used as password)."""
    kind = "remote"

    def __init__(self, url, db, login, key, timeout=30):
        self.url = url.rstrip("/")
        self.db = db
        self.login = login
        self.key = key
        self.timeout = timeout
        self._uid = None

    def _proxy(self, path):
        transport = _TimeoutTransport(self.timeout) if self.url.startswith("http:") else \
            _TimeoutSafeTransport(self.timeout)
        return xmlrpc.client.ServerProxy("%s/xmlrpc/2/%s" % (self.url, path), allow_none=True,
                                         transport=transport)

    @property
    def uid(self):
        if self._uid is None:
            try:
                uid = self._proxy("common").authenticate(self.db, self.login, self.key, {})
            except Exception as e:  # noqa: BLE001
                raise ToolError("Could not reach the remote Odoo server %s: %s" % (self.url, e))
            if not uid:
                raise ToolError("The remote Odoo server refused the login %r for database %r. Check the "
                                "connection's login and API key." % (self.login, self.db))
            self._uid = uid
        return self._uid

    def _exec(self, model, method, args, kwargs=None):
        try:
            return self._proxy("object").execute_kw(self.db, self.uid, self.key, model, method, args,
                                                    kwargs or {})
        except xmlrpc.client.Fault as f:
            msg = f.faultString.strip().splitlines()[-1] if f.faultString else str(f)
            raise ToolError("Remote Odoo error: %s" % msg)
        except ToolError:
            raise
        except Exception as e:  # noqa: BLE001
            raise ToolError("Remote Odoo server error: %s" % e)

    def fields_get(self, model, attributes=None):
        return self._exec(model, "fields_get", [], {"attributes": attributes} if attributes else {})

    def search_read(self, model, domain, fields, limit, offset=0, order=None):
        kw = {"limit": limit, "offset": offset}
        if fields:
            kw["fields"] = fields
        if order:
            kw["order"] = order
        return self._exec(model, "search_read", [domain], kw)

    def search_count(self, model, domain):
        return self._exec(model, "search_count", [domain])

    def read(self, model, ids, fields):
        return self._exec(model, "read", [ids], {"fields": fields} if fields else {})

    def read_group(self, model, domain, fields, groupby, limit=None, orderby=False, lazy=False):
        return self._exec(model, "read_group", [domain, fields, groupby],
                          {"limit": limit, "orderby": orderby, "lazy": lazy})

    def create(self, model, vals_list):
        res = self._exec(model, "create", [vals_list])
        return res if isinstance(res, list) else [res]

    def write(self, model, ids, vals):
        existing = [r["id"] for r in self._exec(model, "read", [ids], {"fields": ["id"]})]
        self._exec(model, "write", [existing, vals])
        return {"updated": existing, "not_found": sorted(set(ids) - set(existing))}

    def unlink(self, model, ids):
        existing = [r["id"] for r in self._exec(model, "read", [ids], {"fields": ["id"]})]
        self._exec(model, "unlink", [existing])
        return {"deleted": existing, "not_found": sorted(set(ids) - set(existing))}

    def call(self, model, method, ids, args, kwargs):
        if method.startswith("_"):
            raise ToolError("Private methods (starting with _) cannot be called.")
        return self._exec(model, method, ([ids] if ids else []) + list(args or []), kwargs or {})

    def companies(self):
        user = self._exec("res.users", "read", [[self.uid]], {"fields": ["company_ids"]})[0]
        return self._exec("res.company", "read", [user["company_ids"]],
                          {"fields": ["name", "currency_id", "country_id"]})

    def user_info(self):
        u = self._exec("res.users", "read", [[self.uid]],
                       {"fields": ["name", "login", "company_id", "lang", "tz"]})[0]
        return {"id": u["id"], "name": u["name"], "login": u["login"],
                "company": u["company_id"] and u["company_id"][1], "lang": u["lang"], "tz": u["tz"]}

    def installed(self, module):
        try:
            return bool(self._exec("ir.module.module", "search_count",
                                   [[("name", "=", module), ("state", "=", "installed")]]))
        except ToolError:
            return False


class _TimeoutTransport(xmlrpc.client.Transport):
    def __init__(self, timeout):
        super().__init__()
        self._timeout = timeout

    def make_connection(self, host):
        conn = super().make_connection(host)
        conn.timeout = self._timeout
        return conn


class _TimeoutSafeTransport(xmlrpc.client.SafeTransport):
    def __init__(self, timeout):
        super().__init__()
        self._timeout = timeout

    def make_connection(self, host):
        conn = super().make_connection(host)
        conn.timeout = self._timeout
        return conn


class ToolContext(object):
    """Everything a tool may need.

    ``env`` is the Odoo environment of the *target* database (None on a plain RPC
    connection), ``orm`` the adapter, ``hub_env`` the environment of the database that
    received the MCP request, ``connection`` a dict describing the target.
    """

    def __init__(self, orm, env, hub_env, connection, caller, settings):
        self.orm = orm
        self.env = env
        self.hub_env = hub_env
        self.connection = connection
        self.caller = caller
        self.settings = settings
        self.server = None

    @property
    def is_remote(self):
        return self.env is None

    def require_env(self, what="This tool"):
        if self.env is None:
            raise ToolError("%s needs the AI Connector app installed on the target database. Connection %r "
                            "only allows basic record tools." % (what, self.connection.get("code")))
        return self.env

    def require_module(self, module, label):
        if not self.orm.installed(module):
            raise ToolError("The %s app (%s) is not installed in database %r." % (
                label, module, self.connection.get("code")))

    def cap_limit(self, limit, default=50):
        try:
            limit = int(limit or default)
        except (TypeError, ValueError):
            limit = default
        return max(1, min(limit, int(self.settings.get("max_records", 500))))
