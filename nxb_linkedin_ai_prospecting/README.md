# AI Prospecting Agents for LinkedIn (Odoo 18)

**Technical name:** `nxb_linkedin_ai_prospecting` · **Author:** Nextbridge · **License:** OPL-1 · **Price:** 500 USD

LinkedIn prospecting for Odoo 18, run by AI agents. The agents write personal invitations, hold the first
conversations, follow up with silent prospects and draft posts. Odoo controls volume, timing and pacing, and creates
a CRM lead when a prospect is ready for sales. Your AI assistant connects through a secure MCP connector, and no AI
API key is stored in Odoo.

## Contents

| Path | Purpose |
|---|---|
| `models/` | Accounts, services, personas, prospects, queue, posts, agents, AI work interface, dashboard data |
| `controllers/mcp.py` | MCP connector endpoint (`/li/mcp/<token>`) |
| `controllers/portal.py` | Public portfolio links and the in-Odoo LinkedIn sign-in view |
| `engine/` | Automation-browser installer, process manager and the standalone engine server |
| `static/src/dashboard/` | Dashboard, offline prospects map and remote sign-in client actions |
| `static/description/` | App Store page, icon, banner, screenshots and `user_guide.pdf` |
| `doc/USER_GUIDE.md` | Full user guide (same content as the PDF) |

## Quick start

1. Install the app (dependencies: CRM, Calendar, Discuss, Contacts, Sales Teams).
2. **LinkedIn Sales → Configuration → Settings**: *Prepare engine* (self-hosted only), then *New connector URL*.
   Add the URL to your AI assistant as a custom MCP connector, with no authentication.
3. **Agents → LinkedIn Profiles**: add each LinkedIn account and connect it.
4. **Configuration → Services** and **Agents → Personas**: describe what you sell and to whom, then click *Run* on
   the agents.
5. Copy a prompt from Settings into your AI assistant, or create a scheduled task with the *Scheduled* prompt.

See `doc/USER_GUIDE.md` for full instructions, troubleshooting and FAQ.

## Production notes

- Run Odoo with workers and cron threads. Odoo must be reachable over HTTPS.
- The built-in engine needs a self-hosted Linux server. On Odoo.sh, use the AI browser agent option.
- LinkedIn changes its pages from time to time. Engine page handling lives in `engine/engine_server.py`, and every
  automated action is logged under **Activity → Engine actions**.

## Support

Nextbridge — <https://www.nextbridge.pk>

LinkedIn is a trademark of LinkedIn Corporation. This app is an independent product of Nextbridge and is not
affiliated with, endorsed or sponsored by LinkedIn.
