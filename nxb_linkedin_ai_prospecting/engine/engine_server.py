#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LinkedIn engine: one process per LinkedIn account, started by Odoo.

Runs a persistent Chromium profile through Playwright and serves JSON actions on
http://127.0.0.1:<port>/<secret>/<action>. Single-threaded on purpose: one LinkedIn action at a time
per account, and Playwright's sync API stays on one thread.

This file has no Odoo imports; it runs inside the engine's own virtualenv.
LinkedIn changes its markup often, so the page reading below is deliberately generic (roles, labels,
visible text) rather than tied to CSS class names, and every action reports what it saw.
"""
import argparse
import base64
import json
import os
import random
import re
import signal
import sys
import time
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import quote_plus

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

LI = "https://www.linkedin.com"
VIEWPORT = {"width": 1280, "height": 860}


class EngineError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def pause(a=0.6, b=1.6):
    time.sleep(random.uniform(a, b))


class LinkedInEngine:
    def __init__(self, data_dir, headless=True):
        self.data_dir = data_dir
        self.pages = 0
        self.own_name = None
        self.pw = sync_playwright().start()
        launch = dict(
            user_data_dir=os.path.join(data_dir, "browser"),
            headless=headless,
            viewport=VIEWPORT,
            locale="en-US",
            args=["--disable-blink-features=AutomationControlled", "--no-first-run",
                  "--no-default-browser-check", "--disable-gpu", "--disable-dev-shm-usage"],
        )
        attempts = [dict(launch, channel="chromium"), dict(launch)]  # full Chromium first, then default build
        if not headless:
            attempts += [dict(launch, headless=True, channel="chromium"), dict(launch, headless=True)]
        last = None
        for kw in attempts:
            try:
                self.ctx = self.pw.chromium.launch_persistent_context(**kw)
                break
            except Exception as e:  # noqa: BLE001
                last = e
                print("launch attempt failed (%s): %s" % (
                    "headless" if kw["headless"] else "visible", str(e).splitlines()[0]), flush=True)
        else:
            raise last
        self.ctx.set_default_timeout(25000)
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        state_file = os.path.join(data_dir, "state.json")
        if os.path.exists(state_file):
            try:
                with open(state_file) as f:
                    self.own_name = json.load(f).get("own_name")
            except Exception:  # noqa: BLE001
                pass

    def close(self):
        try:
            self.ctx.close()
        finally:
            self.pw.stop()

    def _save_state(self):
        with open(os.path.join(self.data_dir, "state.json"), "w") as f:
            json.dump({"own_name": self.own_name}, f)

    # ------------------------------------------------------------ navigation
    def goto(self, url, wait="domcontentloaded"):
        self.pages += 1
        try:
            self.page.goto(url, wait_until=wait, timeout=45000)
        except PWTimeout:
            pass
        pause(1.5, 3.0)
        self._check_session()

    def _check_session(self):
        url = self.page.url
        if any(x in url for x in ("/login", "/authwall", "/uas/login", "/signup")):
            raise EngineError("session_expired", "LinkedIn asks to sign in again")
        if "/checkpoint/" in url:
            body = self.text()
            if re.search(r"restricted|suspended", body, re.I):
                raise EngineError("restricted", "LinkedIn restricted this account")
            raise EngineError("session_expired", "LinkedIn security check")

    def text(self, selector="body"):
        try:
            return self.page.locator(selector).first.inner_text(timeout=8000)
        except Exception:  # noqa: BLE001
            return ""

    def scroll(self, times=3):
        for _ in range(times):
            self.page.mouse.wheel(0, random.randint(900, 1500))
            pause(0.8, 1.6)

    def top_card(self):
        """The profile's top card: the first section of <main>."""
        loc = self.page.locator("main section").first
        loc.wait_for(timeout=20000)
        return loc

    def click_if(self, locator, timeout=4000):
        try:
            locator.first.wait_for(state="visible", timeout=timeout)
            pause(0.4, 1.0)
            locator.first.click()
            return True
        except Exception:  # noqa: BLE001
            return False

    # ------------------------------------------------------------ profile
    def _profile_state(self):
        """connect | pending | message | follow_only | email_required, judged from the top card."""
        card = self.top_card()
        if card.get_by_role("button", name=re.compile(r"^Pending", re.I)).count():
            return "pending"
        if card.get_by_role("button", name=re.compile(r"Invite .* to connect|^Connect$", re.I)).count():
            return "connect"
        degree = self.text("main section")
        if re.search(r"\b1st\b", degree) and card.get_by_role("button", name=re.compile(r"^Message", re.I)).count():
            return "message"
        # Connect may be hidden under More
        more = card.get_by_role("button", name=re.compile(r"^More", re.I))
        if self.click_if(more, 3000):
            menu = self.page.get_by_role("button", name=re.compile(r"Invite .* to connect", re.I))
            pending = self.page.get_by_role("button", name=re.compile(r"Pending|Withdraw", re.I))
            state = "connect" if menu.count() else ("pending" if pending.count() else None)
            self.page.keyboard.press("Escape")
            if state:
                return state
        if re.search(r"\b1st\b", degree):
            return "message"
        return "follow_only"

    def read_profile(self, url):
        self.goto(url)
        if "/404" in self.page.url or "this page doesn" in self.text().lower():
            raise EngineError("not_found", "Profile not found")
        card_text = self.text("main section")
        name = self.text("main h1").strip()
        lines = [l.strip() for l in card_text.splitlines() if l.strip()]
        headline = ""
        location = ""
        if name in lines:
            i = lines.index(name)
            rest = [l for l in lines[i + 1:] if not re.match(r"^(·|\d(st|nd|rd|th)|He/Him|She/Her|They/Them)", l)]
            headline = rest[0] if rest else ""
            for l in rest[1:6]:
                if "," in l or re.search(r"Area|Region|Pakistan|India|United|Germany|France|Kingdom", l):
                    location = l
                    break
        self.scroll(3)
        main = self.text("main")
        main = re.sub(r"\n{3,}", "\n\n", main)[:6000]
        company = ""
        m = re.search(r"Experience\s*\n(.+)\n(.+)", main)
        if m:
            company = m.group(2).split("·")[0].strip()
        self.page.mouse.wheel(0, -5000)
        state = self._profile_state()
        return {"name": name, "headline": headline, "location": location, "company": company,
                "text": main, "connect_state": state}

    def me(self):
        self.goto(LI + "/in/me/")
        name = self.text("main h1").strip()
        card = [l.strip() for l in self.text("main section").splitlines() if l.strip()]
        headline = card[card.index(name) + 1] if name in card and card.index(name) + 1 < len(card) else ""
        lang = self.page.evaluate("document.documentElement.lang || ''")
        english = lang.lower().startswith("en") or ("Experience" in self.text("main"))
        self.own_name = name
        self._save_state()
        return {"name": name, "headline": headline, "url": self.page.url, "english": english}

    # ------------------------------------------------------------ invites
    def _weekly_limit_visible(self):
        body = self.text("body").lower()
        return "weekly invitation limit" in body or "reached the weekly" in body or \
            "too many pending invitations" in body

    def send_invite(self, url, note=""):
        self.goto(url)
        state = self._profile_state()
        if state == "pending":
            return {"result": "pending_already"}
        if state == "message":
            return {"result": "connected_already"}
        clicked = False
        card = self.top_card()
        # Route 1: Connect button in the top card
        clicked = self.click_if(card.get_by_role("button", name=re.compile(r"Invite .* to connect|^Connect$", re.I)))
        # Route 2: More menu
        if not clicked:
            if self.click_if(card.get_by_role("button", name=re.compile(r"^More", re.I))):
                clicked = self.click_if(self.page.get_by_role(
                    "button", name=re.compile(r"Invite .* to connect", re.I)))
        # Route 3: the invite link
        if not clicked:
            slug = re.search(r"/in/([^/?#]+)", url)
            if slug:
                self.goto(LI + "/preload/custom-invite/?vanityName=" + slug.group(1))
                clicked = self.page.get_by_role("dialog").count() > 0
        if not clicked:
            return {"result": "no_connect"}
        pause()
        dialog = self.page.get_by_role("dialog").first
        dtext = self.text('[role="dialog"]').lower()
        if self._weekly_limit_visible():
            self.page.keyboard.press("Escape")
            return {"result": "weekly_limit"}
        if "email" in dtext and dialog.locator('input[type="email"], input[name="email"]').count():
            self.page.keyboard.press("Escape")
            return {"result": "email_required"}
        if note:
            if self.click_if(dialog.get_by_role("button", name=re.compile(r"Add a note", re.I))):
                box = self.page.locator('[role="dialog"] textarea').first
                box.click()
                box.fill("")
                self.page.keyboard.insert_text(note[:300])
                pause()
            else:
                note = ""
        send = self.page.get_by_role("button", name=re.compile(
            r"^Send( now| invitation)?$|Send without a note" if not note else r"^Send( now| invitation)?$", re.I))
        if not self.click_if(send, 6000):
            self.page.keyboard.press("Escape")
            return {"result": "failed", "note": "Send button not found"}
        pause(2, 3.5)
        if self._weekly_limit_visible():
            self.page.keyboard.press("Escape")
            return {"result": "weekly_limit"}
        try:
            self.page.mouse.wheel(0, -3000)
            pending = self.top_card().get_by_role("button", name=re.compile(r"^Pending", re.I)).count()
        except Exception:  # noqa: BLE001
            pending = 0
        # Odoo double-checks every invite ~10 minutes later against the Sent invitations page.
        return {"result": "sent", "verified_pending": bool(pending)}

    def follow(self, url):
        self.goto(url)
        card = self.top_card()
        if card.get_by_role("button", name=re.compile(r"Invite .* to connect|^Connect$", re.I)).count():
            return {"result": "failed", "note": "Connect is visible; not following"}
        if self.click_if(card.get_by_role("button", name=re.compile(r"^Follow", re.I))):
            return {"result": "followed"}
        if self.click_if(card.get_by_role("button", name=re.compile(r"^More", re.I))):
            if self.click_if(self.page.get_by_role("button", name=re.compile(r"^Follow", re.I))):
                return {"result": "followed"}
        return {"result": "failed", "note": "No Follow button"}

    def withdraw_invite(self, url):
        self.goto(url)
        card = self.top_card()
        if not self.click_if(card.get_by_role("button", name=re.compile(r"^Pending", re.I))):
            if self.click_if(card.get_by_role("button", name=re.compile(r"^More", re.I))):
                if not self.click_if(self.page.get_by_role("button", name=re.compile(r"Withdraw|Pending", re.I))):
                    return {"result": "not_pending"}
            else:
                return {"result": "not_pending"}
        if self.click_if(self.page.get_by_role("dialog").get_by_role("button", name=re.compile(r"^Withdraw", re.I))):
            pause(1.5, 2.5)
            return {"result": "withdrawn"}
        return {"result": "failed", "note": "Withdraw confirm not found"}

    def sent_invitations(self, limit=200):
        self.goto(LI + "/mynetwork/invitation-manager/sent/")
        people, seen = [], set()
        complete = False
        for _ in range(12):
            rows = self.page.evaluate(r"""() => {
                const out = [];
                for (const a of document.querySelectorAll('main a[href*="/in/"]')) {
                    const li = a.closest('li') || a.closest('[data-view-name]') || a.parentElement;
                    if (!li) continue;
                    const t = li.innerText || '';
                    if (!/Withdraw/i.test(t)) continue;   // only invitation rows
                    const name = (a.innerText || '').split('\n')[0].trim();
                    out.push({url: a.href.split('?')[0], name});
                }
                return out;
            }""")
            for r in rows:
                key = r["url"]
                if key not in seen and r["name"]:
                    seen.add(key)
                    people.append(r)
            if len(people) >= limit:
                break
            nxt = self.page.get_by_role("button", name=re.compile(r"^Next$|Load more|Show more", re.I))
            if nxt.count() and nxt.first.is_enabled():
                self.click_if(nxt)
                self.pages += 1
                pause(1.5, 2.5)
            else:
                before = len(people)
                self.scroll(2)
                if len(people) == before and _ > 1:
                    complete = True
                    break
        return {"people": people, "complete": complete}

    def recent_connections(self, limit=40):
        self.goto(LI + "/mynetwork/invite-connect/connections/")
        self.scroll(2)
        rows = self.page.evaluate(r"""(limit) => {
            const out = [], seen = new Set();
            for (const a of document.querySelectorAll('main a[href*="/in/"]')) {
                const url = a.href.split('?')[0];
                const name = (a.innerText || '').split('\n')[0].trim();
                if (!name || seen.has(url)) continue;
                seen.add(url); out.push({url, name});
                if (out.length >= limit) break;
            }
            return out;
        }""", limit)
        return {"people": rows}

    # ------------------------------------------------------------ search
    def search_people(self, keywords, location="", limit=25, page=1):
        q = " ".join(x for x in (keywords, location) if x)
        self.goto(LI + "/search/results/people/?keywords=%s&origin=GLOBAL_SEARCH_HEADER&page=%s" % (
            quote_plus(q), int(page or 1)))
        self.scroll(2)
        rows = self.page.evaluate(r"""() => {
            const out = [], seen = new Set();
            for (const a of document.querySelectorAll('main a[href*="/in/"]')) {
                const url = a.href.split('?')[0];
                if (seen.has(url)) continue;
                const li = a.closest('li');
                if (!li) continue;
                const lines = (li.innerText || '').split('\n').map(s => s.trim()).filter(Boolean)
                    .filter(s => !/^(View .* profile|Connect|Message|Follow|Pending|Status is)/i.test(s));
                if (!lines.length) continue;
                seen.add(url);
                const name = lines[0];
                const degree = (lines.find(s => /\b(1st|2nd|3rd\+?)\b/.test(s)) || '').match(/1st|2nd|3rd/);
                const rest = lines.slice(1).filter(s => !/^•|\b(1st|2nd|3rd\+?)\b|mutual connection|followers/i.test(s));
                out.push({url, name: name.replace(/\s*•.*$/, ''), headline: rest[0] || '',
                          location: rest[1] || '', degree: degree ? degree[0] : ''});
            }
            return out;
        }""")
        return {"people": [r for r in rows if "LinkedIn Member" not in r["name"]][:limit]}

    # ------------------------------------------------------------ messaging
    def _open_thread(self, url, thread_url=""):
        if thread_url and "/messaging/thread/" in thread_url:
            self.goto(thread_url)
        else:
            self.goto(url)
            card = self.top_card()
            if not self.click_if(card.get_by_role("button", name=re.compile(r"^Message", re.I))):
                if not self.click_if(card.get_by_role("link", name=re.compile(r"^Message", re.I))):
                    raise EngineError("not_connected", "No Message button")
            pause(1.5, 2.5)
        box = self.page.locator('div[contenteditable="true"][role="textbox"], .msg-form__contenteditable').last
        box.wait_for(timeout=20000)
        return box

    def _read_messages(self):
        own = (self.own_name or "").strip()
        rows = self.page.evaluate(r"""(own) => {
            const lists = document.querySelectorAll('.msg-s-message-list-content, [class*="msg-s-message-list"]');
            const root = lists.length ? lists[lists.length - 1] : document;
            const out = [];
            let sender = '';
            for (const ev of root.querySelectorAll('li')) {
                const nameEl = ev.querySelector('.msg-s-message-group__name, [class*="message-group__name"]');
                if (nameEl && nameEl.innerText.trim()) sender = nameEl.innerText.trim();
                const other = ev.querySelector('[class*="listitem--other"]');
                for (const body of ev.querySelectorAll('.msg-s-event-listitem__body, [class*="event-listitem__body"]')) {
                    const text = (body.innerText || '').trim();
                    if (!text) continue;
                    const mine = other ? false : (own ? sender === own : false);
                    out.push({direction: mine ? 'out' : 'in', text, sender});
                }
                for (const att of ev.querySelectorAll('[class*="attachment"] [class*="name"], [class*="attachment-item"]')) {
                    const t = (att.innerText || '').trim().split('\n').join(' ');
                    if (t) out.push({direction: (own && sender === own) ? 'out' : 'in',
                                     text: '[Attachment] ' + t, attachment: true, sender});
                }
            }
            return out;
        }""", own)
        # de-duplicate consecutive repeats from nested lists
        clean = []
        for r in rows:
            if not clean or clean[-1]["text"] != r["text"]:
                clean.append(r)
        return clean[-30:]

    def read_conversation(self, url, thread_url=""):
        self._open_thread(url, thread_url)
        pause(1, 2)
        msgs = self._read_messages()
        turl = self.page.url if "/messaging/thread/" in self.page.url else thread_url
        return {"messages": msgs, "thread_url": turl}

    def send_message(self, url, text, thread_url=""):
        box = self._open_thread(url, thread_url)
        before = self._read_messages()
        box.click()
        pause(0.5, 1.0)
        # insert_text never presses Enter, so the message cannot go out half-written
        self.page.keyboard.insert_text(text)
        pause(1.0, 2.0)
        send = self.page.locator('button.msg-form__send-button, button[type="submit"]').filter(
            has_text=re.compile(r"^\s*Send\s*$", re.I))
        if not self.click_if(send, 6000):
            return {"result": "unknown", "note": "Send button not found after typing"}
        pause(2.0, 3.5)
        after = self._read_messages()
        probe = text.strip()[:40]
        if any(probe in m["text"] for m in after[len(before) - 1 if before else 0:]):
            return {"result": "sent", "thread_url": self.page.url}
        return {"result": "unknown", "note": "Message not visible after sending"}

    def read_inbox(self, limit=40):
        self.goto(LI + "/messaging/")
        rows = self.page.evaluate(r"""(limit) => {
            const out = [];
            for (const li of document.querySelectorAll('li.msg-conversation-listitem, li[class*="conversation-listitem"]')) {
                const a = li.querySelector('a[href*="/messaging/thread/"]');
                const nameEl = li.querySelector('h3, [class*="participant-names"]');
                const snip = li.querySelector('[class*="message-snippet"], p');
                const text = snip ? snip.innerText.trim() : '';
                out.push({
                    name: nameEl ? nameEl.innerText.trim() : '',
                    thread_url: a ? a.href.split('?')[0] : '',
                    last_text: text.replace(/^You:\s*/, ''),
                    last_from_me: /^You:/.test(text),
                    unread: /unread/.test(li.innerHTML) || !!li.querySelector('[class*="unread"]'),
                });
                if (out.length >= limit) break;
            }
            return out;
        }""", limit)
        return {"threads": [r for r in rows if r["name"]]}

    # ------------------------------------------------------------ posts
    def publish_post(self, text):
        self.goto(LI + "/feed/")
        if not self.click_if(self.page.get_by_role("button", name=re.compile(r"Start a post", re.I)), 10000):
            raise EngineError("error", "Start a post not found")
        editor = self.page.locator('[role="dialog"] div[contenteditable="true"]').first
        editor.wait_for(timeout=15000)
        editor.click()
        for i, para in enumerate(text.split("\n")):
            if i:
                self.page.keyboard.press("Enter")
            if para:
                self.page.keyboard.insert_text(para)
        pause(1.5, 2.5)
        if not self.click_if(self.page.get_by_role("dialog").get_by_role("button", name=re.compile(r"^Post$", re.I))):
            raise EngineError("unknown", "Post button not found")
        pause(4, 6)
        link = self.page.get_by_role("link", name=re.compile(r"View post", re.I))
        url = link.first.get_attribute("href") if link.count() else ""
        return {"result": "sent", "url": url}

    def post_stats(self, url):
        self.goto(url)
        body = self.text("main")
        reactions = 0
        m = re.search(r"([\d,]+)\s*(reactions?|likes?)", body, re.I)
        if m:
            reactions = int(m.group(1).replace(",", ""))
        m = re.search(r"([\d,]+)\s*comments?", body, re.I)
        comments = int(m.group(1).replace(",", "")) if m else 0
        texts = self.page.evaluate(r"""() => Array.from(document.querySelectorAll(
            'article[class*="comment"] [class*="main-content"], [class*="comments-comment-item__main-content"]'))
            .map(e => e.innerText.trim()).filter(Boolean).slice(0, 30)""")
        return {"reactions": reactions, "comments": comments, "comment_texts": texts}

    # ------------------------------------------------------------ sign-in inside Odoo
    def login_open(self):
        self.page.goto(LI + "/login", wait_until="domcontentloaded")
        return {"opened": True}

    def screenshot(self):
        png = self.page.screenshot(type="jpeg", quality=60)
        return {"image": base64.b64encode(png).decode(), "url": self.page.url,
                "width": VIEWPORT["width"], "height": VIEWPORT["height"]}

    def input(self, kind, x=None, y=None, text=None, key=None):
        if kind == "click":
            self.page.mouse.click(float(x), float(y))
        elif kind == "type" and text:
            self.page.keyboard.type(text, delay=40)
        elif kind == "key" and key:
            self.page.keyboard.press(key)
        elif kind == "scroll":
            self.page.mouse.wheel(0, float(y or 300))
        pause(0.3, 0.6)
        return {"ok": True}

    def login_status(self):
        cookies = {c["name"]: c for c in self.ctx.cookies(LI)}
        url = self.page.url
        logged = "li_at" in cookies and not any(x in url for x in ("/login", "/checkpoint", "/authwall"))
        return {"logged_in": logged, "url": url}

    def import_session(self, cookies):
        """cookies: list of Playwright-style cookies, or {'li_at': '...'}."""
        if isinstance(cookies, dict) and cookies.get("li_at"):
            cookies = [{"name": "li_at", "value": cookies["li_at"].strip(), "domain": ".linkedin.com",
                        "path": "/", "httpOnly": True, "secure": True, "sameSite": "None"}]
        clean = []
        for c in cookies or []:
            if "linkedin" not in (c.get("domain") or ""):
                continue
            item = {k: c[k] for k in ("name", "value", "domain", "path", "expires", "httpOnly", "secure")
                    if k in c and c[k] is not None}
            same = c.get("sameSite")
            if same in ("Strict", "Lax", "None"):
                item["sameSite"] = same
            item.setdefault("path", "/")
            clean.append(item)
        if not clean:
            raise EngineError("error", "No LinkedIn cookies found in the upload")
        self.ctx.add_cookies(clean)
        self.goto(LI + "/feed/")
        return {"imported": len(clean)}


# ---------------------------------------------------------------- HTTP plumbing
ACTIONS = {
    "me": lambda e, p: e.me(),
    "read_profile": lambda e, p: e.read_profile(p["url"]),
    "send_invite": lambda e, p: e.send_invite(p["url"], p.get("note") or ""),
    "follow": lambda e, p: e.follow(p["url"]),
    "withdraw_invite": lambda e, p: e.withdraw_invite(p["url"]),
    "sent_invitations": lambda e, p: e.sent_invitations(int(p.get("limit") or 200)),
    "recent_connections": lambda e, p: e.recent_connections(int(p.get("limit") or 40)),
    "search_people": lambda e, p: e.search_people(p.get("keywords") or "", p.get("location") or "",
                                                  int(p.get("limit") or 25), int(p.get("page") or 1)),
    "read_conversation": lambda e, p: e.read_conversation(p["url"], p.get("thread_url") or ""),
    "send_message": lambda e, p: e.send_message(p["url"], p["text"], p.get("thread_url") or ""),
    "read_inbox": lambda e, p: e.read_inbox(int(p.get("limit") or 40)),
    "publish_post": lambda e, p: e.publish_post(p["text"]),
    "post_stats": lambda e, p: e.post_stats(p["url"]),
    "login_open": lambda e, p: e.login_open(),
    "screenshot": lambda e, p: e.screenshot(),
    "input": lambda e, p: e.input(p.get("kind"), p.get("x"), p.get("y"), p.get("text"), p.get("key")),
    "login_status": lambda e, p: e.login_status(),
    "import_session": lambda e, p: e.import_session(p.get("cookies")),
    "ping": lambda e, p: {"pong": True},
}
NO_PAGE_COUNT = {"screenshot", "input", "login_status", "ping", "login_open"}


def make_handler(engine, secret):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # keep request paths (secret) out of logs
            pass

        def _reply(self, code, payload):
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            parts = self.path.strip("/").split("/")
            if len(parts) != 2 or parts[0] != secret:
                return self._reply(404, {"ok": False})
            action = parts[1]
            length = int(self.headers.get("Content-Length") or 0)
            try:
                params = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                params = {}
            if action == "shutdown":
                self._reply(200, {"ok": True})
                raise KeyboardInterrupt
            fn = ACTIONS.get(action)
            if not fn:
                return self._reply(404, {"ok": False, "code": "unknown_action"})
            engine.pages = 0
            try:
                data = fn(engine, params)
                ok = data.get("result") not in ("failed",) if isinstance(data, dict) else True
                self._reply(200, {"ok": ok, "data": data, "error": data.get("note") if not ok else None,
                                  "pages": 0 if action in NO_PAGE_COUNT else engine.pages})
            except EngineError as e:
                self._reply(200, {"ok": False, "code": e.code, "error": str(e), "pages": engine.pages})
            except PWTimeout as e:
                self._reply(200, {"ok": False, "code": "timeout", "error": str(e)[:300], "pages": engine.pages})
            except KeyError as e:
                self._reply(200, {"ok": False, "code": "bad_request", "error": "missing %s" % e})
            except Exception as e:  # noqa: BLE001
                self._reply(200, {"ok": False, "code": "error", "error": "%s: %s" % (type(e).__name__, e),
                                  "trace": traceback.format_exc()[-1500:], "pages": engine.pages})

        def do_GET(self):
            parts = self.path.strip("/").split("/")
            if len(parts) == 2 and parts[0] == secret and parts[1] == "health":
                return self._reply(200, {"ok": True, "pid": os.getpid()})
            return self._reply(404, {"ok": False})

    return Handler


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--secret", required=True)
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--headful", action="store_true")
    args = ap.parse_args()
    os.makedirs(args.data_dir, mode=0o700, exist_ok=True)
    os.chmod(args.data_dir, 0o700)
    engine = LinkedInEngine(args.data_dir, headless=not args.headful)
    server = HTTPServer(("127.0.0.1", args.port), make_handler(engine, args.secret))
    signal.signal(signal.SIGTERM, lambda *a: (_ for _ in ()).throw(KeyboardInterrupt()))
    print("ENGINE_READY", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        engine.close()


if __name__ == "__main__":
    sys.exit(main())
