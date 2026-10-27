# -*- coding: utf-8 -*-
# Copyright 2026 Nextbridge. License OPL-1.
"""Website SEO tools (needs the Website app in the target database)."""
import re
from collections import Counter

from ..core.context import ToolError
from ..core.registry import s_arr, s_bool, s_int, s_obj, s_str, tool
from . import site_common as sc

S_WEBSITE = s_int("Website id (only needed when there are several websites).")
S_PAGE = s_str("Page address (e.g. /about-us) or page id.")


def _page_seo(page):
    view = page.view_id
    root = sc.read_arch(view)
    wrap = root.xpath("//div[@id='wrap']")
    body = wrap[0] if wrap else root
    heads = [{"tag": h.tag, "text": sc.text_of(h, 90)} for h in body.iter() if isinstance(h.tag, str)
             and h.tag in ("h1", "h2", "h3")]
    imgs = [{"src": (i.get("src") or "")[:160], "alt": i.get("alt") or ""} for i in body.iter("img")]
    words = len((body.xpath("string(.)") or "").split())
    return {
        "page_id": page.id, "url": page.url, "name": page.name, "published": page.is_published,
        "indexed": page.website_indexed, "meta_title": page.website_meta_title or "",
        "meta_description": page.website_meta_description or "", "meta_keywords": page.website_meta_keywords or "",
        "social_image": page.website_meta_og_img or "", "headings": heads[:40], "images": imgs[:60],
        "word_count": words,
    }


def _issues(info, dup_titles):
    issues = []
    title = info["meta_title"] or ""
    desc = info["meta_description"] or ""
    if not title:
        issues.append(("high", "No meta title (Google shows the page name instead)."))
    elif len(title) > 60:
        issues.append(("medium", "Meta title is %d characters; keep it under 60." % len(title)))
    elif len(title) < 20:
        issues.append(("low", "Meta title is short (%d characters)." % len(title)))
    if title and title in dup_titles:
        issues.append(("medium", "Same meta title as another page."))
    if not desc:
        issues.append(("high", "No meta description."))
    elif len(desc) > 160:
        issues.append(("medium", "Meta description is %d characters; keep it 70-160." % len(desc)))
    elif len(desc) < 70:
        issues.append(("low", "Meta description is short (%d characters)." % len(desc)))
    h1 = [h for h in info["headings"] if h["tag"] == "h1"]
    if not h1:
        issues.append(("medium", "No H1 heading."))
    elif len(h1) > 1:
        issues.append(("medium", "%d H1 headings; use exactly one." % len(h1)))
    missing_alt = [i for i in info["images"] if not i["alt"].strip()]
    if missing_alt:
        issues.append(("medium", "%d image(s) without alt text." % len(missing_alt)))
    if not info["indexed"]:
        issues.append(("info", "Hidden from search engines (not indexed)."))
    if info["word_count"] < 150:
        issues.append(("low", "Thin content (%d words)." % info["word_count"]))
    url = info["url"]
    if re.search(r"[A-Z_]|\.html?$", url) or len(url) > 75:
        issues.append(("low", "URL could be cleaner (lowercase words separated by hyphens, short)."))
    if not info["social_image"]:
        issues.append(("low", "No social sharing image."))
    return issues


SCORE = {"high": 20, "medium": 10, "low": 4, "info": 0}


@tool("seo_guide", "seo", "SEO guidance",
      "Rules of thumb for titles, descriptions, URLs, headings and alt texts, and how the SEO tools work.")
def seo_guide(ctx):
    return {
        "meta_title": "50-60 characters, main keyword first, brand at the end, unique per page.",
        "meta_description": "70-160 characters, a reason to click, matches the page content.",
        "url": "Lowercase words separated by hyphens; changing a URL creates a 301 redirect automatically.",
        "headings": "Exactly one H1 per page; H2/H3 for sections.",
        "alt_text": "Describe the image briefly; include a keyword only when natural.",
        "workflow": ["seo_audit to find issues", "seo_page to inspect one page", "fix with the seo_* tools",
                     "seo_sitemap_refresh after URL changes", "seo_undo restores a page's previous content"],
        "permissions": "Changes need the website to be allowed in AI Connector → Website permissions.",
    }


@tool("seo_audit", "seo", "Audit the website",
      "Check the website's pages for SEO problems (titles, descriptions, headings, alt texts, indexing, URLs, "
      "thin content) and rank pages by how much they need work.",
      properties={"website_id": S_WEBSITE, "include_unpublished": s_bool("Also audit unpublished pages.", False),
                  "limit": s_int("Maximum pages in the result.", 30, 1, 200)})
def seo_audit(ctx, website_id=None, include_unpublished=False, limit=30):
    w = sc.website(ctx, website_id)
    Page = ctx.env["website.page"].sudo().with_context(website_id=w.id)
    pages = Page.search([("website_id", "in", (w.id, False))])
    # keep website-specific versions over shared ones with the same URL
    by_url = {}
    for p in pages.sorted(lambda p: 0 if p.website_id else 1):
        by_url.setdefault(p.url, p)
    infos = []
    for p in by_url.values():
        if not include_unpublished and not p.is_published:
            continue
        try:
            infos.append(_page_seo(p))
        except ToolError:
            continue
    titles = Counter(i["meta_title"] for i in infos if i["meta_title"])
    dup = {t for t, n in titles.items() if n > 1}
    rows, totals = [], Counter()
    for info in infos:
        issues = _issues(info, dup)
        for sev, _msg in issues:
            totals[sev] += 1
        rows.append({"url": info["url"], "page_id": info["page_id"], "score": max(0, 100 - sum(SCORE[s] for s, _m in issues)),
                     "issues": [{"severity": s, "issue": m} for s, m in issues]})
    rows.sort(key=lambda r: r["score"])
    site = {"robots_txt_set": bool(w.sudo().robots_txt), "default_social_image": bool(w.social_default_image),
            "languages": w.language_ids.mapped("code")}
    return {"website": w.name, "pages_checked": len(rows), "issues_by_severity": dict(totals),
            "average_score": round(sum(r["score"] for r in rows) / len(rows), 1) if rows else None,
            "site": site, "pages": rows[:ctx.cap_limit(limit, 30)]}


@tool("seo_page", "seo", "Inspect a page's SEO",
      "Show one page's SEO data: meta title and description, keywords, headings, images with alt texts, word count, "
      "indexing, and its issues.",
      properties={"page": S_PAGE, "website_id": S_WEBSITE}, required=("page",))
def seo_page(ctx, page, website_id=None):
    w = sc.website(ctx, website_id)
    info = _page_seo(sc.find_page(ctx, w, page))
    info["issues"] = [{"severity": s, "issue": m} for s, m in _issues(info, set())]
    return info


def _meta_vals(title, description, keywords, social_image):
    vals = {}
    if title is not None:
        vals["website_meta_title"] = title.strip() or False
    if description is not None:
        vals["website_meta_description"] = description.strip() or False
    if keywords is not None:
        vals["website_meta_keywords"] = keywords.strip() or False
    if social_image is not None:
        vals["website_meta_og_img"] = social_image.strip() or False
    if not vals:
        raise ToolError("Nothing to change.")
    return vals


@tool("seo_meta_set", "seo", "Set meta title and description",
      "Set the meta title, meta description, keywords and/or social sharing image of a page.",
      properties={"page": S_PAGE, "website_id": S_WEBSITE, "title": s_str("Meta title (≤ 60 characters)."),
                  "description": s_str("Meta description (70-160 characters)."), "keywords": s_str("Keywords."),
                  "social_image": s_str("Image URL for social sharing.")},
      required=("page",), writes=True)
def seo_meta_set(ctx, page, website_id=None, title=None, description=None, keywords=None, social_image=None):
    w = sc.website(ctx, website_id)
    perm = sc.permission(ctx, w, "allow_seo")
    p = sc.find_page(ctx, w, page)
    sc.check_page(ctx, perm, p)
    p = sc.own_page(ctx, w, p)
    p.write(_meta_vals(title, description, keywords, social_image))
    return {"page": p.url, "meta_title": p.website_meta_title, "meta_description": p.website_meta_description}


@tool("seo_meta_bulk", "seo", "Set meta data on several pages",
      "Set meta titles and descriptions on many pages in one call.",
      properties={"website_id": S_WEBSITE,
                  "pages": s_arr("One entry per page.", s_obj("Page meta", {
                      "page": S_PAGE, "title": s_str("Meta title."), "description": s_str("Meta description.")}))},
      required=("pages",), writes=True)
def seo_meta_bulk(ctx, pages, website_id=None):
    w = sc.website(ctx, website_id)
    perm = sc.permission(ctx, w, "allow_seo")
    done, failed = [], []
    for item in pages or []:
        try:
            p = sc.find_page(ctx, w, item.get("page"))
            sc.check_page(ctx, perm, p)
            p = sc.own_page(ctx, w, p)
            p.write(_meta_vals(item.get("title"), item.get("description"), None, None))
            done.append(p.url)
        except ToolError as e:
            failed.append({"page": item.get("page"), "error": str(e)})
    return {"updated": done, "failed": failed}


@tool("seo_url_set", "seo", "Change a page URL",
      "Give a page a new address. By default a permanent (301) redirect from the old address is created so links "
      "and rankings are kept.",
      properties={"page": S_PAGE, "website_id": S_WEBSITE, "new_url": s_str("New address, e.g. /handmade-vases."),
                  "redirect": s_bool("Create a 301 redirect from the old address.", True)},
      required=("page", "new_url"), writes=True)
def seo_url_set(ctx, page, new_url, website_id=None, redirect=True):
    w = sc.website(ctx, website_id)
    perm = sc.permission(ctx, w, "allow_seo")
    p = sc.find_page(ctx, w, page)
    sc.check_page(ctx, perm, p)
    if p.url == "/" or p.view_id.key == "website.homepage":
        raise ToolError("The homepage address cannot be changed.")
    p = sc.own_page(ctx, w, p)
    old = p.url
    p.write({"url": new_url})
    new = p.url
    if redirect and old != new:
        ctx.env["website.rewrite"].sudo().create({"name": "AI: %s → %s" % (old, new), "website_id": w.id,
                                                  "url_from": old, "url_to": new, "redirect_type": "301"})
    if perm.scope == "listed":
        perm._add_url(new)
    return {"old_url": old, "new_url": new, "redirect": bool(redirect and old != new)}


@tool("seo_redirects", "seo", "List redirects",
      "List the website's URL redirects.", properties={"website_id": S_WEBSITE, "search": s_str("URL contains.")})
def seo_redirects(ctx, website_id=None, search=None):
    w = sc.website(ctx, website_id)
    dom = [("website_id", "in", (w.id, False))]
    if search:
        dom += ["|", ("url_from", "ilike", search), ("url_to", "ilike", search)]
    return [{"id": r.id, "from": r.url_from, "to": r.url_to, "type": r.redirect_type, "active": r.active}
            for r in ctx.env["website.rewrite"].sudo().search(dom, limit=300)]


@tool("seo_redirect_add", "seo", "Add a redirect",
      "Redirect an old address to a new one (301 permanent, 302 temporary) or mark it as gone (404).",
      properties={"website_id": S_WEBSITE, "from_url": s_str("Old address, e.g. /old-page."),
                  "to_url": s_str("New address (not needed for 404)."),
                  "type": s_str("301, 302 or 404.", ["301", "302", "404"], "301")},
      required=("from_url",), writes=True)
def seo_redirect_add(ctx, from_url, to_url=None, type="301", website_id=None):
    w = sc.website(ctx, website_id)
    sc.permission(ctx, w, "allow_seo")
    for u in (from_url, to_url):
        if u and not sc.URL_RE.match(u):
            raise ToolError("Addresses must start with / and contain no spaces: %r." % u)
    if type != "404" and not to_url:
        raise ToolError("Give the new address.")
    if from_url == to_url:
        raise ToolError("A page cannot redirect to itself.")
    r = ctx.env["website.rewrite"].sudo().create({"name": "AI: %s" % from_url, "website_id": w.id,
                                                  "url_from": from_url, "url_to": to_url or False,
                                                  "redirect_type": type})
    return {"id": r.id, "from": r.url_from, "to": r.url_to, "type": r.redirect_type}


@tool("seo_redirect_remove", "seo", "Remove a redirect",
      "Delete a redirect by id.", properties={"redirect_id": s_int("Redirect id.")},
      required=("redirect_id",), writes=True)
def seo_redirect_remove(ctx, redirect_id):
    r = ctx.env["website.rewrite"].sudo().browse(int(redirect_id)).exists()
    if not r:
        raise ToolError("Redirect %s does not exist." % redirect_id)
    w = r.website_id or sc.website(ctx)
    sc.permission(ctx, w, "allow_seo")
    info = {"from": r.url_from, "to": r.url_to}
    r.unlink()
    return {"removed": info}


@tool("seo_indexing", "seo", "Show or hide a page in search engines",
      "Allow or prevent search engines from indexing a page.",
      properties={"page": S_PAGE, "website_id": S_WEBSITE, "indexed": s_bool("true = visible in search engines.")},
      required=("page", "indexed"), writes=True)
def seo_indexing(ctx, page, indexed, website_id=None):
    w = sc.website(ctx, website_id)
    perm = sc.permission(ctx, w, "allow_seo")
    p = sc.find_page(ctx, w, page)
    sc.check_page(ctx, perm, p)
    p = sc.own_page(ctx, w, p)
    p.website_indexed = bool(indexed)
    return {"page": p.url, "indexed": p.website_indexed}


@tool("seo_alt_texts", "seo", "Set image alt texts",
      "Set the alt text of images on a page. Identify images by their address (src) as listed by seo_page.",
      properties={"page": S_PAGE, "website_id": S_WEBSITE,
                  "images": s_arr("Images to update.", s_obj("Image", {"src": s_str("Image address (or the start of "
                                                                                    "it)."),
                                                                       "alt": s_str("Alt text.")}))},
      required=("page", "images"), writes=True)
def seo_alt_texts(ctx, page, images, website_id=None):
    w = sc.website(ctx, website_id)
    perm = sc.permission(ctx, w, "allow_seo")
    p = sc.own_page(ctx, w, sc.find_page(ctx, w, page))
    sc.check_page(ctx, perm, p)
    root = sc.read_arch(p.view_id)
    done, missing = [], []
    for item in images or []:
        src = (item.get("src") or "").strip()
        hits = [i for i in root.iter("img") if src and (i.get("src") or "").startswith(src)]
        if not hits:
            missing.append(src)
            continue
        for i in hits:
            i.set("alt", item.get("alt") or "")
        done.append(src)
    if not done:
        raise ToolError("None of these images were found on %s." % p.url)
    snap = sc.save_arch(ctx, w, p, root, "Alt texts")
    return {"page": p.url, "updated": done, "not_found": missing, "undo_id": snap.id}


@tool("seo_headings_fix", "seo", "Fix the heading structure",
      "Make a page have exactly one H1: keep the first H1 (or promote the first H2 when there is none) and turn "
      "other H1s into H2s. Optionally set the H1 text.",
      properties={"page": S_PAGE, "website_id": S_WEBSITE, "h1_text": s_str("New text for the main heading.")},
      required=("page",), writes=True)
def seo_headings_fix(ctx, page, website_id=None, h1_text=None):
    w = sc.website(ctx, website_id)
    perm = sc.permission(ctx, w, "allow_seo")
    p = sc.own_page(ctx, w, sc.find_page(ctx, w, page))
    sc.check_page(ctx, perm, p)
    root = sc.read_arch(p.view_id)
    wrap = sc.wrap_of(root)
    h1s = wrap.xpath(".//h1")
    changes = []
    if not h1s:
        h2 = wrap.xpath(".//h2")
        if not h2:
            raise ToolError("The page has no heading to promote; add one with the website builder.")
        h2[0].tag = "h1"
        h1s = [h2[0]]
        changes.append("promoted the first H2 to H1")
    for extra in h1s[1:]:
        extra.tag = "h2"
        changes.append("H1 '%s' → H2" % sc.text_of(extra, 50))
    if h1_text:
        main = h1s[0]
        for child in list(main):
            main.remove(child)
        main.text = h1_text
        changes.append("set H1 text")
    if not changes:
        return {"page": p.url, "changes": [], "message": "Heading structure is already fine."}
    snap = sc.save_arch(ctx, w, p, root, "Headings")
    return {"page": p.url, "changes": changes, "undo_id": snap.id}


@tool("seo_robots", "seo", "Read or change robots.txt",
      "Show the website's robots.txt, or replace it (site-wide permission needed).",
      properties={"website_id": S_WEBSITE, "content": s_str("New robots.txt content; omit to just read it.")},
      writes=True)
def seo_robots(ctx, website_id=None, content=None):
    w = sc.website(ctx, website_id)
    if content is None:
        return {"website": w.name, "robots_txt": w.sudo().robots_txt or "(Odoo default)"}
    sc.permission(ctx, w, "allow_sitewide")
    w.sudo().robots_txt = content
    return {"website": w.name, "robots_txt": content}


@tool("seo_sitemap_refresh", "seo", "Refresh the sitemap",
      "Clear the cached sitemap so search engines get an up-to-date list of pages on their next visit.",
      properties={"website_id": S_WEBSITE}, writes=True)
def seo_sitemap_refresh(ctx, website_id=None):
    w = sc.website(ctx, website_id)
    sc.permission(ctx, w, "allow_seo")
    atts = ctx.env["ir.attachment"].sudo().search([("url", "=like", "/sitemap-%d-%%" % w.id), ("type", "=", "binary")])
    n = len(atts)
    atts.unlink()
    base = (w.domain or ctx.env["ir.config_parameter"].sudo().get_param("web.base.url", "")).rstrip("/")
    if base and not base.startswith("http"):
        base = "https://" + base
    return {"cleared_cache_files": n, "sitemap": base + "/sitemap.xml"}


@tool("seo_site_settings", "seo", "Website SEO settings",
      "Read or change site-wide SEO settings: website name, Google Analytics key and the default social sharing "
      "image (site-wide permission needed to change).",
      properties={"website_id": S_WEBSITE, "name": s_str("Website name."),
                  "google_analytics_key": s_str("Measurement id, e.g. G-XXXXXXX."),
                  "social_image_url": s_str("Image URL to use as default social image.")},
      writes=True)
def seo_site_settings(ctx, website_id=None, name=None, google_analytics_key=None, social_image_url=None):
    w = sc.website(ctx, website_id).sudo()
    vals = {}
    if name:
        vals["name"] = name
    if google_analytics_key is not None:
        vals["google_analytics_key"] = google_analytics_key or False
    if social_image_url:
        from .designer_tools import _download_image
        data, _mime = _download_image(social_image_url, ctx.env)
        import base64
        vals["social_default_image"] = base64.b64encode(data)
    if vals:
        sc.permission(ctx, w, "allow_sitewide")
        w.write(vals)
    return {"website": w.name, "domain": w.domain or "", "google_analytics_key": w.google_analytics_key or "",
            "has_default_social_image": bool(w.social_default_image),
            "languages": w.language_ids.mapped("code"), "default_language": w.default_lang_id.code}


@tool("seo_undo", "seo", "Undo an SEO content change",
      "Restore a page to the version before a change made by the SEO tools (alt texts, headings).",
      properties={"undo_id": s_int("The undo_id returned by the change.")}, required=("undo_id",), writes=True)
def seo_undo(ctx, undo_id):
    from .designer_tools import restore_snapshot
    return restore_snapshot(ctx, undo_id)
