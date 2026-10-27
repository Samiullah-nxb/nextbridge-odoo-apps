# -*- coding: utf-8 -*-
# Copyright 2026 Nextbridge. License OPL-1.
from unittest.mock import patch

from odoo.tests import tagged

from .common import McpCase

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
       b"\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x35\x81\x84\x00\x00\x00\x00IEND\xaeB`\x82")

EXTERNAL = b"""<html><head><title>Our studio</title><script>evil()</script></head><body>
<nav><a href="/">Home</a></nav><main><h1>We make vases</h1><p>Handmade in Lahore since 1990.</p>
<img src="/img/vase.png" alt="A blue vase"/><h2>Workshop</h2><p onclick="x()">Visit us any weekday.</p>
<ul><li>Clay</li><li>Glaze</li></ul></main><footer>(c)</footer></body></html>"""


class FakeResponse:
    def __init__(self, content, ctype, url):
        self.content, self.headers, self.url, self.status_code = content, {"Content-Type": ctype}, url, 200

    def raise_for_status(self):
        return None

    def iter_content(self, size):
        yield self.content


def fake_get(url, **kw):
    if url.endswith(".png"):
        return FakeResponse(PNG, "image/png", url)
    return FakeResponse(EXTERNAL, "text/html; charset=utf-8", url)


@tagged("post_install", "-at_install", "nxb_mcp")
class TestWebsite(McpCase):

    def setUp(self):
        super().setUp()
        if not self.installed("website"):
            self.skipTest("Website not installed")
        self.website = self.env["website"].search([], limit=1)
        self.perm = self.env["nxb.mcp.site.permission"].create({
            "website_key": str(self.website.id), "scope": "all", "allow_seo": True, "allow_edit": True,
            "allow_create": True, "allow_delete": True, "allow_sitewide": True})
        self.w = self.website.id

    def _new_page(self, name="MCP Test Page", blocks=("s_text_image", "s_title")):
        res = self.call("site_page_create", website_id=self.w, name=name, blocks=list(blocks))
        return res

    # -------------------------------------------------------------- permissions
    def test_permission_required(self):
        self.perm.active = False
        out = self.call("site_page_create", ok=False, website_id=self.w, name="Nope")
        self.assertIn("not allowed", out)
        out = self.call("seo_meta_set", ok=False, website_id=self.w, page="/", title="x")
        self.assertIn("not allowed", out)
        self.perm.write({"active": True, "allow_create": False})
        out = self.call("site_page_create", ok=False, website_id=self.w, name="Nope")
        self.assertIn("is not allowed", out)
        # listed scope: pages outside the list are refused
        page = self._create_allowed_page()
        self.perm.write({"scope": "listed", "page_urls": "/somewhere-else"})
        out = self.call("site_block_add", ok=False, website_id=self.w, page=page["url"], block="s_title")
        self.assertIn("not in the list", out)
        self.perm.write({"page_urls": page["url"]})
        self.call("site_block_add", website_id=self.w, page=page["url"], block="s_title")

    def _create_allowed_page(self):
        allow = self.perm.allow_create
        self.perm.allow_create = True
        res = self._new_page("Perm page", blocks=())
        self.perm.allow_create = allow
        return res

    # -------------------------------------------------------------- read tools
    def test_read_tools(self):
        self.assertTrue(self.call("site_guide")["styles"]["fonts"])
        ov = self.call("site_overview")
        self.assertIn(self.w, [w["website_id"] for w in ov["websites"]])
        blocks = self.call("site_blocks")
        keys = [b["key"] for b in blocks["blocks"]]
        self.assertIn("s_text_image", keys)
        self.assertIn("s_banner", keys)
        pages = self.call("site_pages", website_id=self.w)
        self.assertTrue(any(p["url"] == "/contactus" for p in pages))
        struct = self.call("site_page_structure", website_id=self.w, page="/")
        self.assertEqual(struct["page"], "/")

    # -------------------------------------------------------------- page building
    def test_page_lifecycle_and_blocks(self):
        page = self._new_page()
        url = page["url"]
        self.assertFalse(page["published"])
        self.assertEqual(page["blocks_added"], ["s_text_image", "s_title"])
        st = self.call("site_page_structure", website_id=self.w, page=url)
        self.assertEqual([s["block"] for s in st["sections"]][:2], ["s_text_image", "s_title"])

        add = self.call("site_block_add", website_id=self.w, page=url, block="s_banner", after_section=0)
        self.assertEqual(add["section"], 1)
        st = self.call("site_page_structure", website_id=self.w, page=url)
        self.assertEqual(st["sections"][0]["block"], "s_banner")
        self.call("site_block_move", website_id=self.w, page=url, section=1, to_position=3)
        st = self.call("site_page_structure", website_id=self.w, page=url)
        self.assertEqual([s["block"] for s in st["sections"]], ["s_text_image", "s_title", "s_banner"])
        self.call("site_block_duplicate", website_id=self.w, page=url, section=2)
        st = self.call("site_page_structure", website_id=self.w, page=url)
        self.assertEqual(len(st["sections"]), 4)
        self.call("site_block_remove", website_id=self.w, page=url, section=3)
        st = self.call("site_page_structure", website_id=self.w, page=url)
        self.assertEqual(len(st["sections"]), 3)
        self.assertIn("does not exist", self.call("site_block_remove", ok=False, website_id=self.w, page=url,
                                                  section=99))
        self.assertIn("unknown building block", self.call("site_block_add", ok=False, website_id=self.w,
                                                          page=url, block="s_no_such_block").lower())

        # text, link, image, style
        sec1 = st["sections"][0]
        heading = next(e for e in sec1["elements"] if e["type"].startswith("h"))
        res = self.call("site_text_set", website_id=self.w, page=url, section=1, element=heading["path"],
                        text="Crafted by hand")
        self.assertEqual(res["now"], "Crafted by hand")
        para = next(e for e in sec1["elements"] if e["type"] == "p")
        self.call("site_text_set", website_id=self.w, page=url, section=1, element=para["path"],
                  text="First.\n\nSecond.")
        sec1 = self.call("site_page_structure", website_id=self.w, page=url)["sections"][0]
        self.assertIn("Second.", sec1["summary"])
        link = next((e for e in sec1["elements"] if e["type"] in ("button", "link")), None)
        if link:
            res = self.call("site_link_set", website_id=self.w, page=url, section=1, element=link["path"],
                            url="/contactus", label="Talk to us")
            self.assertEqual(res["href"], "/contactus")
        self.assertIn("javascript", self.call("site_link_set", ok=False, website_id=self.w, page=url, section=1,
                                              element=".", url="javascript:alert(1)"))
        img = next(e for e in sec1["elements"] if e["type"] == "image")
        att = self.env["ir.attachment"].create({"name": "x.png", "raw": PNG, "public": True})
        res = self.call("site_image_set", website_id=self.w, page=url, section=1, element=img["path"],
                        image_url="/web/image/%d" % att.id, alt="Blue vase")
        self.assertEqual(res["alt"], "Blue vase")
        res = self.call("site_section_style", website_id=self.w, page=url, section=1, background="dark",
                        spacing="large", align="center", add_classes=["my-class"])
        self.assertIn("text-center", res["classes"])
        self.assertIn("my-class", res["classes"])
        view = self.env["website.page"].browse(page["page_id"]).view_id
        self.assertIn("Crafted by hand", view.arch)
        self.assertIn("Talk to us", view.arch) if link else None
        self.assertIn("Blue vase", view.arch)

        # undo / redo
        hist = self.call("site_history", website_id=self.w, page=url)
        last = hist[0]
        self.assertIn("Style", last["change"])
        undo = self.call("site_undo", undo_id=last["undo_id"])
        self.assertNotIn("my-class", view.arch)
        self.call("site_undo", undo_id=undo["redo_id"])
        self.assertIn("my-class", view.arch)

        # update and delete
        upd = self.call("site_page_update", website_id=self.w, page=url, name="Renamed", publish=True,
                        in_menu=True)
        self.assertTrue(upd["published"])
        self.assertTrue(upd["in_menu"])
        out = self.call("site_page_delete", ok=False, website_id=self.w, page=url, confirm=False)
        self.assertIn("confirm", out)
        self.call("site_page_delete", website_id=self.w, page=url, confirm=True, redirect_to="/")
        self.assertFalse(self.env["website.page"].browse(page["page_id"]).exists())
        self.assertTrue(self.env["website.rewrite"].search([("url_from", "=", url)]))
        self.assertIn("homepage", self.call("site_page_delete", ok=False, website_id=self.w, page="/",
                                            confirm=True))

    def test_section_html_sanitised(self):
        page = self._new_page(blocks=())
        res = self.call("site_section_html", website_id=self.w, page=page["url"],
                        html='<h2 onclick="x()">Hello</h2><script>bad()</script><a href="javascript:x">l</a>')
        self.assertEqual(res["inserted_sections"], 2)
        arch = self.env["website.page"].browse(page["page_id"]).view_id.arch
        self.assertIn("Hello", arch)
        self.assertNotIn("onclick", arch)
        self.assertNotIn("bad()", arch)
        self.assertNotIn("javascript:", arch)
        self.call("site_section_html", website_id=self.w, page=page["url"], replace_section=1,
                  html='<section class="pt32"><div class="container"><p>Replaced</p></div></section>')
        arch = self.env["website.page"].browse(page["page_id"]).view_id.arch
        self.assertIn("Replaced", arch)

    # -------------------------------------------------------------- site-wide
    def test_menu_theme_css_code(self):
        menu = self.call("site_menu", website_id=self.w)
        self.assertTrue(menu["menu"])
        m = self.call("site_menu_change", website_id=self.w, action="add", label="Blog AI", url="/blog-ai",
                      position=1)
        self.assertEqual(self.call("site_menu", website_id=self.w)["menu"][0]["label"], "Blog AI")
        self.call("site_menu_change", website_id=self.w, action="rename", menu_id=m["menu_id"], label="Journal")
        self.call("site_menu_change", website_id=self.w, action="relink", menu_id=m["menu_id"], url="/journal")
        self.assertEqual(self.env["website.menu"].browse(m["menu_id"]).url, "/journal")
        self.call("site_menu_change", website_id=self.w, action="remove", menu_id=m["menu_id"])
        self.assertFalse(self.env["website.menu"].browse(m["menu_id"]).exists())

        res = self.call("site_theme", website_id=self.w, palette="default-3",
                        colors=["#112233", "#445566"], heading_font="Raleway")
        self.assertEqual(res["changed"]["palette"], "default-3")
        self.assertIn("Colours", self.call("site_theme", ok=False, website_id=self.w, colors=["red"]))
        self.assertIn("not available", self.call("site_theme", ok=False, website_id=self.w, body_font="Comic"))

        self.call("site_custom_css", website_id=self.w, mode="replace", css=".x { color: red; }")
        self.call("site_custom_css", website_id=self.w, mode="append", css=".y { color: blue; }")
        css = self.call("site_custom_css", website_id=self.w, mode="read")["css"]
        self.assertIn(".x", css)
        self.assertIn(".y", css)
        self.assertIn("not allowed", self.call("site_custom_css", ok=False, website_id=self.w, mode="append",
                                               css="@import url(http://evil)"))

        self.call("site_custom_code", website_id=self.w, where="head", code="<meta name='x' content='1'/>")
        self.assertIn("meta", self.call("site_custom_code", website_id=self.w, where="head")["head"])

        self.perm.allow_sitewide = False
        self.assertIn("not allowed", self.call("site_theme", ok=False, website_id=self.w, palette="default-2"))
        self.assertIn("not allowed", self.call("site_menu_change", ok=False, website_id=self.w, action="add",
                                               label="a", url="/a"))

    # -------------------------------------------------------------- external pages
    def test_ssrf_protection(self):
        out = self.call("site_web_preview", ok=False, url="http://127.0.0.1:8069/")
        self.assertIn("own network", out)
        out = self.call("site_image_upload", ok=False, image_url="http://169.254.169.254/latest/meta-data/x.png")
        self.assertIn("own network", out)
        self.assertIn("http", self.call("site_web_preview", ok=False, url="file:///etc/passwd"))

    def test_web_preview_and_import(self):
        self.env["ir.config_parameter"].sudo().set_param("nxb_mcp_hub.allow_private_fetch", "True")
        with patch("requests.get", side_effect=fake_get):
            prev = self.call("site_web_preview", url="http://example.test/studio")
            self.assertEqual(prev["title"], "Our studio")
            self.assertEqual(prev["headings"][0]["text"], "We make vases")
            self.assertEqual(prev["images"][0]["src"], "http://example.test/img/vase.png")
            up = self.call("site_image_upload", image_url="http://example.test/a.png")
            self.assertTrue(up["src"].startswith("/web/image/"))
            imp = self.call("site_web_import", url="http://example.test/studio", name="Studio copy",
                            download_images=True)
        self.assertEqual(imp["sections"], 2)
        self.assertEqual(imp["images_copied"], 1)
        arch = self.env["website.page"].browse(imp["page_id"]).view_id.arch
        self.assertIn("We make vases", arch)
        self.assertIn("Visit us any weekday", arch)
        self.assertNotIn("onclick", arch)
        self.assertNotIn("evil()", arch)
        self.assertNotIn("(c)", arch)

    # -------------------------------------------------------------- SEO
    def test_seo_tools(self):
        self.assertTrue(self.call("seo_guide")["meta_title"])
        page = self._new_page("Seo Page", blocks=("s_text_image",))
        url = page["url"]
        self.call("site_section_html", website_id=self.w, page=url,
                  html="<h1>One</h1><h1>Two</h1><img src='/web/image/website.s_text_image_default_image'/>")
        audit = self.call("seo_audit", website_id=self.w, include_unpublished=True, limit=200)
        row = next(r for r in audit["pages"] if r["url"] == url)
        issues = " ".join(i["issue"] for i in row["issues"])
        self.assertIn("No meta title", issues)
        self.assertIn("H1 headings", issues)
        info = self.call("seo_page", website_id=self.w, page=url)
        self.assertGreaterEqual(len([h for h in info["headings"] if h["tag"] == "h1"]), 2)

        res = self.call("seo_meta_set", website_id=self.w, page=url, title="Handmade vases | Studio",
                        description="Handmade ceramic vases from our Lahore studio, glazed by hand and shipped "
                                    "worldwide.")
        self.assertEqual(res["meta_title"], "Handmade vases | Studio")
        bulk = self.call("seo_meta_bulk", website_id=self.w, pages=[
            {"page": url, "title": "Vases"}, {"page": "/does-not-exist", "title": "x"}])
        self.assertEqual(bulk["updated"], [url])
        self.assertEqual(len(bulk["failed"]), 1)

        fix = self.call("seo_headings_fix", website_id=self.w, page=url, h1_text="Handmade vases")
        self.assertTrue(fix["changes"])
        info = self.call("seo_page", website_id=self.w, page=url)
        self.assertEqual(len([h for h in info["headings"] if h["tag"] == "h1"]), 1)
        self.call("seo_undo", undo_id=fix["undo_id"])
        info = self.call("seo_page", website_id=self.w, page=url)
        self.assertGreaterEqual(len([h for h in info["headings"] if h["tag"] == "h1"]), 2)

        alt = self.call("seo_alt_texts", website_id=self.w, page=url,
                        images=[{"src": "/web/image/website.s_text_image_default_image", "alt": "Vase"}])
        self.assertTrue(alt["updated"])
        self.call("seo_indexing", website_id=self.w, page=url, indexed=False)
        self.assertFalse(self.env["website.page"].browse(page["page_id"]).website_indexed)

        moved = self.call("seo_url_set", website_id=self.w, page=url, new_url="/handmade-vases")
        self.assertEqual(moved["new_url"], "/handmade-vases")
        self.assertTrue(moved["redirect"])
        reds = self.call("seo_redirects", website_id=self.w, search="handmade")
        self.assertTrue(reds)
        r = self.call("seo_redirect_add", website_id=self.w, from_url="/old-shop", to_url="/shop", type="302")
        self.assertEqual(r["type"], "302")
        self.assertIn("start with /", self.call("seo_redirect_add", ok=False, website_id=self.w,
                                                from_url="old shop", to_url="/x"))
        self.call("seo_redirect_remove", redirect_id=r["id"])
        self.assertIn("homepage", self.call("seo_url_set", ok=False, website_id=self.w, page="/", new_url="/home2"))

        self.call("seo_robots", website_id=self.w, content="User-agent: *\nDisallow: /private")
        self.assertIn("Disallow", self.call("seo_robots", website_id=self.w)["robots_txt"])
        self.assertIn("sitemap.xml", self.call("seo_sitemap_refresh", website_id=self.w)["sitemap"])
        s = self.call("seo_site_settings", website_id=self.w, google_analytics_key="G-TEST123")
        self.assertEqual(s["google_analytics_key"], "G-TEST123")

    def test_copy_on_write_of_shared_page(self):
        shared = self.env["website.page"].search([("url", "=", "/contactus"), ("website_id", "=", False)], limit=1)
        if not shared:
            self.skipTest("no shared contact page")
        self.call("seo_meta_set", website_id=self.w, page="/contactus", title="Contact our studio")
        specific = self.env["website.page"].search([("url", "=", "/contactus"), ("website_id", "=", self.w)])
        self.assertEqual(specific.website_meta_title, "Contact our studio")
        self.assertNotEqual(shared.website_meta_title, "Contact our studio")
