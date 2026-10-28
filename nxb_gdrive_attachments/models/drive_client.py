# -*- coding: utf-8 -*-
# Copyright 2026 Nextbridge. License OPL-1.
"""Minimal Google Drive v3 REST client used by the module (no Google SDK needed).

Authentication is either an OAuth 2.0 refresh token (a Google account) or a service account key
(Google Workspace, usually with a Shared Drive). Access tokens are kept in memory only, per database
and company, so parallel requests never write the same database row."""
import base64
import json
import logging
import threading
import time
import uuid
from urllib.parse import urlencode

import requests

_logger = logging.getLogger(__name__)

API_URL = "https://www.googleapis.com/drive/v3"
UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3"
TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
SCOPES = {"full": "https://www.googleapis.com/auth/drive",
          "app": "https://www.googleapis.com/auth/drive.file"}
FOLDER_MIME = "application/vnd.google-apps.folder"
SIMPLE_LIMIT = 5 * 1024 * 1024        # above this, resumable upload
CHUNK = 8 * 1024 * 1024               # resumable chunk size (multiple of 256 KiB)
TIMEOUT = 120

_TOKENS = {}
_TOKENS_LOCK = threading.Lock()


class DriveError(Exception):
    """A Drive call failed. ``status`` is the HTTP status (0 = network), ``retry`` tells whether it is
    worth trying again later."""

    def __init__(self, message, status=0, retry=True):
        super().__init__(message)
        self.status = status
        self.retry = retry


def forget_token(key):
    with _TOKENS_LOCK:
        _TOKENS.pop(key, None)


def _b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class DriveClient:
    """Talk to Google Drive for one company.

    ``cfg`` keys: auth (oauth / service), client_id, client_secret, refresh_token, service_key (JSON text),
    service_subject, scope (full / app), and the endpoint URLs."""

    def __init__(self, cfg, cache_key):
        self.cfg = cfg
        self.cache_key = cache_key
        self.api = cfg.get("api_url") or API_URL
        self.upload_api = cfg.get("upload_url") or UPLOAD_URL
        self.token_url = cfg.get("token_url") or TOKEN_URL
        self.session = requests.Session()

    # ------------------------------------------------------------------ authentication
    def _new_token(self):
        cfg = self.cfg
        if cfg.get("auth") == "service":
            data = self._service_assertion()
        else:
            if not (cfg.get("client_id") and cfg.get("client_secret") and cfg.get("refresh_token")):
                raise DriveError("Google Drive is not connected yet.", retry=False)
            data = {"grant_type": "refresh_token", "client_id": cfg["client_id"],
                    "client_secret": cfg["client_secret"], "refresh_token": cfg["refresh_token"]}
        try:
            r = self.session.post(self.token_url, data=data, timeout=TIMEOUT)
        except requests.RequestException as e:
            raise DriveError("Google could not be reached: %s" % e)
        if r.status_code != 200:
            try:
                info = r.json()
            except ValueError:
                info = {}
            msg = info.get("error_description") or info.get("error") or r.text[:200]
            raise DriveError("Google refused the credentials: %s" % msg, r.status_code,
                             retry=r.status_code >= 500)
        info = r.json()
        return info["access_token"], time.time() + int(info.get("expires_in", 3600)) - 120

    def _service_assertion(self):
        try:
            key = json.loads(self.cfg.get("service_key") or "")
            email = key["client_email"]
            pem = key["private_key"].encode()
        except (ValueError, KeyError, TypeError):
            raise DriveError("The service account key is not a valid JSON key file.", retry=False)
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        now = int(time.time())
        claims = {"iss": email, "scope": SCOPES["full"], "aud": key.get("token_uri") or self.token_url,
                  "iat": now, "exp": now + 3600}
        if self.cfg.get("service_subject"):
            claims["sub"] = self.cfg["service_subject"]
        header = {"alg": "RS256", "typ": "JWT"}
        signing_input = "%s.%s" % (_b64url(json.dumps(header).encode()), _b64url(json.dumps(claims).encode()))
        try:
            private_key = serialization.load_pem_private_key(pem, password=None)
        except ValueError:
            raise DriveError("The private key in the service account file cannot be read.", retry=False)
        signature = private_key.sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA256())
        return {"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": signing_input + "." + _b64url(signature)}

    def token(self, force=False):
        with _TOKENS_LOCK:
            cached = _TOKENS.get(self.cache_key)
        if cached and not force and cached[1] > time.time():
            return cached[0]
        token, expires = self._new_token()
        with _TOKENS_LOCK:
            _TOKENS[self.cache_key] = (token, expires)
        return token

    # ------------------------------------------------------------------ HTTP
    def _call(self, method, url, params=None, json_body=None, data=None, headers=None, stream=False,
              expect=(200,), retry_auth=True, raw_url=False):
        params = dict(params or {})
        if not raw_url:
            params.setdefault("supportsAllDrives", "true")
        hdrs = {"Authorization": "Bearer " + self.token()}
        hdrs.update(headers or {})
        attempts = 0
        while True:
            attempts += 1
            try:
                r = self.session.request(method, url, params=params, json=json_body, data=data, headers=hdrs,
                                         timeout=TIMEOUT, stream=stream)
            except requests.RequestException as e:
                if attempts < 3:
                    time.sleep(attempts)
                    continue
                raise DriveError("Google Drive could not be reached: %s" % e)
            if r.status_code == 401 and retry_auth:
                hdrs["Authorization"] = "Bearer " + self.token(force=True)
                retry_auth = False
                continue
            if r.status_code in (429, 500, 502, 503, 504) and attempts < 3:
                time.sleep(attempts * 1.5)
                continue
            if r.status_code not in expect:
                raise DriveError(self._message(r), r.status_code,
                                 retry=r.status_code in (0, 408, 429, 500, 502, 503, 504))
            return r

    @staticmethod
    def _message(r):
        try:
            err = r.json().get("error")
            msg = err.get("message") if isinstance(err, dict) else err
        except (ValueError, AttributeError):
            msg = r.text[:200]
        return "Google Drive answered %s: %s" % (r.status_code, msg or r.reason)

    # ------------------------------------------------------------------ API
    def about(self):
        return self._call("GET", self.api + "/about", {"fields": "user,storageQuota"}).json()

    def get(self, file_id, fields="id,name,mimeType,parents,trashed,webViewLink,size,driveId"):
        return self._call("GET", "%s/files/%s" % (self.api, file_id), {"fields": fields}).json()

    def shared_drive(self, drive_id):
        return self._call("GET", "%s/drives/%s" % (self.api, drive_id)).json()

    def create_folder(self, name, parent):
        meta = {"name": name, "mimeType": FOLDER_MIME, "parents": [parent]}
        return self._call("POST", self.api + "/files", {"fields": "id,name,webViewLink"}, json_body=meta).json()

    def find_folder(self, name, parent):
        q = "name = '%s' and '%s' in parents and mimeType = '%s' and trashed = false" % (
            name.replace("\\", "\\\\").replace("'", "\\'"), parent, FOLDER_MIME)
        params = {"q": q, "fields": "files(id,name,webViewLink)", "includeItemsFromAllDrives": "true",
                  "pageSize": 10}
        files = self._call("GET", self.api + "/files", params).json().get("files", [])
        return files[0] if files else None

    def upload(self, name, content, mimetype, parent=None, file_id=None, app_props=None):
        """Create (``parent``) or replace the content of (``file_id``) a file."""
        meta = {"name": name, "mimeType": mimetype or "application/octet-stream"}
        if app_props:
            meta["appProperties"] = app_props
        if parent and not file_id:
            meta["parents"] = [parent]
        fields = "id,name,webViewLink,size,parents"
        if len(content) <= SIMPLE_LIMIT:
            boundary = "nxb_" + uuid.uuid4().hex
            body = b"".join([
                ("--%s\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n" % boundary).encode(),
                json.dumps(meta).encode(), b"\r\n",
                ("--%s\r\nContent-Type: %s\r\n\r\n" % (boundary, meta["mimeType"])).encode(),
                content, ("\r\n--%s--" % boundary).encode()])
            url = "%s/files%s" % (self.upload_api, "/" + file_id if file_id else "")
            return self._call("PATCH" if file_id else "POST", url, {"uploadType": "multipart", "fields": fields},
                              data=body, headers={"Content-Type": "multipart/related; boundary=" + boundary}).json()
        return self._resumable(meta, content, file_id, fields)

    def _resumable(self, meta, content, file_id, fields):
        url = "%s/files%s" % (self.upload_api, "/" + file_id if file_id else "")
        r = self._call("PATCH" if file_id else "POST", url, {"uploadType": "resumable", "fields": fields},
                       json_body=meta, headers={"X-Upload-Content-Type": meta["mimeType"],
                                                "X-Upload-Content-Length": str(len(content))})
        session_url = r.headers.get("Location")
        if not session_url:
            raise DriveError("Google Drive did not open an upload session.")
        total, pos = len(content), 0
        while pos < total:
            chunk = content[pos:pos + CHUNK]
            end = pos + len(chunk) - 1
            r = self._call("PUT", session_url, data=chunk, expect=(200, 201, 308), raw_url=True,
                           headers={"Content-Range": "bytes %d-%d/%d" % (pos, end, total)})
            if r.status_code in (200, 201):
                return r.json()
            rng = r.headers.get("Range")
            pos = int(rng.split("-")[1]) + 1 if rng else end + 1
        raise DriveError("The upload session ended without a result.")

    def download(self, file_id):
        r = self._call("GET", "%s/files/%s" % (self.api, file_id), {"alt": "media"})
        return r.content

    def rename(self, file_id, name):
        return self._call("PATCH", "%s/files/%s" % (self.api, file_id), {"fields": "id,name"},
                          json_body={"name": name}).json()

    def trash(self, file_id):
        self._call("PATCH", "%s/files/%s" % (self.api, file_id), {"fields": "id"}, json_body={"trashed": True})

    def delete(self, file_id):
        self._call("DELETE", "%s/files/%s" % (self.api, file_id), expect=(200, 204))

    def share_anyone(self, file_id):
        self._call("POST", "%s/files/%s/permissions" % (self.api, file_id), {"fields": "id"},
                   json_body={"role": "reader", "type": "anyone"})


def authorization_url(client_id, redirect_uri, state, scope="full", auth_url=None):
    return (auth_url or AUTH_URL) + "?" + urlencode({
        "client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code",
        "scope": SCOPES.get(scope, SCOPES["full"]), "access_type": "offline", "prompt": "consent",
        "include_granted_scopes": "true", "state": state})


def exchange_code(token_url, client_id, client_secret, code, redirect_uri):
    try:
        r = requests.post(token_url or TOKEN_URL, data={
            "grant_type": "authorization_code", "code": code, "client_id": client_id,
            "client_secret": client_secret, "redirect_uri": redirect_uri}, timeout=TIMEOUT)
    except requests.RequestException as e:
        raise DriveError("Google could not be reached: %s" % e)
    try:
        info = r.json()
    except ValueError:
        info = {}
    if r.status_code != 200 or not info.get("refresh_token"):
        raise DriveError("Google did not give access: %s" % (info.get("error_description") or info.get("error")
                                                              or "no refresh token returned"), r.status_code,
                         retry=False)
    return info
