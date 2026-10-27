# -*- coding: utf-8 -*-
# Copyright 2026 Nextbridge. License OPL-1.
import base64
import hashlib
import json
import re
import secrets
from urllib.parse import parse_qs, urlparse

from odoo.tests import HttpCase, tagged

from ..core.registry import PACKS, PARAM_PREFIX


@tagged("post_install", "-at_install", "nxb_mcp")
class TestHttpEndpoint(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["nxb.mcp.connection"]._ensure_this()
        icp = cls.env["ir.config_parameter"].sudo()
        for key in PACKS:
            icp.set_param(PARAM_PREFIX + "pack." + key, "True")
        icp.set_param(PARAM_PREFIX + "rate_limit", "0")
        icp.set_param(PARAM_PREFIX + "oauth_enabled", "True")
        icp.set_param(PARAM_PREFIX + "oauth_dcr", "True")
        icp.set_param(PARAM_PREFIX + "strict_readonly", "False")
        # isolate from whatever the database contains: only "this database", no private fetches
        cls.env["nxb.mcp.connection"].search([("kind", "!=", "this")]).write({"active": False})
        icp.set_param(PARAM_PREFIX + "allow_private_fetch", "False")
        icp.set_param(PARAM_PREFIX + "max_records", "500")
        cls.admin = cls.env.ref("base.user_admin")
        cls.key, cls.token = cls.env["nxb.mcp.key"]._generate(
            {"name": "http test", "user_id": cls.admin.id, "scope": "write"})
        cls.rkey, cls.rtoken = cls.env["nxb.mcp.key"]._generate(
            {"name": "http read", "user_id": cls.admin.id, "scope": "read"})

    def rpc(self, payload, token=None, path="/mcp", headers=None, raw=None):
        hdrs = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if token:
            hdrs["Authorization"] = "Bearer " + token
        hdrs.update(headers or {})
        return self.url_open(path, data=raw if raw is not None else json.dumps(payload), headers=hdrs)

    # ------------------------------------------------------------------ protocol over HTTP
    def test_auth_required(self):
        r = self.rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertEqual(r.status_code, 401)
        self.assertIn("resource_metadata", r.headers.get("WWW-Authenticate", ""))
        r = self.rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, token="nxbm_1_wrong")
        self.assertEqual(r.status_code, 401)

    def test_initialize_session_and_call(self):
        r = self.rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                      "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "pytest", "version": "1"}}},
                     token=self.token)
        self.assertEqual(r.status_code, 200)
        sid = r.headers.get("Mcp-Session-Id")
        self.assertTrue(sid)
        self.assertEqual(r.json()["result"]["serverInfo"]["name"], "nextbridge-odoo-mcp-hub")
        sess = self.env["nxb.mcp.session"].search([("ref", "=", sid)])
        self.assertEqual(sess.client_name, "pytest")
        r = self.rpc({"jsonrpc": "2.0", "method": "notifications/initialized"}, token=self.token,
                     headers={"Mcp-Session-Id": sid})
        self.assertEqual(r.status_code, 202)
        r = self.rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "data_count", "arguments": {"model": "res.users"}}}, token=self.token,
            headers={"Mcp-Session-Id": sid})
        res = r.json()["result"]
        self.assertFalse(res["isError"])
        self.assertGreater(json.loads(res["content"][0]["text"])["count"], 0)
        r = self.opener.request("DELETE", self.base_url() + "/mcp", timeout=30,
                                headers={"Authorization": "Bearer " + self.token, "Mcp-Session-Id": sid})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(sess.closed)

    def test_batch_get_options_parse(self):
        r = self.rpc([{"jsonrpc": "2.0", "id": 1, "method": "ping"},
                      {"jsonrpc": "2.0", "method": "notifications/initialized"},
                      {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}], token=self.token)
        body = r.json()
        self.assertIsInstance(body, list)
        self.assertEqual([m["id"] for m in body], [1, 2])
        r = self.url_open("/mcp", headers={"Authorization": "Bearer " + self.token})
        self.assertEqual(r.status_code, 405)
        r = self.opener.request("OPTIONS", self.base_url() + "/mcp", timeout=30)
        self.assertEqual(r.status_code, 204)
        self.assertEqual(r.headers.get("Access-Control-Allow-Origin"), "*")
        r = self.rpc(None, token=self.token, raw="{not json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"]["code"], -32700)
        h = self.url_open("/mcp/health").json()
        self.assertEqual(h["status"], "ok")

    def test_key_in_url_and_db_path(self):
        r = self.rpc({"jsonrpc": "2.0", "id": 1, "method": "ping"}, path="/mcp/key/" + self.token)
        self.assertEqual(r.status_code, 200)
        db = self.env.cr.dbname
        r = self.rpc({"jsonrpc": "2.0", "id": 1, "method": "ping"}, path="/mcp/db/%s/key/%s" % (db, self.token))
        self.assertEqual(r.status_code, 200)
        r = self.rpc({"jsonrpc": "2.0", "id": 1, "method": "ping"}, path="/mcp/db/no_such_db_x", token=self.token)
        self.assertEqual(r.status_code, 404)

    def test_read_key_over_http(self):
        r = self.rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, token=self.rtoken)
        names = {t["name"] for t in r.json()["result"]["tools"]}
        self.assertIn("data_search", names)
        self.assertNotIn("data_create", names)

    # ------------------------------------------------------------------ OAuth 2.1
    def test_oauth_full_flow(self):
        meta = self.url_open("/.well-known/oauth-protected-resource/mcp").json()
        self.assertTrue(meta["resource"].endswith("/mcp"))
        asm = self.url_open("/.well-known/oauth-authorization-server").json()
        self.assertEqual(asm["code_challenge_methods_supported"], ["S256"])

        r = self.url_open("/mcp/oauth/register", data=json.dumps({"client_name": "Test AI",
                                                                  "redirect_uris": ["javascript:x"]}),
                          headers={"Content-Type": "application/json"})
        self.assertEqual(r.status_code, 400)
        redirect = "http://localhost:6274/callback"
        r = self.url_open("/mcp/oauth/register", data=json.dumps({"client_name": "Test AI",
                                                                  "redirect_uris": [redirect]}),
                          headers={"Content-Type": "application/json"})
        self.assertEqual(r.status_code, 201)
        client_id = r.json()["client_id"]

        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        q = {"response_type": "code", "client_id": client_id, "redirect_uri": redirect, "state": "st4te",
             "code_challenge": challenge, "code_challenge_method": "S256", "scope": "mcp:write"}

        self.authenticate("admin", "admin")
        page = self.url_open("/mcp/oauth/authorize?" + "&".join("%s=%s" % kv for kv in q.items()))
        self.assertEqual(page.status_code, 200)
        self.assertIn("Test AI", page.text)
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
        form = dict(q, csrf_token=csrf, decision="allow", scope_choice="write")
        r = self.url_open("/mcp/oauth/authorize", data=form, allow_redirects=False)
        self.assertIn(r.status_code, (302, 303))
        loc = urlparse(r.headers["Location"])
        params = parse_qs(loc.query)
        self.assertEqual(params["state"], ["st4te"])
        code = params["code"][0]

        bad = self.url_open("/mcp/oauth/token", data={"grant_type": "authorization_code", "code": code,
                                                      "client_id": client_id, "redirect_uri": redirect,
                                                      "code_verifier": "wrong"})
        self.assertEqual(bad.status_code, 400)
        # a failed attempt burns the code? re-run flow only if necessary
        tok = self.url_open("/mcp/oauth/token", data={"grant_type": "authorization_code", "code": code,
                                                      "client_id": client_id, "redirect_uri": redirect,
                                                      "code_verifier": verifier})
        if tok.status_code != 200:
            r = self.url_open("/mcp/oauth/authorize", data=form, allow_redirects=False)
            code = parse_qs(urlparse(r.headers["Location"]).query)["code"][0]
            tok = self.url_open("/mcp/oauth/token", data={"grant_type": "authorization_code", "code": code,
                                                          "client_id": client_id, "redirect_uri": redirect,
                                                          "code_verifier": verifier})
        self.assertEqual(tok.status_code, 200, tok.text)
        tokens = tok.json()
        self.assertTrue(tokens["access_token"].startswith("nxbo_"))
        self.assertTrue(tokens["refresh_token"].startswith("nxbr_"))
        # code cannot be reused
        again = self.url_open("/mcp/oauth/token", data={"grant_type": "authorization_code", "code": code,
                                                        "client_id": client_id, "redirect_uri": redirect,
                                                        "code_verifier": verifier})
        self.assertEqual(again.status_code, 400)

        self.logout()
        r = self.rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "hub_whoami", "arguments": {}}}, token=tokens["access_token"])
        who = json.loads(r.json()["result"]["content"][0]["text"])
        self.assertEqual(who["login"], "admin")

        ref = self.url_open("/mcp/oauth/token", data={"grant_type": "refresh_token", "client_id": client_id,
                                                      "refresh_token": tokens["refresh_token"]})
        self.assertEqual(ref.status_code, 200)
        new = ref.json()
        self.assertNotEqual(new["access_token"], tokens["access_token"])
        # old refresh token is rotated out
        old = self.url_open("/mcp/oauth/token", data={"grant_type": "refresh_token", "client_id": client_id,
                                                      "refresh_token": tokens["refresh_token"]})
        self.assertEqual(old.status_code, 400)
        r = self.rpc({"jsonrpc": "2.0", "id": 1, "method": "ping"}, token=tokens["access_token"])
        self.assertEqual(r.status_code, 401)

        self.url_open("/mcp/oauth/revoke", data={"token": new["access_token"]})
        r = self.rpc({"jsonrpc": "2.0", "id": 1, "method": "ping"}, token=new["access_token"])
        self.assertEqual(r.status_code, 401)

    def test_oauth_deny_and_pkce_required(self):
        client = self.env["nxb.mcp.oauth.client"].create({"name": "Manual", "client_id": "nxbc_manual",
                                                          "redirect_uris": "https://app.example/cb"})
        self.authenticate("admin", "admin")
        page = self.url_open("/mcp/oauth/authorize?response_type=code&client_id=nxbc_manual"
                             "&redirect_uri=https://app.example/cb")
        self.assertIn("PKCE", page.text)
        page = self.url_open("/mcp/oauth/authorize?response_type=code&client_id=nxbc_manual"
                             "&redirect_uri=https://evil.example/cb&code_challenge=abc")
        self.assertIn("Unknown application", page.text)
        page = self.url_open("/mcp/oauth/authorize?response_type=code&client_id=nxbc_manual"
                             "&redirect_uri=https://app.example/cb&code_challenge=abc&state=s")
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
        r = self.url_open("/mcp/oauth/authorize", data={
            "response_type": "code", "client_id": "nxbc_manual", "redirect_uri": "https://app.example/cb",
            "code_challenge": "abc", "state": "s", "csrf_token": csrf, "decision": "deny"}, allow_redirects=False)
        self.assertIn("error=access_denied", r.headers["Location"])
        self.assertTrue(client)
