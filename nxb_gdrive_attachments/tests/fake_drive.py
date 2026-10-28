# -*- coding: utf-8 -*-
# Copyright 2026 Nextbridge. License OPL-1.
"""A small in-memory imitation of the Google Drive v3 and OAuth 2.0 HTTP APIs, used by the tests.

It implements only what the module uses: token endpoint (authorization_code, refresh_token and
JWT bearer grants), files (create folder / metadata, multipart and resumable uploads, update,
download, get, list, delete, trash), permissions and drives. It also records every request so
tests can assert on what was sent."""
import base64
import hashlib
import email
import json
import re
import secrets
import threading
from email import policy

from werkzeug.serving import make_server
from werkzeug.wrappers import Request, Response


class FakeDrive:

    def __init__(self):
        self.files = {}  # id -> dict(name, mimeType, parents, content, trashed, permissions, driveId)
        self.drives = {"shared1": {"id": "shared1", "name": "Company Shared Drive"}}
        self.codes = {}  # auth code -> refresh token
        self.refresh_tokens = {"rt-valid"}
        self.access_tokens = set()
        self.sessions = {}  # resumable upload sessions
        self.requests = []
        self.fail_next = []  # list of (method, path-regex, status)
        self.lock = threading.Lock()
        self.server = None

    # ------------------------------------------------------------------ server
    def start(self):
        self.server = make_server("127.0.0.1", 0, self.wsgi, threaded=True)
        self.port = self.server.server_port
        self.url = "http://127.0.0.1:%d" % self.port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()

    def params(self):
        """ir.config_parameter values pointing the module to this fake."""
        return {"nxb_gdrive.api_url": self.url + "/drive/v3",
                "nxb_gdrive.upload_url": self.url + "/upload/drive/v3",
                "nxb_gdrive.token_url": self.url + "/token",
                "nxb_gdrive.auth_url": self.url + "/o/oauth2/v2/auth"}

    # ------------------------------------------------------------------ helpers
    def new_id(self):
        return "f" + secrets.token_hex(8)

    def issue_code(self):
        code = "code-" + secrets.token_hex(4)
        self.codes[code] = "rt-" + secrets.token_hex(6)
        return code

    def children(self, folder_id):
        return [f for f in self.files.values() if folder_id in f["parents"] and not f["trashed"]]

    def by_name(self, name):
        return [f for f in self.files.values() if f["name"] == name and not f["trashed"]]

    def path_of(self, file_id):
        parts = []
        f = self.files.get(file_id)
        while f:
            parts.append(f["name"])
            parent = f["parents"][0] if f["parents"] else None
            f = self.files.get(parent)
        return "/".join(reversed(parts))

    def _meta(self, f, fields=None):
        out = {"id": f["id"], "name": f["name"], "mimeType": f["mimeType"], "parents": f["parents"],
               "size": str(len(f["content"])), "trashed": f["trashed"],
               "md5Checksum": hashlib.md5(f["content"]).hexdigest(),
               "webViewLink": "https://drive.google.com/file/d/%s/view" % f["id"],
               "webContentLink": "https://drive.google.com/uc?id=%s&export=download" % f["id"]}
        if f.get("driveId"):
            out["driveId"] = f["driveId"]
        if f["mimeType"] == "application/vnd.google-apps.folder":
            out["webViewLink"] = "https://drive.google.com/drive/folders/%s" % f["id"]
        if fields:
            keep = set(re.findall(r"\w+", fields))
            out = {k: v for k, v in out.items() if k in keep}
        return out

    def _json(self, data, status=200, headers=None):
        return Response(json.dumps(data), status=status, mimetype="application/json", headers=headers)

    def _error(self, status, message, reason="error"):
        return self._json({"error": {"code": status, "message": message,
                                     "errors": [{"reason": reason, "message": message}]}}, status)

    def _auth(self, request):
        auth = request.headers.get("Authorization", "")
        return auth.startswith("Bearer ") and auth[7:] in self.access_tokens

    def _drive_of(self, parents):
        for p in parents or []:
            if p in self.drives:
                return p
            f = self.files.get(p)
            if f and f.get("driveId"):
                return f["driveId"]
        return None

    def _create(self, meta, content=b""):
        parents = meta.get("parents") or ["root"]
        for p in parents:
            if p != "root" and p not in self.files and p not in self.drives:
                return None
        fid = self.new_id()
        f = {"id": fid, "name": meta.get("name") or "Untitled", "parents": parents,
             "mimeType": meta.get("mimeType") or "application/octet-stream", "content": content,
             "trashed": False, "permissions": [], "driveId": self._drive_of(parents),
             "description": meta.get("description") or "", "appProperties": meta.get("appProperties") or {}}
        self.files[fid] = f
        return f

    # ------------------------------------------------------------------ WSGI
    def wsgi(self, environ, start_response):
        request = Request(environ)
        with self.lock:
            body = request.get_data()
            self.requests.append((request.method, request.path, dict(request.args), body[:2000]))
            for i, (m, rx, status) in enumerate(self.fail_next):
                if m == request.method and re.search(rx, request.path):
                    self.fail_next.pop(i)
                    resp = self._error(status, "Injected failure %s" % status,
                                       "rateLimitExceeded" if status == 429 else "backendError")
                    return resp(environ, start_response)
            resp = self.route(request, body)
        return resp(environ, start_response)

    def route(self, request, body):
        path, method, args = request.path, request.method, request.args
        if path == "/token":
            return self.token(request)
        if path == "/o/oauth2/v2/auth":
            # behaves like a user who clicks "Allow" on Google's consent page
            from urllib.parse import urlencode
            if not args.get("client_id") or not args.get("redirect_uri"):
                return Response("Error 400: invalid_request", status=400)
            code = self.issue_code()
            target = args["redirect_uri"] + "?" + urlencode({"code": code, "state": args.get("state", "")})
            return Response(status=302, headers={"Location": target})
        if not self._auth(request):
            return self._error(401, "Invalid Credentials", "authError")
        m = re.match(r"^/upload/drive/v3/files(?:/([\w-]+))?$", path)
        if m:
            return self.upload(request, body, m.group(1))
        m = re.match(r"^/upload/session/([\w-]+)$", path)
        if m:
            return self.resumable_chunk(request, body, m.group(1))
        if path == "/drive/v3/about":
            return self._json({"user": {"displayName": "Test User", "emailAddress": "drive.user@example.com"},
                               "storageQuota": {"limit": "16106127360", "usage": "1024"}})
        if path == "/drive/v3/drives" and method == "GET":
            return self._json({"drives": list(self.drives.values())})
        m = re.match(r"^/drive/v3/drives/([\w-]+)$", path)
        if m:
            d = self.drives.get(m.group(1))
            return self._json(d) if d else self._error(404, "Shared drive not found", "notFound")
        if path == "/drive/v3/files" and method == "POST":
            meta = json.loads(body or b"{}")
            f = self._create(meta)
            if not f:
                return self._error(404, "File not found: parent", "notFound")
            return self._json(self._meta(f, args.get("fields")))
        if path == "/drive/v3/files" and method == "GET":
            q = args.get("q", "")
            items = [f for f in self.files.values() if not f["trashed"]]
            mm = re.search(r"'([\w-]+)' in parents", q)
            if mm:
                items = [f for f in items if mm.group(1) in f["parents"]]
            mm = re.search(r"name = '((?:[^'\\]|\\.)*)'", q)
            if mm:
                items = [f for f in items if f["name"] == mm.group(1).replace("\\'", "'")]
            if "mimeType = 'application/vnd.google-apps.folder'" in q:
                items = [f for f in items if f["mimeType"] == "application/vnd.google-apps.folder"]
            return self._json({"files": [self._meta(f, None) for f in items]})
        m = re.match(r"^/drive/v3/files/([\w-]+)/permissions$", path)
        if m:
            f = self.files.get(m.group(1))
            if not f:
                return self._error(404, "File not found", "notFound")
            perm = json.loads(body or b"{}")
            perm["id"] = "p" + secrets.token_hex(3)
            f["permissions"].append(perm)
            return self._json(perm)
        m = re.match(r"^/drive/v3/files/([\w-]+)$", path)
        if m:
            f = self.files.get(m.group(1))
            if not f or (f["trashed"] and method != "DELETE"):
                return self._error(404, "File not found: %s." % m.group(1), "notFound")
            if method == "GET":
                if args.get("alt") == "media":
                    return Response(f["content"], status=200, mimetype=f["mimeType"])
                return self._json(self._meta(f, args.get("fields")))
            if method == "DELETE":
                del self.files[f["id"]]
                return Response(status=204)
            if method == "PATCH":
                meta = json.loads(body or b"{}")
                for key in ("name", "description", "trashed", "appProperties"):
                    if key in meta:
                        f[key] = meta[key]
                if args.get("addParents"):
                    f["parents"] = [p for p in f["parents"] if p not in args.get("removeParents", "").split(",")]
                    f["parents"].append(args["addParents"])
                return self._json(self._meta(f, args.get("fields")))
        return self._error(404, "Unknown endpoint %s %s" % (method, path), "notFound")

    def token(self, request):
        form = request.form
        grant = form.get("grant_type")
        if grant == "authorization_code":
            rt = self.codes.pop(form.get("code"), None)
            if not rt or not form.get("client_id") or not form.get("client_secret") or not form.get("redirect_uri"):
                return self._json({"error": "invalid_grant"}, 400)
            self.refresh_tokens.add(rt)
            at = "at-" + secrets.token_hex(8)
            self.access_tokens.add(at)
            return self._json({"access_token": at, "expires_in": 3599, "refresh_token": rt, "token_type": "Bearer",
                               "scope": "https://www.googleapis.com/auth/drive"})
        if grant == "refresh_token":
            if form.get("refresh_token") not in self.refresh_tokens:
                return self._json({"error": "invalid_grant", "error_description": "Token has been expired or "
                                                                                  "revoked."}, 400)
            at = "at-" + secrets.token_hex(8)
            self.access_tokens.add(at)
            return self._json({"access_token": at, "expires_in": 3599, "token_type": "Bearer"})
        if grant == "urn:ietf:params:oauth:grant-type:jwt-bearer":
            parts = (form.get("assertion") or "").split(".")
            if len(parts) != 3:
                return self._json({"error": "invalid_grant"}, 400)
            claims = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
            if not claims.get("iss", "").endswith("gserviceaccount.com") or "drive" not in claims.get("scope", ""):
                return self._json({"error": "invalid_grant"}, 400)
            self.last_jwt_claims = claims
            at = "at-sa-" + secrets.token_hex(8)
            self.access_tokens.add(at)
            return self._json({"access_token": at, "expires_in": 3599, "token_type": "Bearer"})
        return self._json({"error": "unsupported_grant_type"}, 400)

    def upload(self, request, body, file_id):
        utype = request.args.get("uploadType")
        if utype == "multipart":
            msg = email.message_from_bytes(
                b"Content-Type: " + request.headers["Content-Type"].encode() + b"\r\n\r\n" + body,
                policy=policy.HTTP)
            parts = list(msg.iter_parts())
            meta = json.loads(parts[0].get_content() if isinstance(parts[0].get_content(), str)
                              else parts[0].get_content().decode())
            content = parts[1].get_payload(decode=True)
            if file_id:
                f = self.files.get(file_id)
                if not f or f["trashed"]:
                    return self._error(404, "File not found", "notFound")
                f["content"] = content
                f.update({k: v for k, v in meta.items() if k in ("name", "mimeType", "description")})
            else:
                f = self._create(meta, content)
                if not f:
                    return self._error(404, "File not found: parent", "notFound")
            return self._json(self._meta(f, request.args.get("fields")))
        if utype == "resumable":
            meta = json.loads(body or b"{}")
            if file_id and (file_id not in self.files or self.files[file_id]["trashed"]):
                return self._error(404, "File not found", "notFound")
            sid = secrets.token_hex(6)
            self.sessions[sid] = {"meta": meta, "file_id": file_id, "data": b"",
                                  "total": int(request.headers.get("X-Upload-Content-Length") or 0),
                                  "fields": request.args.get("fields")}
            return Response(status=200, headers={"Location": "%s/upload/session/%s" % (self.url, sid)})
        return self._error(400, "Unsupported uploadType", "badRequest")

    def resumable_chunk(self, request, body, sid):
        s = self.sessions.get(sid)
        if not s:
            return self._error(404, "Upload session not found", "notFound")
        rng = request.headers.get("Content-Range", "")
        m = re.match(r"bytes (\d+)-(\d+)/(\d+|\*)", rng)
        if not m or int(m.group(1)) != len(s["data"]):
            return self._error(400, "Bad Content-Range %r" % rng, "badRequest")
        s["data"] += body
        total = m.group(3)
        if total == "*" or len(s["data"]) < int(total):
            return Response(status=308, headers={"Range": "bytes=0-%d" % (len(s["data"]) - 1)})
        if s["file_id"]:
            f = self.files[s["file_id"]]
            f["content"] = s["data"]
            f.update({k: v for k, v in s["meta"].items() if k in ("name", "mimeType")})
        else:
            f = self._create(s["meta"], s["data"])
        del self.sessions[sid]
        return self._json(self._meta(f, s["fields"]))
