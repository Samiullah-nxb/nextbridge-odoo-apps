# -*- coding: utf-8 -*-
"""AI assistant connector: a minimal MCP server (Streamable HTTP, JSON responses) at /li/mcp/<token>.

Add the URL to any AI assistant that supports custom (remote MCP) connectors, with no sign-in.
The token in the URL is random, stored hashed, revocable, and masked in Odoo's request log.
"""
import json
import logging

from odoo import fields, http
from odoo.http import request

from ..models.li_work import INTENT_HELP

_logger = logging.getLogger(__name__)

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_INSTRUCTIONS = (
    "Odoo LinkedIn Sales agents. Odoo finds people, keeps every limit, send window and delay, sends (engine "
    "mode) and hands warm leads to CRM; you write every invite note, message, follow-up and post. Start with "
    "li_overview. For writing: li_get_work → li_submit_work → li_finish_run. Before writing to a person read their "
    "LinkedIn profile, then the conversation, then their last reply, then write. AI browser agent accounts: "
    "li_chrome_start, then loop li_chrome_next; never click a sending button without li_confirm_send ok=true."
)

S_PROFILE = {"type": "integer", "description": "LinkedIn account id from li_overview (optional = all)."}
TOOLS = [
    {"name": "li_overview", "description": "LinkedIn accounts, agents, limits left today, work waiting, "
                                          "queue and stage counts.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "li_get_work", "description": "Work items for the AI to write: replies, first messages, follow-ups, "
                                          "invite notes and post drafts, each with the person's LinkedIn profile, "
                                          "conversation, persona and services. Returns a run_id.",
     "inputSchema": {"type": "object", "properties": {
         "profile_id": S_PROFILE,
         "limit": {"type": "integer", "default": 25},
         "kinds": {"type": "array", "items": {"type": "string", "enum": [
             "reply", "first_message", "followup", "invite_note", "post"]}},
         "mode": {"type": "string", "enum": ["engine", "chrome"], "default": "engine"}}}},
    {"name": "li_submit_work", "description": "Submit written texts. Odoo queues them and sends within the rules; "
                                             "repeats of messages already sent are refused.",
     "inputSchema": {"type": "object", "required": ["run_id", "items"], "properties": {
         "run_id": {"type": "integer"},
         "items": {"type": "array", "items": {"type": "object", "required": ["work_id", "text"], "properties": {
             "work_id": {"type": "string"},
             "text": {"type": "string"},
             "intent": {"type": "string", "enum": list(INTENT_HELP), "description": "; ".join(
                 "%s = %s" % kv for kv in INTENT_HELP.items())},
             "summary": {"type": "string", "description": "1-3 lines: who they are and where they stand."},
             "answers": {"type": "string", "description": "New questionnaire answers learned from this reply."},
             "referral": {"type": "string", "description": "Name/role/contact of the person they referred to."},
             "instruction": {"type": "string", "description": "Extra instruction for the salesperson (handoffs)."},
         }}}}}},
    {"name": "li_finish_run", "description": "Close the run with a short summary (and errors met, if any).",
     "inputSchema": {"type": "object", "required": ["run_id"], "properties": {
         "run_id": {"type": "integer"}, "summary": {"type": "string"}, "errors": {"type": "string"}}}},
    {"name": "li_send_now", "description": "Send queued invites/messages now (engine accounts). If invite notes "
                                          "are missing it returns them to write first.",
     "inputSchema": {"type": "object", "properties": {"profile_id": S_PROFILE}}},
    {"name": "li_sending_status", "description": "What was sent, what failed and what is still queued.",
     "inputSchema": {"type": "object", "properties": {"profile_id": S_PROFILE,
                                                      "hours": {"type": "number", "default": 24}}}},
    {"name": "li_invite_person", "description": "Queue one LinkedIn person for an invite (from a profile link).",
     "inputSchema": {"type": "object", "required": ["linkedin_url"], "properties": {
         "linkedin_url": {"type": "string"}, "profile_id": S_PROFILE,
         "persona_id": {"type": "integer"}, "name": {"type": "string"}}}},
    {"name": "li_answer_replies_now", "description": "Read the LinkedIn inbox now (engine) and return the replies "
                                                    "to answer.",
     "inputSchema": {"type": "object", "properties": {"profile_id": S_PROFILE}}},
    {"name": "li_takeover", "description": "Take a prospect over (agents stop for that person only), or release "
                                          "them back to the agents.",
     "inputSchema": {"type": "object", "required": ["who"], "properties": {
         "who": {"type": "string", "description": "Name or LinkedIn URL"},
         "release": {"type": "boolean", "default": False}}}},
    {"name": "li_chrome_start", "description": "AI browser agent: confirm which LinkedIn account this Chrome is "
                                              "signed in to before doing anything.",
     "inputSchema": {"type": "object", "required": ["profile_id"], "properties": {
         "profile_id": {"type": "integer"}, "seen_url": {"type": "string"}, "seen_name": {"type": "string"}}}},
    {"name": "li_chrome_next", "description": "AI browser agent: the next LinkedIn step to do, with instructions.",
     "inputSchema": {"type": "object", "required": ["profile_id"], "properties": {"profile_id": {"type": "integer"}}}},
    {"name": "li_confirm_send", "description": "AI browser agent: ask permission right before any click that "
                                              "sends, connects, follows, withdraws or posts. For messages pass the "
                                              "conversation you just read.",
     "inputSchema": {"type": "object", "required": ["action_id"], "properties": {
         "action_id": {"type": "string"},
         "conversation": {"type": "array", "items": {"type": "object", "properties": {
             "direction": {"type": "string", "enum": ["in", "out"]}, "text": {"type": "string"},
             "attachment": {"type": "boolean"}}}}}}},
    {"name": "li_chrome_report", "description": "AI browser agent: report the outcome of a step.",
     "inputSchema": {"type": "object", "required": ["action_id", "outcome"], "properties": {
         "action_id": {"type": "string"}, "outcome": {"type": "string"},
         "data": {"type": "object"}, "run_id": {"type": "integer"}}}},
]


class LiMcpController(http.Controller):

    @http.route("/li/mcp/<string:token>", type="http", auth="none", methods=["GET", "POST", "DELETE", "OPTIONS"],
                csrf=False, save_session=False)
    def mcp(self, token, **kw):
        method = request.httprequest.method
        if method == "OPTIONS":
            return request.make_response("", headers=[("Allow", "POST, OPTIONS")], status=204)
        env = request.env(su=True)
        tok = env["li.connector.token"]._from_token(token)
        if not tok:
            return self._json({"jsonrpc": "2.0", "id": None, "error": {"code": -32001, "message": "Invalid token"}},
                              status=401)
        if method in ("GET", "DELETE"):
            return request.make_response("", status=405 if method == "GET" else 200)
        try:
            payload = json.loads(request.httprequest.get_data() or b"{}")
        except ValueError:
            return self._json({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
        user = tok.user_id or env.ref("base.user_root")
        uenv = request.env(user=user.id, su=False)
        tok = tok.with_env(uenv).sudo()
        if isinstance(payload, list):
            out = [r for r in (self._dispatch(uenv, tok, p) for p in payload) if r is not None]
            return self._json(out) if out else request.make_response("", status=202)
        res = self._dispatch(uenv, tok, payload)
        if res is None:
            return request.make_response("", status=202)
        return self._json(res)

    def _json(self, data, status=200):
        return request.make_response(json.dumps(data, default=str), status=status,
                                     headers=[("Content-Type", "application/json")])

    def _dispatch(self, env, tok, msg):
        mid = msg.get("id")
        method = msg.get("method")
        if mid is None:  # notification
            return None
        try:
            if method == "initialize":
                asked = (msg.get("params") or {}).get("protocolVersion")
                return self._ok(mid, {
                    "protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "odoo-linkedin-sales", "version": "18.0.1.0.0"},
                    "instructions": SERVER_INSTRUCTIONS,
                })
            if method == "ping":
                return self._ok(mid, {})
            if method == "tools/list":
                return self._ok(mid, {"tools": TOOLS})
            if method == "tools/call":
                params = msg.get("params") or {}
                tok.write({"last_used": fields.Datetime.now(), "call_count": tok.call_count + 1})
                result = self._call_tool(env, tok, params.get("name"), params.get("arguments") or {})
                env.cr.commit()
                return self._ok(mid, {"content": [{"type": "text", "text": json.dumps(result, default=str,
                                                                                       ensure_ascii=False)}],
                                      "isError": bool(isinstance(result, dict) and result.get("ok") is False
                                                      and result.get("error"))})
            if method in ("resources/list", "prompts/list"):
                return self._ok(mid, {"resources" if "resources" in method else "prompts": []})
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "Method not found"}}
        except Exception as e:  # noqa: BLE001
            _logger.exception("LinkedIn MCP call %s failed", method)
            env.cr.rollback()
            return self._ok(mid, {"content": [{"type": "text", "text": "Error: %s" % e}], "isError": True})

    def _ok(self, mid, result):
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def _call_tool(self, env, tok, name, a):
        work = env["li.work"].sudo()
        chrome = env["li.chrome"].sudo()
        if name == "li_overview":
            return work.overview(tok)
        if name == "li_get_work":
            return work.get_work(tok, a.get("profile_id"), int(a.get("limit") or 25), a.get("kinds"),
                                 a.get("mode") or "engine")
        if name == "li_submit_work":
            return work.submit_work(tok, a.get("run_id"), a.get("items") or [])
        if name == "li_finish_run":
            return work.finish_run(tok, a.get("run_id"), a.get("summary"), a.get("errors"))
        if name == "li_send_now":
            return work.send_now(tok, a.get("profile_id"))
        if name == "li_sending_status":
            return work.sending_status(tok, a.get("profile_id"), a.get("hours") or 24)
        if name == "li_invite_person":
            return work.invite_person(tok, a.get("linkedin_url"), a.get("profile_id"), a.get("persona_id"),
                                      a.get("name"))
        if name == "li_answer_replies_now":
            return work.answer_replies_now(tok, a.get("profile_id"))
        if name == "li_takeover":
            return work.takeover(tok, a.get("who"), bool(a.get("release")))
        if name == "li_chrome_start":
            return chrome.start(tok, a.get("profile_id"), a.get("seen_url"), a.get("seen_name"))
        if name == "li_chrome_next":
            return chrome.next_action(tok, a.get("profile_id"))
        if name == "li_confirm_send":
            return chrome.confirm_send(tok, a.get("action_id"), a.get("conversation"))
        if name == "li_chrome_report":
            return chrome.report(tok, a.get("action_id"), a.get("outcome"), a.get("data"), a.get("run_id"))
        return {"ok": False, "error": "Unknown tool %s" % name}
