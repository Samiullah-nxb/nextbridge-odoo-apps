# -*- coding: utf-8 -*-
# Copyright 2026 Nextbridge. License OPL-1.
from odoo.tests import tagged

from .common import McpCase


@tagged("post_install", "-at_install", "nxb_mcp")
class TestCrm(McpCase):

    def setUp(self):
        super().setUp()
        if not self.installed("crm"):
            self.skipTest("CRM not installed")

    def test_brief_and_leads(self):
        brief = self.env["nxb.mcp.brief"].create({"name": "DE retail", "kind": "prospecting",
                                                  "goal": "Find furniture retailers", "tags": "AI, DE",
                                                  "state": "active"})
        self.assertIn(str(brief.id), brief.prompt)
        data = self.call("crm_brief_read")
        self.assertEqual(data["brief_id"], brief.id)
        self.assertEqual(data["tags"], ["AI", "DE"])
        lead = self.call("crm_lead_add", name="Moebel GmbH – shop fit", company="Moebel GmbH",
                         email="info@moebel-test.de", website="https://www.moebel-test.de", country="DE",
                         brief_id=brief.id, expected_revenue=5000)["created"]
        self.assertEqual(lead["country"], "Germany")
        self.assertEqual(set(lead["tags"]), {"AI", "DE"})
        dup = self.call("crm_lead_add", name="Again", email="sales@moebel-test.de")
        self.assertTrue(dup["duplicate"])
        found = self.call("crm_lead_find", text="Moebel")
        self.assertEqual(found["total"], 1)
        stage = self.env["crm.stage"].search([], limit=1, offset=1) or self.env["crm.stage"].search([], limit=1)
        upd = self.call("crm_lead_change", lead_id=lead["id"], stage=stage.name, convert_to_opportunity=True,
                        add_tags=["Hot"], probability=40)["updated"]
        self.assertEqual(upd["type"], "opportunity")
        self.assertIn("Hot", upd["tags"])
        fu = self.call("crm_lead_followup", lead_id=lead["id"], note="Spoke to owner", activity="call",
                       summary="Call back", due_date="2030-01-15")
        self.assertEqual(fu["activity"]["due"], "2030-01-15")
        pipe = self.call("crm_pipeline")
        self.assertTrue(pipe["open_opportunities_by_stage"])
        lost = self.call("crm_lead_close", lead_id=lead["id"], outcome="lost", reason="Too expensive")
        self.assertFalse(lost["lead"]["active"])
        self.call("crm_brief_done", brief_id=brief.id, summary="1 lead")
        self.assertEqual(brief.state, "done")
        self.assertIn("No stage", self.call("crm_lead_change", ok=False, lead_id=lead["id"], stage="Nope-stage"))


@tagged("post_install", "-at_install", "nxb_mcp")
class TestMailing(McpCase):

    def setUp(self):
        super().setUp()
        if not self.installed("mass_mailing"):
            self.skipTest("Email Marketing not installed")

    def test_campaign_flow(self):
        brief = self.env["nxb.mcp.brief"].create({"name": "Spring", "kind": "campaign", "goal": "Promote spring",
                                                  "state": "active", "call_to_action": "Shop now"})
        self.assertEqual(self.call("mail_brief_read")["brief_id"], brief.id)
        self.assertTrue(self.call("mail_guide")["blocks"])
        lst = self.call("mail_list_create", name="Spring VIP")
        add = self.call("mail_contact_add", lists=["Spring VIP"], contacts=[
            {"name": "Ann", "email": "ann@example.com"}, {"name": "Bob", "email": "BOB@example.com"},
            {"name": "No mail"}])
        self.assertEqual(add["new_contacts"], 2)
        self.assertEqual(add["skipped_without_email"], ["No mail"])
        again = self.call("mail_contact_add", lists=[str(lst["id"])], contacts=[{"email": "bob@example.com"}])
        self.assertEqual(again["existing_subscribed"], 1)
        self.assertEqual(self.call("mail_contact_find", list="Spring VIP")["total"], 2)
        self.assertTrue(any(l["name"] == "Spring VIP" for l in self.call("mail_lists")))
        sections = [{"type": "heading", "text": "Spring is here"},
                    {"type": "text", "text": "New colours.\n\nFree shipping."},
                    {"type": "button", "text": "Shop now", "url": "https://example.com/shop"},
                    {"type": "divider"}]
        if "product.product" in self.env:
            self.env["product.product"].create({"name": "Spring Vase", "default_code": "SV1", "list_price": 25})
            sections.append({"type": "products", "products": ["SV1", "Unknown thing"]})
        d = self.call("mail_campaign_draft", subject="Spring collection", preview="New in", lists=["Spring VIP"],
                      sections=sections, brief_id=brief.id)
        self.assertEqual(d["state"], "draft")
        m = self.env["mailing.mailing"].browse(d["id"])
        self.assertIn("Spring is here", m.body_html)
        if "product.product" in self.env:
            self.assertIn("Spring Vase", m.body_html)
            self.assertEqual(d["products_not_found"], ["Unknown thing"])
        self.call("mail_campaign_update", mailing_id=m.id, subject="Spring collection 2026")
        self.assertEqual(m.subject, "Spring collection 2026")
        self.assertTrue(self.call("mail_campaign_find", text="Spring"))
        out = self.call("mail_campaign_send", ok=False, mailing_id=m.id, confirm=False)
        self.assertIn("cannot be undone", out)
        sent = self.call("mail_campaign_send", mailing_id=m.id, confirm=True, send_at="2035-01-01 09:00")
        self.assertEqual(sent["state"], "in_queue")
        self.assertIn("Only draft", self.call("mail_campaign_update", ok=False, mailing_id=m.id, subject="x"))
        self.call("mail_brief_done", brief_id=brief.id, summary="Drafted")
        self.assertEqual(brief.state, "done")

    def test_escaping(self):
        self.call("mail_list_create", name="Esc")
        d = self.call("mail_campaign_draft", subject="x", lists=["Esc"],
                      sections=[{"type": "text", "text": "<script>alert(1)</script>"}])
        self.assertNotIn("<script>", self.env["mailing.mailing"].browse(d["id"]).body_html)
