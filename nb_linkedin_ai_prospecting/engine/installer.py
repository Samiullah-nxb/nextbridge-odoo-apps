# -*- coding: utf-8 -*-
"""Prepare engine: a private virtualenv with Playwright and Chromium inside Odoo's data folder.

No terminal needed. If the server lacks the browser's system libraries, the status shows the one
command a server administrator runs once.
"""
import json
import logging
import os
import subprocess
import sys
import threading
import time

from odoo.tools import config

_logger = logging.getLogger(__name__)
PLAYWRIGHT_SPEC = "playwright>=1.47,<2"
_lock = threading.Lock()


def base_dir():
    path = os.path.join(config["data_dir"], "nb_linkedin_ai_prospecting")
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def venv_python():
    return os.path.join(base_dir(), "engine_venv", "bin", "python")


def browsers_dir():
    return os.path.join(base_dir(), "browsers")


def engine_env():
    env = dict(os.environ)
    env["PLAYWRIGHT_BROWSERS_PATH"] = browsers_dir()
    env.pop("PYTHONPATH", None)
    return env


def _status_file():
    return os.path.join(base_dir(), "engine_status.json")


def _write_status(**kw):
    data = _read_status()
    data.update(kw, updated=time.time())
    with open(_status_file(), "w") as f:
        json.dump(data, f)


def _read_status():
    try:
        with open(_status_file()) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def engine_status():
    from ..models.li_common import is_odoo_sh
    if is_odoo_sh():
        return {"state": "unavailable", "label": "Not available on Odoo.sh (use AI browser agent)"}
    st = _read_status()
    state = st.get("state") or ("ready" if os.path.exists(venv_python()) and os.path.isdir(browsers_dir())
                                else "missing")
    labels = {"missing": "Not prepared", "installing": "Preparing…", "ready": "Ready",
              "needs_admin": "Needs one administrator command", "error": "Error"}
    return {"state": state, "label": labels.get(state, state), "detail": st.get("detail", ""),
            "admin_command": st.get("admin_command", "")}


def _run(cmd, timeout=1800):
    from .manager import _lift_memory_limit
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=engine_env(),
                          preexec_fn=_lift_memory_limit)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _prepare():
    try:
        _write_status(state="installing", detail="Creating the engine environment…", admin_command="")
        venv = os.path.join(base_dir(), "engine_venv")
        if not os.path.exists(venv_python()):
            code, out = _run([sys.executable, "-m", "venv", venv])
            if code:
                raise RuntimeError("venv failed: %s" % out[-800:])
        _write_status(detail="Installing Playwright…")
        code, out = _run([venv_python(), "-m", "pip", "install", "-q", "--upgrade", "pip", PLAYWRIGHT_SPEC])
        if code:
            raise RuntimeError("pip failed: %s" % out[-800:])
        _write_status(detail="Downloading Chromium (about 300 MB)…")
        code, out = _run([venv_python(), "-m", "playwright", "install", "chromium"])
        if code:
            raise RuntimeError("browser download failed: %s" % out[-800:])
        _write_status(detail="Testing the browser…")
        test = ("from playwright.sync_api import sync_playwright\n"
                "with sync_playwright() as p:\n"
                "    b = p.chromium.launch(headless=True); b.new_page().goto('about:blank'); b.close()\n"
                "print('BROWSER_OK')\n")
        code, out = _run([venv_python(), "-c", test], timeout=180)
        if "BROWSER_OK" in out:
            _write_status(state="ready", detail="Engine ready.", admin_command="")
            return
        code2, deps = _run([venv_python(), "-m", "playwright", "install-deps", "--dry-run", "chromium"], timeout=120)
        cmd = "\n".join(l for l in deps.splitlines() if l.strip().startswith(("sudo", "apt"))) or deps.strip()
        if cmd and not cmd.startswith("sudo"):
            cmd = "sudo " + cmd
        _write_status(state="needs_admin", admin_command=(cmd + "\nsudo apt-get install -y xvfb").strip(),
                      detail="The browser needs system libraries. Ask a server administrator to run the command "
                             "below once, then press Refresh status.\n\n" + out[-600:])
    except Exception as e:  # noqa: BLE001
        _logger.exception("LinkedIn engine preparation failed")
        _write_status(state="error", detail=str(e)[:2000])


def prepare_engine():
    if not _lock.acquire(blocking=False):
        return False
    def run():
        try:
            _prepare()
        finally:
            _lock.release()
    threading.Thread(target=run, name="li-engine-prepare", daemon=True).start()
    return True


def recheck_browser():
    """Called by Refresh status after an administrator installed the libraries."""
    st = _read_status()
    if st.get("state") == "needs_admin":
        prepare_engine()
