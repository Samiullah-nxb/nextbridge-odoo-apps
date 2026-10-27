# -*- coding: utf-8 -*-
# Copyright 2026 Nextbridge. License OPL-1.
"""Connector keys, OAuth 2.1 clients/grants/tokens, sessions and the call log."""
import base64
import hashlib
import hmac
import secrets
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

KEY_PREFIX = "nxbm"


def _hash(secret):
    return hashlib.sha256((secret or "").encode()).hexdigest()


class McpKey(models.Model):
    _name = "nxb.mcp.key"
    _description = "AI Connector: connector key"
    _order = "id desc"

    name = fields.Char(required=True, help="What this key is for, e.g. 'Claude on Sara's laptop'.")
    user_id = fields.Many2one("res.users", required=True, ondelete="cascade", default=lambda s: s.env.user,
                              help="The assistant acts as this user and gets exactly these access rights.")
    secret_hash = fields.Char(required=True, groups="base.group_system", copy=False)
    hint = fields.Char("Ends with", readonly=True)
    scope = fields.Selection([("read", "Read only"), ("write", "Read and write")], required=True,
                             default="write")
    connection_ids = fields.Many2many("nxb.mcp.connection", string="Limited to databases",
                                      help="Empty = every database the user is allowed to use.")
    active = fields.Boolean(default=True)
    expires_at = fields.Datetime("Expires")
    last_used = fields.Datetime(readonly=True)
    call_count = fields.Integer("AI calls", compute="_compute_call_count")

    @api.model
    def _generate(self, vals):
        secret = secrets.token_urlsafe(32)
        vals = dict(vals, secret_hash=_hash(secret), hint=secret[-4:])
        key = self.sudo().create(vals)
        return key, "%s_%d_%s" % (KEY_PREFIX, key.id, secret)

    @api.model
    def _authenticate(self, token):
        """Return the key record for a presented token, or an empty recordset."""
        if not token or not token.startswith(KEY_PREFIX + "_"):
            return self.browse()
        try:
            _p, kid, secret = token.split("_", 2)
            kid = int(kid)
        except ValueError:
            return self.browse()
        key = self.sudo().browse(kid).exists()
        if not key or not key.active or not hmac.compare_digest(key.secret_hash, _hash(secret)):
            return self.browse()
        if key.expires_at and key.expires_at < fields.Datetime.now():
            return self.browse()
        if not key.user_id.active:
            return self.browse()
        return key

    def _compute_call_count(self):
        counts = dict(self.env["nxb.mcp.log"].sudo()._read_group([("key_id", "in", self.ids)], ["key_id"],
                                                                 ["__count"]))
        for rec in self:
            rec.call_count = counts.get(rec, 0)

    def _touch(self):
        touch_row(self, "last_used")

    def action_revoke(self):
        self.write({"active": False})

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("secret_hash"):
                raise UserError(_("Use the 'New connector key' button to create keys."))
        return super().create(vals_list)

    def write(self, vals):
        if "user_id" in vals and not self.env.user.has_group("base.group_system"):
            raise AccessError(_("Only administrators can change the user of a key."))
        return super().write(vals)


class McpOauthClient(models.Model):
    _name = "nxb.mcp.oauth.client"
    _description = "AI Connector: OAuth client"
    _order = "id desc"

    name = fields.Char(required=True)
    client_id = fields.Char(required=True, index=True, readonly=True, copy=False)
    redirect_uris = fields.Text(required=True, help="One per line.")
    active = fields.Boolean(default=True)
    token_count = fields.Integer(compute="_compute_token_count")

    def _compute_token_count(self):
        for rec in self:
            rec.token_count = self.env["nxb.mcp.oauth.token"].sudo().search_count(
                [("client_id", "=", rec.id), ("revoked", "=", False)])

    def _redirect_allowed(self, uri):
        self.ensure_one()
        return uri in [u.strip() for u in (self.redirect_uris or "").splitlines() if u.strip()]

    def action_revoke_tokens(self):
        self.env["nxb.mcp.oauth.token"].sudo().search([("client_id", "in", self.ids)]).write({"revoked": True})


class McpOauthCode(models.Model):
    _name = "nxb.mcp.oauth.code"
    _description = "AI Connector: OAuth authorisation code"

    code_hash = fields.Char(required=True, index=True)
    client_id = fields.Many2one("nxb.mcp.oauth.client", required=True, ondelete="cascade")
    user_id = fields.Many2one("res.users", required=True, ondelete="cascade")
    redirect_uri = fields.Char(required=True)
    code_challenge = fields.Char(required=True)
    scope = fields.Selection([("read", "Read only"), ("write", "Read and write")], default="write")
    expires_at = fields.Datetime(required=True)
    used = fields.Boolean()

    @api.model
    def _issue(self, client, user, redirect_uri, challenge, scope):
        code = secrets.token_urlsafe(32)
        self.sudo().create({"code_hash": _hash(code), "client_id": client.id, "user_id": user.id,
                            "redirect_uri": redirect_uri, "code_challenge": challenge, "scope": scope,
                            "expires_at": fields.Datetime.now() + timedelta(minutes=5)})
        return code

    @api.model
    def _redeem(self, code, client, redirect_uri, verifier):
        rec = self.sudo().search([("code_hash", "=", _hash(code)), ("client_id", "=", client.id)], limit=1)
        if not rec or rec.used or rec.expires_at < fields.Datetime.now() or rec.redirect_uri != redirect_uri:
            return self.browse()
        digest = base64.urlsafe_b64encode(hashlib.sha256((verifier or "").encode()).digest()).rstrip(b"=")
        if not hmac.compare_digest(digest.decode(), rec.code_challenge):
            return self.browse()
        rec.used = True
        return rec


class McpOauthToken(models.Model):
    _name = "nxb.mcp.oauth.token"
    _description = "AI Connector: OAuth access"
    _order = "id desc"

    client_id = fields.Many2one("nxb.mcp.oauth.client", required=True, ondelete="cascade")
    user_id = fields.Many2one("res.users", required=True, ondelete="cascade")
    access_hash = fields.Char(required=True, index=True, groups="base.group_system")
    refresh_hash = fields.Char(index=True, groups="base.group_system")
    scope = fields.Selection([("read", "Read only"), ("write", "Read and write")], default="write")
    expires_at = fields.Datetime(required=True)
    refresh_expires_at = fields.Datetime()
    revoked = fields.Boolean()
    last_used = fields.Datetime(readonly=True)

    ACCESS_LIFETIME = timedelta(hours=1)
    REFRESH_LIFETIME = timedelta(days=60)

    @api.model
    def _issue(self, client, user, scope):
        access, refresh = "nxbo_" + secrets.token_urlsafe(32), "nxbr_" + secrets.token_urlsafe(32)
        now = fields.Datetime.now()
        self.sudo().create({"client_id": client.id, "user_id": user.id, "scope": scope,
                            "access_hash": _hash(access), "refresh_hash": _hash(refresh),
                            "expires_at": now + self.ACCESS_LIFETIME,
                            "refresh_expires_at": now + self.REFRESH_LIFETIME})
        return {"access_token": access, "token_type": "Bearer",
                "expires_in": int(self.ACCESS_LIFETIME.total_seconds()),
                "refresh_token": refresh, "scope": "mcp:%s" % scope}

    @api.model
    def _authenticate(self, token):
        if not token or not token.startswith("nxbo_"):
            return self.browse()
        rec = self.sudo().search([("access_hash", "=", _hash(token)), ("revoked", "=", False)], limit=1)
        if not rec or rec.expires_at < fields.Datetime.now() or not rec.client_id.active \
                or not rec.user_id.active:
            return self.browse()
        return rec

    @api.model
    def _refresh(self, refresh_token, client):
        rec = self.sudo().search([("refresh_hash", "=", _hash(refresh_token)), ("client_id", "=", client.id),
                                  ("revoked", "=", False)], limit=1)
        if not rec or (rec.refresh_expires_at and rec.refresh_expires_at < fields.Datetime.now()):
            return None
        rec.revoked = True  # rotate
        return self._issue(client, rec.user_id, rec.scope)

    def action_revoke(self):
        self.sudo().write({"revoked": True})


class McpSession(models.Model):
    _name = "nxb.mcp.session"
    _description = "AI Connector: client session"
    _order = "last_seen desc"

    ref = fields.Char(required=True, index=True)
    user_id = fields.Many2one("res.users", ondelete="cascade")
    key_id = fields.Many2one("nxb.mcp.key", ondelete="set null")
    client_name = fields.Char()
    client_version = fields.Char()
    protocol_version = fields.Char()
    last_seen = fields.Datetime(default=fields.Datetime.now)
    closed = fields.Boolean()


def touch_row(records, field):
    """Set a 'last used' timestamp at most once a minute, with a plain UPDATE.

    MCP clients send requests in parallel; writing the same row on every request would make
    concurrent transactions fail with serialization errors."""
    if not records:
        return
    records.env.cr.execute(
        'UPDATE "%s" SET "%s" = (now() at time zone \'UTC\') WHERE id IN %%s AND ("%s" IS NULL OR "%s" < '
        '(now() at time zone \'UTC\') - interval \'1 minute\')' % (records._table, field, field, field),
        [tuple(records.ids)])
    records.invalidate_recordset([field])


class McpLog(models.Model):
    _name = "nxb.mcp.log"
    _description = "AI Connector: tool call"
    _order = "id desc"
    _rec_name = "tool"

    tool = fields.Char(required=True, index=True)
    user_id = fields.Many2one("res.users", ondelete="set null", index=True)
    key_id = fields.Many2one("nxb.mcp.key", ondelete="set null", index=True)
    oauth_token_id = fields.Many2one("nxb.mcp.oauth.token", ondelete="set null", index=True)
    connection_id = fields.Many2one("nxb.mcp.connection", ondelete="set null", index=True)
    database_code = fields.Char("Database")
    status = fields.Selection([("ok", "OK"), ("error", "Error"), ("refused", "Refused")], index=True)
    error = fields.Text()
    arguments = fields.Text()
    duration_ms = fields.Integer("Duration (ms)")
    session_ref = fields.Char("Session")

    @api.autovacuum
    def _gc_old_logs(self):
        days = int(self.env["ir.config_parameter"].sudo().get_param("nxb_mcp_hub.log_days") or 90)
        self.sudo().search([("create_date", "<", fields.Datetime.now() - timedelta(days=days))]).unlink()
        self.env["nxb.mcp.oauth.code"].sudo().search(
            [("expires_at", "<", fields.Datetime.now() - timedelta(days=1))]).unlink()
