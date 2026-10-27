# -*- coding: utf-8 -*-
# Copyright 2026 Nextbridge. License OPL-1.
"""Website builder tools: standard mode (Odoo blocks, texts, images, styles, pages, menus, theme)
and expert mode (raw HTML, custom CSS/code, external page preview and import)."""
import base64
import ipaddress
import re
import socket
from urllib.parse import urljoin, urlparse

from lxml import etree, html as lhtml

from ..core.context import ToolError
from ..core.registry import S_CONFIRM, s_arr, s_bool, s_int, s_str, tool
from . import site_common as sc

S_WEBSITE = s_int("Website id (only needed when there are several websites).")
S_PAGE = s_str("Page address (e.g. /about-us) or page id.")
S_SECTION = s_int("Section number from the page structure (1 = first).", None, 1)
S_ELEMENT = s_str("Element path from the page structure, e.g. /div/div/div[1]/h1.")

BACKGROUNDS = {"default": "o_cc1", "light": "o_cc2", "accent": "o_cc3", "primary": "o_cc4", "dark": "o_cc5"}
SPACING = {"none": 0, "small": 24, "medium": 48, "large": 96, "xl": 152}
FONTS = ["Roboto", "Open Sans", "Source Sans Pro", "Raleway", "Noto Serif", "Arvo", "Inter Tight", "Inter"]
MAX_FETCH = 3 * 1024 * 1024


# ---------------------------------------------------------------------------- helpers
def _ctx_page(ctx, page, website_id, flag="allow_edit"):
    w = sc.website(ctx, website_id)
    perm = sc.permission(ctx, w, flag)
    p = sc.find_page(ctx, w, page)
    sc.check_page(ctx, perm, p)
    p = sc.own_page(ctx, w, p)
    return w, perm, p


def _snippet_catalog(env):
    view = env.ref("website.snippets", raise_if_not_found=False)
    if not view:
        return []
    root = etree.fromstring(view.sudo().get_combined_arch() if hasattr(view, "get_combined_arch") else view.arch)
    out, seen = [], set()
    for t in root.iter("t"):
        key = t.get("t-snippet")
        if not key or key.endswith("s_snippet_group") or key in seen:
            continue
        seen.add(key)
        out.append({"key": key.split(".", 1)[1] if "." in key else key, "module_key": key,
                    "name": t.get("string") or key, "group": t.get("group") or ""})
    return out


def _render_snippet(env, w, key):
    full = key if "." in key else "website.%s" % key
    tmpl = env.ref(full, raise_if_not_found=False)
    if not tmpl:
        raise ToolError("Unknown building block %r. Use site_blocks to list them." % key)
    try:
        markup = env["ir.qweb"].with_context(website_id=w.id)._render(full, {"website": w})
        markup = str(markup)
        nodes = [n for n in lhtml.fragments_fromstring(markup) if not isinstance(n, str)]
        if not nodes:
            raise ValueError("empty")
        return [etree.fromstring(lhtml.tostring(n, method="xml", encoding="unicode"),
                                 parser=etree.XMLParser(recover=True)) for n in nodes]
    except Exception:  # noqa: BLE001 — fall back to the raw template body
        root = etree.fromstring(tmpl.sudo().arch)
        return [c for c in root if isinstance(c.tag, str)]


def restore_snapshot(ctx, undo_id):
    snap = ctx.env["nxb.mcp.site.snapshot"].sudo().browse(int(undo_id)).exists()
    if not snap:
        raise ToolError("Undo step %s does not exist." % undo_id)
    view = ctx.env["ir.ui.view"].sudo().browse(snap.view_id_int).exists()
    if not view:
        raise ToolError("The page of this undo step no longer exists.")
    w = ctx.env["website"].sudo().browse(snap.website_id_int).exists()
    if w:
        sc.permission(ctx, w, "allow_edit")
    # keep a snapshot of the current state so the undo can itself be undone
    redo = ctx.env["nxb.mcp.site.snapshot"].sudo().create({
        "website_id_int": snap.website_id_int, "view_id_int": view.id, "page_url": snap.page_url,
        "label": "Before undo of #%d" % snap.id, "arch": view.arch_db})
    view.write({"arch": snap.arch})
    snap.restored = True
    return {"restored": snap.page_url, "to_before": snap.label, "redo_id": redo.id}


def _public_url_only(env, url):
    p = urlparse(url or "")
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ToolError("Only http(s) addresses can be fetched.")
    if env["ir.config_parameter"].sudo().get_param("nxb_mcp_hub.allow_private_fetch") == "True":
        return
    try:
        infos = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80))
    except socket.gaierror:
        raise ToolError("The address %s cannot be resolved." % p.hostname)
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ToolError("For security, addresses on the server's own network cannot be fetched.")


def _fetch(env, url, accept="text/html"):
    import requests
    _public_url_only(env, url)
    try:
        r = requests.get(url, timeout=15, stream=True, allow_redirects=True,
                         headers={"User-Agent": "Mozilla/5.0 (compatible; OdooMcpHub/1.0)", "Accept": accept})
        r.raise_for_status()
        _public_url_only(env, r.url)
        data = b""
        for chunk in r.iter_content(65536):
            data += chunk
            if len(data) > MAX_FETCH:
                raise ToolError("The page is larger than 3 MB.")
        return data, r.headers.get("Content-Type", ""), r.url
    except ToolError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ToolError("Could not fetch %s: %s" % (url, e))


def _download_image(url, env=None):
    import requests
    p = urlparse(url or "")
    if p.scheme not in ("http", "https"):
        raise ToolError("Image addresses must be http(s).")
    if env is not None:
        _public_url_only(env, url)
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0 (compatible; OdooMcpHub/1.0)"})
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        raise ToolError("Could not download the image: %s" % e)
    mime = r.headers.get("Content-Type", "").split(";")[0]
    if not mime.startswith("image/"):
        raise ToolError("That address is not an image (%s)." % (mime or "unknown type"))
    if len(r.content) > 10 * 1024 * 1024:
        raise ToolError("Images must be smaller than 10 MB.")
    return r.content, mime


def _store_image(env, data, name, mime):
    att = env["ir.attachment"].sudo().create({"name": name, "datas": base64.b64encode(data), "mimetype": mime,
                                              "public": True, "res_model": "ir.ui.view"})
    return att, "/web/image/%d/%s" % (att.id, re.sub(r"[^\w.\-]", "_", name))


# ---------------------------------------------------------------------------- standard: read
@tool("site_guide", "designer", "How to edit the website",
      "Explain how the website builder tools work, the permission model and good practice. Read it before your "
      "first website change.")
def site_guide(ctx):
    return {
        "workflow": ["site_overview: websites, what you are allowed to do, menus",
                     "site_pages: list pages; site_page_structure: sections and element paths of one page",
                     "site_blocks: Odoo building blocks you can add",
                     "change with site_block_add / site_text_set / site_image_set / site_section_style ...",
                     "every content change returns an undo_id; site_undo restores the previous version"],
        "addressing": "Sections are numbered from 1 in page order. Elements inside a section are addressed by the "
                      "path shown in site_page_structure. Re-read the structure after adding/moving sections.",
        "styles": {"backgrounds": list(BACKGROUNDS), "spacing": list(SPACING), "fonts": FONTS},
        "permissions": "An administrator allows each website and its pages in AI Connector → Website permissions: "
                       "editing, creating and deleting pages and site-wide changes are separate switches.",
        "good_practice": ["Keep using Odoo blocks so people can keep editing pages in the website editor.",
                          "Publish new pages only when the user asks; they are created unpublished.",
                          "Tell the user what changed and give the page address."],
    }


@tool("site_overview", "designer", "Website overview",
      "List the websites with their domain, languages, number of pages, menu, theme and exactly what the assistant "
      "is allowed to change on each.")
def site_overview(ctx):
    env = ctx.env
    if "website" not in env:
        raise ToolError("The Website app is not installed in this database.")
    out = []
    Perm = env["nxb.mcp.site.permission"].sudo()
    theme = env["ir.module.module"].sudo().search([("name", "=like", "theme_%"), ("state", "=", "installed")],
                                                  limit=3).mapped("shortdesc")
    for w in env["website"].sudo().search([]):
        perm = Perm.search([("website_key", "=", str(w.id)), ("active", "=", True)], limit=1)
        out.append({"website_id": w.id, "name": w.name, "domain": w.domain or "", "homepage": w.homepage_url or "/",
                    "languages": w.language_ids.mapped("code"),
                    "pages": env["website.page"].sudo().search_count([("website_id", "in", (w.id, False))]),
                    "menu": [{"name": m.name, "url": m.url, "children": [c.name for c in m.child_id]}
                             for m in w.menu_id.child_id],
                    "assistant_may": perm._describe() if perm else "nothing yet (no permission set)"})
    return {"websites": out, "themes_installed": theme}


@tool("site_pages", "designer", "List website pages",
      "List the pages of a website with address, title, published and indexed status.",
      properties={"website_id": S_WEBSITE, "search": s_str("Address or name contains.")})
def site_pages(ctx, website_id=None, search=None):
    w = sc.website(ctx, website_id)
    dom = [("website_id", "in", (w.id, False))]
    if search:
        dom += ["|", ("url", "ilike", search), ("name", "ilike", search)]
    pages = ctx.env["website.page"].sudo().with_context(website_id=w.id).search(dom, order="url")
    by_url = {}
    for p in pages.sorted(lambda p: 0 if p.website_id else 1):
        by_url.setdefault(p.url, p)
    perm = ctx.env["nxb.mcp.site.permission"].sudo().search([("website_key", "=", str(w.id))], limit=1)
    return [{"page_id": p.id, "url": p.url, "name": p.name, "published": p.is_published, "indexed": p.website_indexed,
             "in_menu": p.is_in_menu, "editable_by_assistant": bool(perm and perm._page_allowed(p.url))}
            for p in by_url.values()]


@tool("site_page_structure", "designer", "Show a page's structure",
      "Show the sections of a page in order, with their block type, texts, buttons, links and images, and the "
      "element paths used by the editing tools.",
      properties={"page": S_PAGE, "website_id": S_WEBSITE, "compact": s_bool("Only one line per section.", False)},
      required=("page",))
def site_page_structure(ctx, page, website_id=None, compact=False):
    w = sc.website(ctx, website_id)
    p = sc.find_page(ctx, w, page)
    root = sc.read_arch(p.view_id)
    wrap = sc.wrap_of(root)
    return {"page": p.url, "name": p.name, "published": p.is_published, "sections": sc.outline(wrap, not compact)}


@tool("site_blocks", "designer", "List building blocks",
      "List Odoo's website building blocks (banners, text-image, columns, pricing, team, FAQ, call to action, ...) "
      "that can be added to pages.",
      properties={"group": s_str("Only one group, e.g. intro, columns, content, images, people, text, contact.")})
def site_blocks(ctx, group=None):
    blocks = _snippet_catalog(ctx.env)
    if group:
        blocks = [b for b in blocks if b["group"] == group]
    groups = sorted({b["group"] for b in _snippet_catalog(ctx.env)})
    return {"groups": groups, "blocks": [{"key": b["key"], "name": b["name"], "group": b["group"]} for b in blocks]}


@tool("site_history", "designer", "Page change history",
      "List the recent changes made by the assistant on a page, with the undo ids.",
      properties={"page": S_PAGE, "website_id": S_WEBSITE}, required=("page",))
def site_history(ctx, page, website_id=None):
    w = sc.website(ctx, website_id)
    p = sc.find_page(ctx, w, page)
    snaps = ctx.env["nxb.mcp.site.snapshot"].sudo().search([("view_id_int", "=", p.view_id.id)], limit=30)
    return [{"undo_id": s.id, "change": s.label, "when": str(s.create_date), "by": s.user_id.name,
             "already_restored": s.restored} for s in snaps]


# ---------------------------------------------------------------------------- standard: pages
@tool("site_page_create", "designer", "Create a page",
      "Create a new website page (unpublished unless asked), optionally with Odoo building blocks and a menu entry.",
      properties={"name": s_str("Page name, e.g. 'Our story'."), "website_id": S_WEBSITE,
                  "blocks": s_arr("Building block keys to add, in order (see site_blocks).", {"type": "string"}),
                  "add_to_menu": s_bool("Add it to the main menu.", False),
                  "publish": s_bool("Publish immediately.", False)},
      required=("name",), writes=True)
def site_page_create(ctx, name, website_id=None, blocks=None, add_to_menu=False, publish=False):
    w = sc.website(ctx, website_id)
    perm = sc.permission(ctx, w, "allow_create")
    if add_to_menu and not perm.allow_sitewide:
        raise ToolError("Adding to the menu is a site-wide change, which is not allowed. Create the page without "
                        "add_to_menu.")
    res = w.with_context(website_id=w.id).new_page(name=name, add_menu=bool(add_to_menu),
                                                   template="website.default_page")
    page = ctx.env["website.page"].sudo().browse(res["page_id"])
    page.write({"is_published": bool(publish), "website_id": w.id})
    perm._add_url(page.url)
    added = []
    if blocks:
        root = sc.read_arch(page.view_id)
        wrap = sc.wrap_of(root)
        for key in blocks:
            for node in _render_snippet(ctx.env, w, key):
                wrap.append(node)
            added.append(key)
        page.view_id.write({"arch": etree.tostring(root, encoding="unicode")})
    base = (w.domain or ctx.env["ir.config_parameter"].sudo().get_param("web.base.url", "")).rstrip("/")
    return {"page_id": page.id, "url": page.url, "published": page.is_published, "blocks_added": added,
            "open": base + page.url if base.startswith("http") else page.url}


@tool("site_page_update", "designer", "Rename, publish or hide a page",
      "Change a page's name, publication, header/footer visibility, or add it to / remove it from the menu.",
      properties={"page": S_PAGE, "website_id": S_WEBSITE, "name": s_str("New name."),
                  "publish": s_bool("true = published, false = unpublished."),
                  "show_header": s_bool("Show the website header."), "show_footer": s_bool("Show the footer."),
                  "in_menu": s_bool("true = add a menu entry, false = remove its menu entries.")},
      required=("page",), writes=True)
def site_page_update(ctx, page, website_id=None, name=None, publish=None, show_header=None, show_footer=None,
                     in_menu=None):
    w, perm, p = _ctx_page(ctx, page, website_id)
    vals = {}
    if name:
        vals["name"] = name
    if publish is not None:
        vals["is_published"] = bool(publish)
    if show_header is not None:
        vals["header_visible"] = bool(show_header)
    if show_footer is not None:
        vals["footer_visible"] = bool(show_footer)
    if vals:
        p.write(vals)
    if in_menu is not None:
        if not perm.allow_sitewide:
            raise ToolError("Menu changes are site-wide changes, which are not allowed.")
        if in_menu and not p.menu_ids:
            ctx.env["website.menu"].sudo().create({"name": p.name, "url": p.url, "page_id": p.id,
                                                   "parent_id": w.menu_id.id, "website_id": w.id})
        elif not in_menu:
            p.menu_ids.unlink()
    if not vals and in_menu is None:
        raise ToolError("Nothing to change.")
    return {"page": p.url, "name": p.name, "published": p.is_published, "in_menu": bool(p.menu_ids)}


@tool("site_page_delete", "designer", "Delete a page",
      "Delete a website page permanently (cannot be undone). Tell the user first, then call with confirm=true.",
      properties={"page": S_PAGE, "website_id": S_WEBSITE, "confirm": S_CONFIRM,
                  "redirect_to": s_str("Optional address to redirect the deleted page to (301).")},
      required=("page", "confirm"), writes=True, destructive=True)
def site_page_delete(ctx, page, confirm=False, website_id=None, redirect_to=None):
    w = sc.website(ctx, website_id)
    perm = sc.permission(ctx, w, "allow_delete")
    p = sc.find_page(ctx, w, page)
    sc.check_page(ctx, perm, p)
    if p.url == "/" or p.view_id.key == "website.homepage":
        raise ToolError("The homepage cannot be deleted.")
    url = p.url
    if not p.website_id:
        raise ToolError("This page is shared by all websites; delete it in the website editor.")
    p.menu_ids.unlink()
    p.unlink()
    if redirect_to:
        ctx.env["website.rewrite"].sudo().create({"name": "AI: deleted %s" % url, "website_id": w.id,
                                                  "url_from": url, "url_to": redirect_to, "redirect_type": "301"})
    return {"deleted": url, "redirect_to": redirect_to or None, "undo": "not possible"}


# ---------------------------------------------------------------------------- standard: blocks & content
@tool("site_block_add", "designer", "Add a building block",
      "Add an Odoo building block (see site_blocks) to a page, at the end or after a given section.",
      properties={"page": S_PAGE, "website_id": S_WEBSITE, "block": s_str("Block key, e.g. s_text_image."),
                  "after_section": s_int("Insert after this section number; 0 = at the top; omit = at the end.")},
      required=("page", "block"), writes=True)
def site_block_add(ctx, page, block, website_id=None, after_section=None):
    w, perm, p = _ctx_page(ctx, page, website_id)
    root = sc.read_arch(p.view_id)
    wrap = sc.wrap_of(root)
    nodes = _render_snippet(ctx.env, w, block)
    secs = sc.sections(wrap)
    if after_section is None or after_section >= len(secs):
        for n in nodes:
            wrap.append(n)
        position = len(secs) + 1
    else:
        anchor_index = 0 if after_section <= 0 else wrap.index(secs[after_section - 1]) + 1
        for i, n in enumerate(nodes):
            wrap.insert(anchor_index + i, n)
        position = max(after_section, 0) + 1
    snap = sc.save_arch(ctx, w, p, root, "Add block %s" % block)
    return {"page": p.url, "added": block, "section": position, "undo_id": snap.id,
            "next": "Read site_page_structure to edit its texts and images."}


def _section_edit(ctx, page, website_id, label, fn):
    w, perm, p = _ctx_page(ctx, page, website_id)
    root = sc.read_arch(p.view_id)
    wrap = sc.wrap_of(root)
    result = fn(wrap)
    snap = sc.save_arch(ctx, w, p, root, label)
    result.update({"page": p.url, "undo_id": snap.id})
    return result


@tool("site_block_move", "designer", "Move a section",
      "Move a section to another position on the page.",
      properties={"page": S_PAGE, "website_id": S_WEBSITE, "section": S_SECTION,
                  "to_position": s_int("New position (1 = top).", None, 1)},
      required=("page", "section", "to_position"), writes=True)
def site_block_move(ctx, page, section, to_position, website_id=None):
    def fn(wrap):
        sec = sc.get_section(wrap, section)
        wrap.remove(sec)
        secs = sc.sections(wrap)
        if to_position > len(secs):
            wrap.append(sec)
        else:
            wrap.insert(wrap.index(secs[to_position - 1]), sec)
        return {"moved": section, "now_at": min(to_position, len(secs) + 1)}
    return _section_edit(ctx, page, website_id, "Move section %s" % section, fn)


@tool("site_block_remove", "designer", "Remove a section",
      "Remove a section from a page (can be undone with site_undo).",
      properties={"page": S_PAGE, "website_id": S_WEBSITE, "section": S_SECTION},
      required=("page", "section"), writes=True)
def site_block_remove(ctx, page, section, website_id=None):
    def fn(wrap):
        sec = sc.get_section(wrap, section)
        summary = sc.text_of(sec, 80)
        wrap.remove(sec)
        return {"removed_section": section, "it_contained": summary}
    return _section_edit(ctx, page, website_id, "Remove section %s" % section, fn)


@tool("site_block_duplicate", "designer", "Duplicate a section",
      "Copy a section and insert the copy right after it.",
      properties={"page": S_PAGE, "website_id": S_WEBSITE, "section": S_SECTION},
      required=("page", "section"), writes=True)
def site_block_duplicate(ctx, page, section, website_id=None):
    import copy

    def fn(wrap):
        sec = sc.get_section(wrap, section)
        wrap.insert(wrap.index(sec) + 1, copy.deepcopy(sec))
        return {"duplicated": section, "copy_is_section": section + 1}
    return _section_edit(ctx, page, website_id, "Duplicate section %s" % section, fn)


@tool("site_text_set", "designer", "Change a text",
      "Replace the text of a heading, paragraph, button or other text element. Use blank lines in the text to "
      "create several paragraphs when the element is a paragraph.",
      properties={"page": S_PAGE, "website_id": S_WEBSITE, "section": S_SECTION, "element": S_ELEMENT,
                  "text": s_str("New text.")},
      required=("page", "section", "element", "text"), writes=True)
def site_text_set(ctx, page, section, element, text, website_id=None):
    def fn(wrap):
        el = sc.get_element(sc.get_section(wrap, section), element)
        if el.tag in ("img", "section"):
            raise ToolError("Element %s is not a text element." % element)
        old = sc.text_of(el, 80)
        paras = [t.strip() for t in text.split("\n\n") if t.strip()]
        for child in list(el):
            el.remove(child)
        el.text = paras[0] if paras else ""
        if el.tag == "p" and len(paras) > 1:
            parent = el.getparent()
            idx = parent.index(el)
            for i, extra in enumerate(paras[1:], start=1):
                np = etree.Element("p")
                np.text = extra
                parent.insert(idx + i, np)
        return {"element": element, "was": old, "now": sc.text_of(el, 80)}
    return _section_edit(ctx, page, website_id, "Text in section %s" % section, fn)


@tool("site_link_set", "designer", "Change a button or link",
      "Change where a button or link points to, and optionally its label.",
      properties={"page": S_PAGE, "website_id": S_WEBSITE, "section": S_SECTION, "element": S_ELEMENT,
                  "url": s_str("Destination, e.g. /contactus or https://..."), "label": s_str("New label.")},
      required=("page", "section", "element", "url"), writes=True)
def site_link_set(ctx, page, section, element, url, website_id=None, label=None):
    if url.strip().lower().startswith("javascript:"):
        raise ToolError("javascript: links are not allowed.")

    def fn(wrap):
        el = sc.get_element(sc.get_section(wrap, section), element)
        if el.tag != "a":
            links = el.xpath(".//a")
            if not links:
                raise ToolError("Element %s is not a link or button." % element)
            el = links[0]
        el.set("href", url)
        if label:
            for child in list(el):
                el.remove(child)
            el.text = label
        return {"link": sc.text_of(el, 60), "href": url}
    return _section_edit(ctx, page, website_id, "Link in section %s" % section, fn)


@tool("site_image_set", "designer", "Replace an image",
      "Replace an image on a page with an image from an address (downloaded into Odoo) or one already uploaded "
      "(/web/image/...), and set its alt text.",
      properties={"page": S_PAGE, "website_id": S_WEBSITE, "section": S_SECTION, "element": S_ELEMENT,
                  "image_url": s_str("https:// image address, or /web/image/... from site_image_upload."),
                  "alt": s_str("Alt text describing the image.")},
      required=("page", "section", "element", "image_url"), writes=True)
def site_image_set(ctx, page, section, element, image_url, website_id=None, alt=None):
    src = image_url
    if image_url.startswith("http"):
        data, mime = _download_image(image_url, ctx.env)
        _att, src = _store_image(ctx.env, data, image_url.rsplit("/", 1)[-1].split("?")[0] or "image", mime)
    elif not image_url.startswith("/"):
        raise ToolError("Give an https:// address or a /web/image/... address.")

    def fn(wrap):
        el = sc.get_element(sc.get_section(wrap, section), element)
        if el.tag != "img":
            imgs = el.xpath(".//img")
            if not imgs:
                raise ToolError("Element %s is not an image." % element)
            el = imgs[0]
        el.set("src", src)
        for attr in ("data-original-id", "data-original-src", "data-mimetype", "data-resize-width",
                     "data-shape", "data-file-name", "data-original-mimetype"):
            el.attrib.pop(attr, None)
        if alt is not None:
            el.set("alt", alt)
        return {"image": src, "alt": el.get("alt") or ""}
    return _section_edit(ctx, page, website_id, "Image in section %s" % section, fn)


@tool("site_image_upload", "designer", "Upload an image",
      "Download an image from an address into Odoo's media library and return its local address, ready for "
      "site_image_set or HTML.",
      properties={"image_url": s_str("https:// image address."), "name": s_str("File name.")},
      required=("image_url",), writes=True)
def site_image_upload(ctx, image_url, name=None):
    data, mime = _download_image(image_url, ctx.env)
    att, src = _store_image(ctx.env, data, name or image_url.rsplit("/", 1)[-1].split("?")[0] or "image", mime)
    return {"attachment_id": att.id, "src": src, "size_kb": round(len(data) / 1024)}


@tool("site_section_style", "designer", "Style a section",
      "Change a section's look with the theme's own styles: background colour scheme, vertical spacing, text "
      "alignment, and extra CSS classes.",
      properties={"page": S_PAGE, "website_id": S_WEBSITE, "section": S_SECTION,
                  "background": s_str("Colour scheme from the theme.", list(BACKGROUNDS)),
                  "spacing": s_str("Space above and below.", list(SPACING)),
                  "align": s_str("Text alignment.", ["left", "center", "right"]),
                  "add_classes": s_arr("Extra classes to add.", {"type": "string"}),
                  "remove_classes": s_arr("Classes to remove.", {"type": "string"})},
      required=("page", "section"), writes=True)
def site_section_style(ctx, page, section, website_id=None, background=None, spacing=None, align=None,
                       add_classes=None, remove_classes=None):
    def fn(wrap):
        sec = sc.get_section(wrap, section)
        classes = (sec.get("class") or "").split()
        if background:
            classes = [c for c in classes if not re.match(r"^o_cc\d?$", c)]
            classes += ["o_cc", BACKGROUNDS[background]]
        if spacing:
            classes = [c for c in classes if not re.match(r"^p[tb]\d+$", c)]
            px = SPACING[spacing]
            classes += ["pt%d" % px, "pb%d" % px]
        if align:
            classes = [c for c in classes if c not in ("text-start", "text-center", "text-end")]
            classes.append({"left": "text-start", "center": "text-center", "right": "text-end"}[align])
        for c in add_classes or []:
            if re.match(r"^[\w\-]+$", c) and c not in classes:
                classes.append(c)
        classes = [c for c in classes if c not in (remove_classes or [])]
        sec.set("class", " ".join(dict.fromkeys(classes)))
        return {"section": section, "classes": sec.get("class")}
    return _section_edit(ctx, page, website_id, "Style section %s" % section, fn)


@tool("site_undo", "designer", "Undo a change",
      "Restore a page to the version before one of the assistant's changes (use the undo_id returned by the "
      "change, or see site_history). The undo itself can be undone with the returned redo_id.",
      properties={"undo_id": s_int("Undo id.")}, required=("undo_id",), writes=True)
def site_undo(ctx, undo_id):
    return restore_snapshot(ctx, undo_id)


# ---------------------------------------------------------------------------- standard: site-wide
@tool("site_menu", "designer", "Show the website menu",
      "Show the website's menu tree with ids, labels and addresses.", properties={"website_id": S_WEBSITE})
def site_menu(ctx, website_id=None):
    w = sc.website(ctx, website_id)

    def node(m):
        return {"menu_id": m.id, "label": m.name, "url": m.url, "new_window": m.new_window,
                "children": [node(c) for c in m.child_id.sorted("sequence")]}
    return {"website": w.name, "menu": [node(m) for m in w.menu_id.child_id.sorted("sequence")]}


@tool("site_menu_change", "designer", "Change the website menu",
      "Add, rename, re-point, move or remove a website menu entry (site-wide permission needed).",
      properties={"website_id": S_WEBSITE,
                  "action": s_str("What to do.", ["add", "rename", "relink", "move", "remove"]),
                  "menu_id": s_int("Menu entry to change (not for add)."), "label": s_str("Label (add/rename)."),
                  "url": s_str("Address (add/relink)."),
                  "parent_menu_id": s_int("Parent entry for add/move (omit = top level)."),
                  "position": s_int("1-based position among its siblings."),
                  "new_window": s_bool("Open in a new tab.")},
      required=("action",), writes=True)
def site_menu_change(ctx, action, website_id=None, menu_id=None, label=None, url=None, parent_menu_id=None,
                     position=None, new_window=None):
    w = sc.website(ctx, website_id)
    sc.permission(ctx, w, "allow_sitewide")
    Menu = ctx.env["website.menu"].sudo()
    parent = Menu.browse(parent_menu_id).exists() if parent_menu_id else w.menu_id
    if parent_menu_id and (not parent or parent.website_id != w):
        raise ToolError("Parent menu %s is not part of this website." % parent_menu_id)
    if action == "add":
        if not (label and url):
            raise ToolError("Give a label and an address.")
        m = Menu.create({"name": label, "url": url, "parent_id": parent.id, "website_id": w.id,
                         "new_window": bool(new_window)})
    else:
        m = Menu.browse(menu_id or 0).exists()
        if not m or m.website_id != w or m == w.menu_id:
            raise ToolError("Menu entry %s does not exist on this website." % menu_id)
        if action == "rename":
            m.name = label or m.name
        elif action == "relink":
            m.write({"url": url or m.url, **({"new_window": bool(new_window)} if new_window is not None else {})})
        elif action == "move":
            m.parent_id = parent
        elif action == "remove":
            name = m.name
            m.unlink()
            return {"removed": name}
    if position:
        siblings = m.parent_id.child_id.sorted("sequence") - m
        siblings = list(siblings)
        siblings.insert(max(position - 1, 0), m)
        for i, s in enumerate(siblings):
            s.sequence = (i + 1) * 10
    return {"menu_id": m.id, "label": m.name, "url": m.url, "parent": m.parent_id.name}


@tool("site_theme", "designer", "Change colours and fonts",
      "Change the website theme: a preset colour palette, the five main colours, and the body/heading/button "
      "fonts (site-wide permission needed; cannot be undone automatically — the result reports the previous "
      "values).",
      properties={"website_id": S_WEBSITE,
                  "palette": s_str("Preset palette name like 'default-3' (1-30)."),
                  "colors": s_arr("Up to five colours in order: primary, secondary, light, white, dark, as #RRGGBB.",
                                  {"type": "string"}),
                  "body_font": s_str("Body font.", FONTS), "heading_font": s_str("Heading font.", FONTS),
                  "button_font": s_str("Button font.", FONTS)},
      writes=True)
def site_theme(ctx, website_id=None, palette=None, colors=None, body_font=None, heading_font=None, button_font=None):
    w = sc.website(ctx, website_id)
    sc.permission(ctx, w, "allow_sitewide")
    Assets = ctx.env["web_editor.assets"].sudo().with_context(website_id=w.id)
    changed = {}
    if palette:
        if not re.match(r"^default-\d{1,2}$", palette):
            raise ToolError("Palette names look like default-1 … default-30.")
        Assets.make_scss_customization("/website/static/src/scss/options/user_values.scss",
                                       {"color-palettes-name": "'%s'" % palette})
        changed["palette"] = palette
    if colors:
        vals = {}
        for i, c in enumerate(colors[:5], start=1):
            if not re.match(r"^#[0-9a-fA-F]{6}$", c or ""):
                raise ToolError("Colours must look like #1A2B3C (got %r)." % c)
            vals["o-color-%d" % i] = c
        Assets.make_scss_customization("/website/static/src/scss/options/colors/user_color_palette.scss", vals)
        changed["colors"] = colors[:5]
    fonts = {}
    for key, val in (("font", body_font), ("headings-font", heading_font), ("buttons-font", button_font)):
        if val:
            if val not in FONTS:
                raise ToolError("Font %r is not available. Choose: %s." % (val, ", ".join(FONTS)))
            fonts[key] = "'%s'" % val
    if fonts:
        Assets.make_scss_customization("/website/static/src/scss/options/user_values.scss", fonts)
        changed["fonts"] = {k: v.strip("'") for k, v in fonts.items()}
    if not changed:
        raise ToolError("Nothing to change.")
    return {"website": w.name, "changed": changed,
            "note": "Visitors see the new style after the website assets are rebuilt (automatic on next visit)."}


# ---------------------------------------------------------------------------- expert mode
@tool("site_section_html", "designer_pro", "Write a section in HTML",
      "Replace a section with your own HTML, or insert a new HTML section. Scripts and event handlers are removed. "
      "Such sections display correctly but have fewer options in Odoo's website editor — tell the user so.",
      properties={"page": S_PAGE, "website_id": S_WEBSITE,
                  "html": s_str("HTML of the section, ideally <section class=\"pt48 pb48\"><div class=\"container\">"
                                "…</div></section>."),
                  "replace_section": s_int("Section to replace; omit to insert."),
                  "after_section": s_int("When inserting: after this section (0 = top; omit = end).")},
      required=("page", "html"), writes=True)
def site_section_html(ctx, page, html, website_id=None, replace_section=None, after_section=None):
    nodes = sc.sanitize_fragment(html)
    if not nodes:
        raise ToolError("The HTML is empty after cleaning.")
    wrapped = []
    for n in nodes:
        if n.tag != "section":
            sec = etree.Element("section", {"class": "pt48 pb48", "data-name": "Custom HTML"})
            cont = etree.SubElement(sec, "div", {"class": "container"})
            cont.append(n)
            n = sec
        wrapped.append(n)

    def fn(wrap):
        secs = sc.sections(wrap)
        if replace_section:
            old = sc.get_section(wrap, replace_section)
            idx = wrap.index(old)
            wrap.remove(old)
            for i, n in enumerate(wrapped):
                wrap.insert(idx + i, n)
            return {"replaced_section": replace_section, "sections_written": len(wrapped),
                    "note": "Custom HTML has fewer options in the website editor."}
        if after_section is None or after_section >= len(secs):
            for n in wrapped:
                wrap.append(n)
        else:
            idx = 0 if after_section <= 0 else wrap.index(secs[after_section - 1]) + 1
            for i, n in enumerate(wrapped):
                wrap.insert(idx + i, n)
        return {"inserted_sections": len(wrapped), "note": "Custom HTML has fewer options in the website editor."}
    return _section_edit(ctx, page, website_id, "Custom HTML", fn)


def _custom_css(env, w):
    Assets = env["web_editor.assets"].sudo().with_context(website_id=w.id)
    url = "/website/static/src/scss/user_custom_rules.scss"
    custom = Assets._make_custom_asset_url(url, "web.assets_frontend")
    att = Assets._get_custom_attachment(custom)
    current = base64.b64decode(att.datas).decode() if att else ""
    return Assets, url, current


@tool("site_custom_css", "designer_pro", "Custom CSS",
      "Read the website's custom CSS, append rules to it or replace it (site-wide permission needed). The previous "
      "CSS is returned so it can be restored.",
      properties={"website_id": S_WEBSITE, "mode": s_str("read, append or replace.", ["read", "append", "replace"]),
                  "css": s_str("CSS/SCSS rules.")},
      required=("mode",), writes=True)
def site_custom_css(ctx, mode, website_id=None, css=None):
    w = sc.website(ctx, website_id)
    Assets, url, current = _custom_css(ctx.env, w)
    if mode == "read":
        return {"website": w.name, "css": current or "(empty)"}
    sc.permission(ctx, w, "allow_sitewide")
    if not css:
        raise ToolError("Give the CSS.")
    if re.search(r"@import\s+url\(|expression\(|javascript:", css, re.I):
        raise ToolError("Remote imports, expressions and javascript: are not allowed in custom CSS.")
    new = (current.rstrip() + "\n\n" + css) if mode == "append" else css
    Assets.save_asset(url, "web.assets_frontend", new, "scss")
    return {"website": w.name, "mode": mode, "previous_css": current}


@tool("site_custom_code", "designer_pro", "Head / footer code",
      "Read or set code added to every page's <head> or end of <body> (e.g. analytics or chat widgets). Site-wide "
      "permission needed. Returns the previous code.",
      properties={"website_id": S_WEBSITE, "where": s_str("head or footer.", ["head", "footer"]),
                  "code": s_str("Code to set; omit to read.")},
      required=("where",), writes=True)
def site_custom_code(ctx, where, website_id=None, code=None):
    w = sc.website(ctx, website_id).sudo()
    field = "custom_code_head" if where == "head" else "custom_code_footer"
    previous = w[field] or ""
    if code is None:
        return {"website": w.name, where: str(previous) or "(empty)"}
    sc.permission(ctx, w, "allow_sitewide")
    w[field] = code
    return {"website": w.name, "where": where, "previous_code": str(previous)}


def _page_text(markup, base_url):
    doc = lhtml.fromstring(markup)
    for bad in doc.xpath("//script | //style | //noscript | //svg | //iframe | //form"):
        bad.getparent().remove(bad)
    title = (doc.findtext(".//title") or "").strip()
    main = (doc.xpath("//main") or doc.xpath("//article") or doc.xpath("//body") or [doc])[0]
    for bad in main.xpath(".//nav | .//header | .//footer | .//aside"):
        if bad.getparent() is not None:
            bad.getparent().remove(bad)
    for el in main.xpath(".//*[@src] | .//*[@href]"):
        for attr in ("src", "href"):
            if el.get(attr):
                el.set(attr, urljoin(base_url, el.get(attr)))
    return title, main


@tool("site_web_preview", "designer_pro", "Look at an external web page",
      "Fetch a public web page and summarise it: title, headings, main texts, images and links. Use it for "
      "inspiration or before copying a page.",
      properties={"url": s_str("https:// address.")}, required=("url",))
def site_web_preview(ctx, url):
    data, ctype, final = _fetch(ctx.env, url)
    if "html" not in ctype:
        raise ToolError("That address is not a web page (%s)." % ctype)
    title, main = _page_text(data, final)
    heads = [{"tag": h.tag, "text": " ".join(h.text_content().split())[:120]}
             for h in main.iter() if isinstance(h.tag, str) and h.tag in ("h1", "h2", "h3")][:40]
    paras = [" ".join(p.text_content().split())[:300] for p in main.iter("p") if p.text_content().strip()][:25]
    imgs = [{"src": i.get("src"), "alt": i.get("alt") or ""} for i in main.iter("img") if i.get("src")][:30]
    return {"url": final, "title": title, "headings": heads, "paragraphs": paras, "images": imgs,
            "words": len(main.text_content().split())}


@tool("site_web_import", "designer_pro", "Copy an external page",
      "Copy the main content of a public web page into a NEW unpublished page of this website, split into "
      "sections by heading. Only copy pages the user owns or has the rights to reuse — remind the user of this.",
      properties={"url": s_str("https:// address of the page to copy."), "name": s_str("Name of the new page."),
                  "website_id": S_WEBSITE,
                  "download_images": s_bool("Copy the images into Odoo too (max 20).", False)},
      required=("url", "name"), writes=True)
def site_web_import(ctx, url, name, website_id=None, download_images=False):
    w = sc.website(ctx, website_id)
    perm = sc.permission(ctx, w, "allow_create")
    data, ctype, final = _fetch(ctx.env, url)
    if "html" not in ctype:
        raise ToolError("That address is not a web page (%s)." % ctype)
    _title, main = _page_text(data, final)
    # group the content into sections, starting a new one at each h1/h2
    blocks, current = [], []
    flat = []
    for el in main.xpath(".//h1 | .//h2 | .//h3 | .//p | .//ul | .//ol | .//img | .//blockquote | .//table"):
        if any(a in flat for a in el.iterancestors()):
            continue
        flat.append(el)
    for el in flat:
        if el.tag in ("h1", "h2") and current:
            blocks.append(current)
            current = []
        current.append(el)
    if current:
        blocks.append(current)
    if not blocks:
        raise ToolError("No readable content was found on that page.")
    images_copied = 0
    sections_xml = []
    for i, block in enumerate(blocks[:40]):
        sec = etree.Element("section", {"class": "s_text_block pt48 pb48" + (" o_cc o_cc2" if i % 2 else ""),
                                        "data-snippet": "s_text_block", "data-name": "Text"})
        cont = etree.SubElement(sec, "div", {"class": "container s_allow_columns"})
        for el in block:
            if el.tag == "img":
                src = el.get("src") or ""
                if download_images and images_copied < 20 and src.startswith("http"):
                    try:
                        img_data, mime = _download_image(src, ctx.env)
                        _att, src = _store_image(ctx.env, img_data, src.rsplit("/", 1)[-1].split("?")[0] or "image",
                                                 mime)
                        images_copied += 1
                    except ToolError:
                        pass
                etree.SubElement(cont, "img", {"src": src, "alt": el.get("alt") or "",
                                                      "class": "img img-fluid mx-auto d-block",
                                                      "loading": "lazy"})
                continue
            for n in sc.sanitize_fragment(lhtml.tostring(el, encoding="unicode")):
                for attr in ("class", "style", "id"):
                    for sub in n.iter():
                        if isinstance(sub.tag, str):
                            sub.attrib.pop(attr, None)
                cont.append(n)
        sections_xml.append(sec)
    res = w.with_context(website_id=w.id).new_page(name=name, add_menu=False, template="website.default_page")
    page = ctx.env["website.page"].sudo().browse(res["page_id"])
    page.write({"is_published": False, "website_id": w.id})
    root = sc.read_arch(page.view_id)
    wrap = sc.wrap_of(root)
    for s in sections_xml:
        wrap.append(s)
    page.view_id.write({"arch": etree.tostring(root, encoding="unicode")})
    perm._add_url(page.url)
    return {"page_id": page.id, "url": page.url, "published": False, "sections": len(sections_xml),
            "images_copied": images_copied, "source": final,
            "reminder": "Only publish copied content you own or are allowed to reuse."}
