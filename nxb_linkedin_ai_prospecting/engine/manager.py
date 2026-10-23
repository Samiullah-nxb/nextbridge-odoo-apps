# -*- coding: utf-8 -*-
"""Start, stop and talk to the per-account engine process (127.0.0.1 only, random secret path)."""
import json
import logging
import os
import secrets
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request

from odoo import _
from odoo.exceptions import UserError

from .installer import base_dir, engine_env, engine_status, venv_python

_logger = logging.getLogger(__name__)
SERVER_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "engine_server.py")


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class EngineClient:
    def __init__(self, profile):
        self.profile = profile  # sudo record

    @property
    def account_dir(self):
        path = os.path.join(base_dir(), "accounts", "%s_%s" % (self.profile.env.cr.dbname, self.profile.id))
        os.makedirs(path, mode=0o700, exist_ok=True)
        os.chmod(path, 0o700)
        return path

    def _url(self, action):
        return "http://127.0.0.1:%s/%s/%s" % (self.profile.engine_port, self.profile.engine_secret, action)

    # ------------------------------------------------------------ lifecycle
    def is_alive(self):
        if not self.profile.engine_port or not self.profile.engine_secret:
            return False
        try:
            with urllib.request.urlopen(self._url("health"), timeout=5) as r:
                return json.loads(r.read()).get("ok", False)
        except Exception:  # noqa: BLE001
            return False

    def start(self, wait=90):
        if self.is_alive():
            self.profile.write({"engine_state": "running", "engine_error": False})
            return True
        st = engine_status()
        if st["state"] != "ready":
            raise UserError(_("The LinkedIn engine is not ready (%s). Go to Settings → LinkedIn engine → "
                              "Prepare engine.") % st["label"])
        self._kill_pid()
        port, secret = _free_port(), secrets.token_urlsafe(24)
        log = open(os.path.join(self.account_dir, "engine.log"), "ab")
        cmd = [venv_python(), SERVER_SCRIPT, "--port", str(port), "--secret", secret,
               "--data-dir", self.account_dir]
        env = engine_env()
        if not env.get("DISPLAY") and _which("xvfb-run"):
            cmd = ["xvfb-run", "-a", "--server-args=-screen 0 1280x900x24"] + cmd + ["--headful"]
        proc = subprocess.Popen(cmd, stdout=log, stderr=log, stdin=subprocess.DEVNULL, env=env,
                                start_new_session=True, close_fds=True, preexec_fn=_lift_memory_limit)
        self.profile.write({"engine_port": port, "engine_secret": secret, "engine_pid": proc.pid})
        deadline = time.time() + wait
        while time.time() < deadline:
            if proc.poll() is not None:
                break
            if self.is_alive():
                self.profile.write({"engine_state": "running", "engine_error": False})
                return True
            time.sleep(1)
        tail = self.log_tail()
        self.profile.write({"engine_state": "error", "engine_error": tail})
        raise UserError(_("The engine did not start. Last log lines:\n%s") % tail)

    def stop(self):
        if self.is_alive():
            try:
                self._post("shutdown", {}, timeout=10)
            except Exception:  # noqa: BLE001
                pass
            time.sleep(1)
        self._kill_pid()
        self.profile.write({"engine_state": "stopped", "engine_port": 0, "engine_secret": False, "engine_pid": 0})

    def _kill_pid(self):
        pid = self.profile.engine_pid
        if not pid:
            return
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            pass

    def log_tail(self, n=30):
        try:
            with open(os.path.join(self.account_dir, "engine.log"), "rb") as f:
                return b"\n".join(f.read().splitlines()[-n:]).decode(errors="replace")
        except OSError:
            return ""

    # ------------------------------------------------------------ calls
    def _post(self, action, params, timeout):
        req = urllib.request.Request(self._url(action), data=json.dumps(params).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())

    def call(self, action, timeout=120, **params):
        if not self.profile.engine_port:
            return {"ok": False, "code": "engine_down", "error": "Engine not running"}
        try:
            return self._post(action, params, timeout)
        except urllib.error.URLError as e:
            return {"ok": False, "code": "engine_down", "error": "Engine unreachable: %s" % e}
        except TimeoutError:
            # The action may have happened: callers treat sends with unknown outcome as 'unknown'.
            return {"ok": False, "code": "unknown", "error": "Engine timed out"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "code": "engine_down", "error": str(e)}


def _lift_memory_limit():
    """Odoo caps its workers' virtual memory (limit_memory_hard). Chromium reserves far more address space
    than it uses and crashes under that cap, so the engine process gets the hard limit instead."""
    import resource
    for lim in (resource.RLIMIT_AS, resource.RLIMIT_DATA):
        try:
            _soft, hard = resource.getrlimit(lim)
            resource.setrlimit(lim, (hard, hard))
        except (ValueError, OSError):
            pass


def _which(name):
    from shutil import which
    return which(name)
