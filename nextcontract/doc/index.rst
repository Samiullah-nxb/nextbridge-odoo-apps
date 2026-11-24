============
NextContract
============

**NextContract** is contract lifecycle management for Odoo 19 Enterprise. It takes every contract from the first
request through legal review, drafting, negotiation, signature and activation, and then keeps track of it until it
is renewed, terminated or archived. Everything happens inside Odoo, on the same contacts, documents, signatures and
users you already have.

.. figure:: https://apps.odoocdn.com/apps/assets/19.0/nextcontract/doc_home.png
   :alt: The NextContract home screen
   :width: 100%

   The NextContract home screen. Each tile opens one area of the app.

What NextContract does for you
==============================

.. list-table::
   :class: table table-bordered table-striped
   :header-rows: 1
   :widths: 34 66

   * - The problem
     - How NextContract solves it
   * - Requests arrive by email and chat, with missing details
     - One **intake form** for the business. Legal screens each request and turns it into a contract in one click.
   * - Every contract is written from scratch, or from an old copy
     - A **library** of approved templates and clauses. Drafts are generated in Word or PDF with the contract's
       data already filled in, and the Word file can be edited inside Odoo.
   * - Nobody knows who has to approve what
     - **Approval rules** create review and approval tasks for the right people, and a contract moves on only when
       they are done.
   * - Negotiation happens in email attachments
     - The counterparty uses the **Odoo portal** to read, download and upload revisions. Every round is recorded,
       and versions are compared as a redline.
   * - Signed contracts disappear into folders
     - Signing with **Odoo Sign** (or a paper signature), then **obligations, risk and key-date alerts** on every
       live contract, with renewals and amendments handled in the same place.
   * - Reading every contract takes hours
     - The optional **AI assistant** reads a contract, proposes its key data and flags clauses that differ from your
       standard wording. It works with **Google Gemini, OpenAI, Anthropic Claude**, or a model you **host yourself**
       (Ollama, vLLM, LM Studio), with your own key. People review and confirm every suggestion
       (`The AI assistant`_).

With NextContract you can:

- collect contract requests from the business and screen them in Legal;
- keep approved templates, clauses and Word document templates in one library, and edit those Word templates
  inside Odoo;
- generate drafts in Word or PDF with the contract's data filled in;
- route reviews and approvals as tasks;
- negotiate with the counterparty through the Odoo portal, and compare versions;
- sign with Odoo Sign, or record a signature made on paper;
- track obligations, risk and key dates on live contracts, and renew, amend, terminate or archive them;
- report on all contracts and export them to Excel;
- optionally, let the AI assistant extract contract data and compare clauses with your library, using the AI
  provider you choose: Google Gemini, OpenAI, Anthropic Claude, or an OpenAI-compatible server on your own network.

.. contents:: On this page
   :local:
   :depth: 1


Quick start
===========

The first hour with NextContract, in order. Each step links to the details.

#. **Install** NextContract, its Python packages and LibreOffice (`Installation`_).
#. **Give people a role**: Legal, approvers, requesters and read-only users (`User roles`_).
#. **Check the defaults**: the contract types and the two approval rules are ready to use. Change them only if your
   process differs (`Contract types and fields`_, `Approval rules`_).
#. **Publish your first template**: upload your Word contract and add placeholders (`Step 3: Prepare the template`_).
#. **Run one contract end to end**, from request to activation (`Your first contract, step by step`_).
#. **Optional: connect an AI provider** so the assistant can read contracts for you. It takes about five minutes:
   switch AI on, add a provider with your key, test the connection (`AI features (optional)`_, then
   `The AI assistant`_).

.. tip::
   Run your first contract with a colleague playing the requester and another the approver. That way every
   person sees their own part of the process.


Who does what
=============

.. list-table::
   :class: table table-bordered table-striped
   :header-rows: 1
   :widths: 24 46 30

   * - Role
     - Typical work
     - Read
   * - Business Requester
     - Asks for new contracts and follows their progress
     - Step 1
   * - Legal Reviewer
     - Screens requests, drafts, reviews, negotiates, sends for signature and activates contracts. Runs the AI
       assistant and confirms its suggestions.
     - Steps 2 to 9, `The AI assistant`_
   * - Legal Admin
     - Everything Legal does, plus setup, templates, renewals, terminations, the audit trail, and connecting the AI
       providers
     - `Configuration`_, Steps 3 and 9
   * - Approver / Management
     - Approves or rejects the tasks assigned to them
     - Step 5
   * - Finance User / Procurement User
     - Gives input during review; Finance follows payments and other obligations
     - Steps 5 and 9
   * - Counterparty
     - Reviews the contract and uploads changes through the Odoo portal. Needs no Odoo licence.
     - Step 6


Requirements
============

.. list-table::
   :class: table table-bordered table-striped
   :header-rows: 1
   :widths: 28 72

   * - Item
     - Requirement
   * - Odoo
     - Odoo **19.0 Enterprise** on your own server (or Docker). Not available on Odoo Online, which does not allow
       custom modules. Odoo.sh has not been verified yet.
   * - Odoo apps
     - Documents, Sign, Employees and Purchase. Odoo installs them automatically with NextContract.
   * - Python packages
     - Listed in ``requirements.txt``: ``python-docx``, ``docxtpl``, ``xlsxwriter`` and ``pypdfium2``. For the AI
       features, also ``pydantic``, and ``google-genai`` if you use Google Gemini. OpenAI, Anthropic Claude and
       self-hosted servers need no extra package.
   * - LibreOffice
     - On the Odoo server, to turn Word documents into PDF with their Word layout.
   * - Browser
     - A current version of Chrome or Edge.
   * - AI (optional)
     - Your own API key from Google (Gemini), OpenAI or Anthropic, billed by them. Or an OpenAI-compatible server
       you run yourself, such as Ollama or vLLM, which needs no key and keeps contracts on your network.


Installation
============

#. Copy every folder of the NextContract package into a directory on your Odoo addons path. The Word editor is
   included in the ``nextcontract`` module; no other module is needed.
#. Install the Python packages::

      pip install -r requirements.txt

#. Install LibreOffice on the Odoo server (Debian/Ubuntu)::

      apt-get install -y libreoffice-writer-nogui fonts-liberation fonts-dejavu-core

#. Restart Odoo.
#. Go to **Apps**, remove the *Apps* filter, search for **NextContract** and click **Activate**.

**Result:** the **NextContract** app appears on the Odoo home screen. Installing it installs the whole suite.

.. important::
   **Behind a reverse proxy** (nginx, Apache, a load balancer), the Word editor needs:

   - ``.wasm`` files served with the type ``application/wasm``;
   - same-origin framing allowed: ``X-Frame-Options: SAMEORIGIN``, not ``DENY``;
   - if you set a Content-Security-Policy, ``'wasm-unsafe-eval'`` allowed for scripts;
   - HTTPS. The copy button in the editor's Tags panel only works over HTTPS.


Configuration
=============

User roles
----------

#. Go to **Settings ‣ Users & Companies ‣ Users** and open the user.
#. On the **Access Rights** tab, choose a role in the **NextContract** field.

.. figure:: https://apps.odoocdn.com/apps/assets/19.0/nextcontract/doc_user_roles.png
   :alt: The NextContract role on a user's Access Rights tab
   :width: 100%

   Choose the user's NextContract role on the Access Rights tab.

.. list-table::
   :class: table table-bordered table-striped
   :header-rows: 1
   :widths: 28 72

   * - Role
     - What the user can do
   * - Read-Only User
     - Read contracts that are not restricted. No changes, no approvals.
   * - Business Requester
     - Create and submit requests. See their own contracts, their department's, and contracts that are not
       restricted.
   * - Procurement User
     - Requests, plus procurement and vendor contracts and their supporting documents.
   * - Finance User
     - Sees contract values, and manages obligations on the contracts they can see.
   * - Approver / Management
     - Approves, rejects or delegates the tasks assigned to them. Sees contract values and dashboards.
   * - Legal Reviewer
     - Screens requests, reviews and negotiates contracts, rates risk, prepares signature and activates contracts.
       Sees every contract of the company.
   * - Legal Admin
     - Everything above, plus configuration, templates (approve, publish, retire), renewals, terminations,
       archiving and the audit trail.

.. note::
   **Contract value** is visible only to Legal Admin, Legal Reviewer, Finance User and Approver / Management.
   **Risk rating** is visible only to Legal. Users only see contracts of the companies they are allowed to access.

Contract types and fields
-------------------------

**NextContract ‣ Configuration ‣ Contract Types** already lists the common types: NDA, Services Agreement, Supply
Agreement, Software/SaaS Agreement, Lease Agreement and more. Add your own as needed.

**NextContract ‣ Configuration ‣ Contract Fields** adds your own fields to contracts, such as Governing Law,
Limitation of Liability or Payment Milestones. A field can apply to all contract types or only to some. The values
are filled in on the contract's **Additional Fields** tab, and each field's code can be used as a placeholder in
your templates.

Approval rules
--------------

**NextContract ‣ Configuration ‣ Workflow Rules** decides which approval tasks are created, and when. Two rules
come ready to use:

- **Legal Review Approval**, created when a contract is submitted for review;
- **Management Approval per DOP**, created when the legal review is complete.

You can limit a rule to a contract type or a minimum value, make it optional, or assign it to the Legal Admin.
A contract moves on only when the required tasks of its current step are done.

Activation checklist
--------------------

**NextContract ‣ Configuration ‣ Activation Checklist** lists what must be filled in before a contract can go live:
counterparty, type, owners, currency, dates, notice period, confidentiality, risk rating, the signed copy and the
executed version. Switch off what your process does not need.

Notifications and alerts
------------------------

**NextContract ‣ Configuration ‣ Notifications** decides who is told about which contract events. When a contract
goes live, alerts are created for its key dates: expiry, renewal, notice period and others. They run on a schedule.
**NextContract ‣ Configuration ‣ Process Contract Alerts** runs them immediately.

AI features (optional)
----------------------

The AI assistant is optional. NextContract works fully without it, and nothing is sent to an AI service until an
administrator has connected one. What the assistant does, and how people use it, is described in
`The AI assistant`_. This section covers the setup.

Where NextContract uses AI
~~~~~~~~~~~~~~~~~~~~~~~~~~

AI only runs when someone asks for it, in these places:

.. list-table::
   :class: table table-bordered table-striped
   :header-rows: 1
   :widths: 30 44 26

   * - Where
     - What the AI does
     - Who
   * - Contract version ‣ **Extract via AI**
     - Reads the version's document. Suggests the contract's key data, or compares its clauses with your library,
       or both.
     - Legal
   * - **NextContract ‣ Extraction Reviews**
     - Nothing new: this is the queue where people check, correct and confirm what the AI suggested.
     - Legal
   * - Contract ‣ **Risk Assessment**
     - Clause deviations that Legal confirmed as *High* or *Critical* become risk factors.
     - Legal
   * - Migration batch ‣ **Run AI Extraction**
     - Fills in missing data on imported contracts, in the background.
     - Legal Admin
   * - **Configuration ‣ Contract Fields**
     - Not a run: decides which of your own fields the AI looks for, and gives it hints.
     - Legal Admin
   * - **Configuration ‣ AI Providers** and **Settings**
     - Not a run: the connections, models, limits and consent described below.
     - Legal Admin

The AI never acts on its own. It does not run on new contracts automatically, and it never changes a contract
until a person confirms its suggestions.

Choose an AI provider
~~~~~~~~~~~~~~~~~~~~~

You bring your own provider and key. NextContract adds no AI charge; the provider bills you directly. You can
connect several providers and pick one for each run, for example a cloud model for everyday work and a local one
for confidential contracts.

.. list-table::
   :class: table table-bordered table-striped
   :header-rows: 1
   :widths: 22 26 52

   * - Provider type
     - Where contract content goes
     - Good to know
   * - **Google Gemini**
     - Google
     - Reads long and scanned documents well. Needs the ``google-genai`` Python package.
   * - **OpenAI**
     - OpenAI
     - Use a model that reads images (most current models do) if you analyse scanned PDFs.
   * - **Anthropic Claude**
     - Anthropic
     - Set **Max Output Tokens** high enough for long extractions (default 8192).
   * - **Custom (OpenAI-compatible)**
     - Your own server, or the service you point it to
     - Ollama, vLLM, LM Studio, OpenRouter or an internal AI gateway. A server on your own network keeps contracts
       in-house and needs no consent (see `Keep contracts on your own network`_).

Step 1: Switch on AI and give consent
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Who:** Legal Admin, or any administrator.

#. Go to **Settings ‣ NextContract**.
#. Tick **Enable AI Features**. The **AI Providers** setting appears.
#. If you will use a provider hosted by a third party (Gemini, OpenAI, Claude, or any cloud service), click
   **Acknowledge** under **Cloud Processing Consent**. Odoo records who consented and when, for this company.
   **Revoke** withdraws consent at any time.
#. Optionally, adjust the review thresholds and document limits, described below.
#. Click **Save**.

.. figure:: https://apps.odoocdn.com/apps/assets/19.0/nextcontract/doc_ai_settings.png
   :alt: The NextContract AI settings
   :width: 100%

   AI settings. **Manage AI Providers** opens the list of connections. Consent is recorded per company, with the
   name of the person who gave it and the date.

.. warning::
   Make sure your organisation allows contract content to be processed by the provider before you acknowledge.
   Consent covers every third-party provider of the company.

Step 2: Add a provider
~~~~~~~~~~~~~~~~~~~~~~

**Who:** Legal Admin, or any administrator.

#. Go to **NextContract ‣ Configuration ‣ AI Providers** (or click **Manage AI Providers** in Settings) and click
   **New**.
#. Give it a **Name** people will recognise, for example *OpenAI (Legal team)*, and choose its **Type**.
#. Paste the **API Key**. Leave **Base URL** empty to use the vendor's standard address.
#. Save, then click **Fetch Models**. NextContract asks the provider which models your key can use and lists them
   on the **Models** tab. You can also type a model ID by hand, exactly as the vendor writes it.
#. Choose a **Default Model**, and optionally a **Fallback Model**.
#. Click **Test Connection**. A green message confirms the provider answered, and which model replied. The test
   sends one short sentence and no contract content, so it works before consent is given.

.. figure:: https://apps.odoocdn.com/apps/assets/19.0/nextcontract/doc_ai_providers.png
   :alt: The AI Providers list with Gemini, OpenAI, Claude and a local Ollama server
   :width: 100%

   Four providers side by side. *Third party* shows which ones send contract content outside your network; the
   on-premise Ollama server does not. Drag the handle to change the order: the first provider is offered by default.

.. figure:: https://apps.odoocdn.com/apps/assets/19.0/nextcontract/doc_ai_provider_form.png
   :alt: An OpenAI provider with its connection, behaviour and models
   :width: 100%

   An OpenAI provider. **Test Connection** and **Fetch Models** are at the top; the blue note reminds you that
   content sent to this provider requires the company's consent.

.. list-table::
   :class: table table-bordered table-striped
   :header-rows: 1
   :widths: 26 74

   * - Field
     - What it does
   * - Type
     - Which kind of API the provider speaks: Google Gemini, OpenAI, Anthropic Claude, or Custom (OpenAI-compatible).
   * - API Key
     - Your key. Stored on the provider and visible only to Legal Admins and administrators. Not needed for a local
       server without authentication.
   * - Base URL
     - The API address. Empty means the vendor's standard address. Required for Custom, for example
       ``http://ollama:11434/v1``.
   * - Default Model
     - Pre-selected when someone runs an analysis with this provider. They can pick another model of the list.
   * - Fallback Model
     - Tried once, automatically, when the chosen model is still overloaded after its retries. Choose a different
       model, one that is unlikely to be busy at the same time. The run records which model actually answered.
   * - Structured Output
     - OpenAI and Custom only. How the model is asked to answer in a fixed format. Keep *JSON Schema*; choose
       *JSON object* or *Prompt only* if a self-hosted server rejects it.
   * - Reads Images
     - Untick for a text-only model. Such a model cannot read scanned documents that have no text layer.
   * - Hosted by a third party
     - Custom only. Untick only for a server you operate on your own network (see below).
   * - Max Output Tokens
     - Anthropic only. The maximum length of the reply. Raise it if long extractions come back cut off.
   * - Models
     - The models people can choose. Switch a model off to hide it without deleting it.

**Result:** the provider can be chosen in **Extract via AI** and in migration imports.

Keep contracts on your own network
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

To analyse contracts without sending them to an outside company, run an open model on your own server with Ollama,
vLLM or LM Studio, and connect it as a **Custom** provider:

#. Create a provider with **Type** *Custom (OpenAI-compatible)*.
#. Enter the server's **Base URL** as the Odoo server sees it, ending in ``/v1``, for example
   ``http://ollama.internal:11434/v1``. With Docker, ``localhost`` means the Odoo container itself, so use the
   server's host name.
#. Leave **API Key** empty unless your server asks for one.
#. Untick **Hosted by a third party**. The blue consent note disappears: content stays on your network, so no
   consent is needed for this provider.
#. Add the model ID exactly as the server names it, for example ``llama3.1:70b``, or click **Fetch Models**.
#. Click **Test Connection**.

.. tip::
   Local models vary much more than hosted ones. Use a large, recent model, and a vision model if you analyse
   scans. If the server rejects requests or the answers cannot be read, set **Structured Output** to
   *JSON object*.

.. note::
   Only untick **Hosted by a third party** for a server you operate. A hosted service such as OpenRouter, or a
   gateway run by someone else, is a third party: leave it ticked so consent is enforced.

Other AI settings
~~~~~~~~~~~~~~~~~

These are in **Settings ‣ NextContract** and apply to every provider.

.. list-table::
   :class: table table-bordered table-striped
   :header-rows: 1
   :widths: 30 70

   * - Setting
     - What it does
   * - Enable AI Features
     - Shows the AI provider settings. What actually allows the AI to run is a configured provider and, for a
       third-party provider, the company's consent.
   * - AI Providers
     - **Manage AI Providers** opens the providers list (`Step 2: Add a provider`_).
   * - Gemini API Key (legacy) / Gemini Model (legacy)
     - The single Gemini key used by earlier versions. It is used only when a Gemini run has no provider. Use a
       provider instead; when you upgrade, an existing key is copied into a provider called *Google Gemini*
       automatically.
   * - Review Thresholds
     - The **high** and **low** confidence limits (default 95 and 80). They decide how each suggestion is
       presented to the reviewer (see `Step 3: Review the suggested field values`_).
   * - Document Limits
     - The maximum number of pages per document (default 300), and the size in MB above which a document is
       uploaded to the provider first instead of being sent with the request (default 15). A document over the
       page limit is refused rather than read in part.
   * - Generate Searchable Text Layer
     - For scanned documents, also transcribes each page. It makes the text searchable but uses many more tokens,
       so leave it off unless you need it.
   * - Cloud Processing Consent
     - Required before contract content is sent to any third-party provider. Recorded per company.

Choose which contract fields the AI fills in
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The AI always looks for the contract's title, dates, value, currency, counterparty and contract type. It can also
fill the extra fields you defined in **NextContract ‣ Configuration ‣ Contract Fields**:

#. Open a field and keep **AI Extractable** ticked.
#. Under **AI Extraction Hint**, tell the AI exactly what to take and what to ignore, for example *The notice period
   for termination for convenience, in days. Ignore the cure period in the breach clause.*

A clear hint is the best way to improve the AI's answers for your contracts.


Your first contract, step by step
=================================

A contract moves through these statuses. The status bar at the top of every contract shows where it is, and the
buttons next to it show what can be done now.

*Draft Request* → *Under Legal Review* → *Internal Review Completed* → *Under Negotiation* →
*Approved for Signature* → *Pending Signature* → *Executed* → *Active*

Step 1: Request a contract
--------------------------

**Who:** anyone with a NextContract role who needs a contract.

#. Go to **NextContract ‣ Intake ‣ Contract Requests** and click **New**.
#. Fill in what you know: title, counterparty (and who signs for them), contract type, department, purpose,
   estimated value, start date and urgency. Attach any supporting documents.
#. Click **Submit Request**.

.. figure:: https://apps.odoocdn.com/apps/assets/19.0/nextcontract/doc_request_submitted.png
   :alt: A submitted contract request
   :width: 100%

   A submitted request. The status bar shows where it is; Legal sees the buttons to screen it.

**Result:** the request gets a reference such as ``REQ-2026-00144``, and Legal sees it in the list of requests.

Step 2: Screen the request (Legal)
----------------------------------

**Who:** Legal Reviewer or Legal Admin.

#. Open the request and click **Start Legal Intake**.
#. Check the details and documents, then click **Accept Request**, or **Reject Request** with a reason.
#. On an accepted request, click **Convert to Contract**.

.. figure:: https://apps.odoocdn.com/apps/assets/19.0/nextcontract/doc_requests.png
   :alt: The list of contract requests with their status
   :width: 100%

   All requests at a glance, with urgency and status.

**Result:** a contract is created with the request's details, with a reference such as ``CNT-2026-00771``. You can
also create contracts directly in **NextContract ‣ Contracts ‣ All Contracts**.

Step 3: Prepare the template
----------------------------

**Who:** Legal Admin. Do this once per type of contract; every contract of that type then reuses it.

Templates and clauses live in **NextContract ‣ Library**. **Clauses** hold your standard wording. A **template**
combines a contract type, its clauses and a **Document Template**: the Word file used to produce contracts.

#. Go to **NextContract ‣ Library ‣ Templates**, click **New**, and give the template a name and contract type.
#. On the **Document Template** tab, click **Upload** and choose your ``.docx`` contract. The first file is kept as
   the original and is never overwritten.
#. Put placeholders where the contract's data should appear, for example ``{{ partner_name }}`` or
   ``{{ effective_date }}``. The **Placeholders** panel lists them all (see `Placeholder reference`_).
#. Click **Insert Clause Collection** to add the template's clauses, and **Insert signature block** for a signature
   section for both parties.
#. Click **Validate**, then **Save Changes**, or **Save as New Template Version**.
#. Click **Submit for Review**, then **Approve**, then **Publish**.

.. figure:: https://apps.odoocdn.com/apps/assets/19.0/nextcontract/doc_template.png
   :alt: The Document Template tab with template versions and the placeholders panel
   :width: 100%

   The Document Template tab. The table lists the template's versions; the active one is used for new contracts.
   Below it, the Placeholders panel and a preview of the Word document.

To edit the Word file without leaving Odoo, click **Word Editor** on the **Document Template** tab.

.. figure:: https://apps.odoocdn.com/apps/assets/19.0/nextcontract/doc_word_editor.png
   :alt: The Word editor with the Tags panel
   :width: 100%

   The Word editor. The headings are listed on the left, and the Tags panel on the right lists every placeholder.
   Click a tag to copy it, then paste it into the document.

- **Save** (or Ctrl+S) saves onto the active version. If that version is the original, or a contract already
  uses it, a new version is created instead and you are told.
- **Save as New Version** always creates a new version and makes it active.
- **Check Tags** checks the placeholders without saving.
- If someone else saved the template after you opened it, your save is refused. Nothing is overwritten. Use
  **Download my copy** to keep your edits, or **Reload latest**.

**Result:** the template is *Published* and can be chosen on contracts. Contracts already drafted keep the version
they were drafted with.

Step 4: Draft the contract
--------------------------

**Who:** Legal.

#. Open the contract and choose a published **Contract Template**.
#. Click **Generate Draft** and choose Word (DOCX), PDF or a plain text preview.
#. Change the wording for this contract with **Edit Contract**. Keep a new version with **Create New Version**.
#. **Preview Contract**, **Generate DOCX** and **Generate PDF** produce the current version at any time.

.. figure:: https://apps.odoocdn.com/apps/assets/19.0/nextcontract/doc_contract_draft.png
   :alt: A draft contract with its actions
   :width: 100%

   A draft contract. The buttons show what can be done at this step.

**Result:** the draft is stored on the **Document Versions** tab, and in **NextContract ‣ Contracts ‣ Contract
Versions**. Each version has a type (Internal Draft, Counterparty Draft, Signature Draft and so on).
**Activate Version** makes a version the working one.

Step 5: Review and approve
--------------------------

**Who:** Legal, the approvers, and Finance, Procurement or Compliance when Legal asks for their input.

#. On the contract, click **Submit for Review**. The **Legal Review Approval** task appears on the
   **Workflow Tasks** tab.
#. If needed, ask other teams with **Request Business Input**, **Request Finance Input**,
   **Request Procurement Input** or **Request Compliance Review**.
#. When the legal review is done, click **Complete Internal Review**. This creates the management approval task.

.. figure:: https://apps.odoocdn.com/apps/assets/19.0/nextcontract/doc_contract_review.png
   :alt: A contract under legal review with its workflow tasks
   :width: 100%

   A contract under legal review. Its tasks are on the Workflow Tasks tab.

Approvers do not need to search for contracts. Their tasks wait in **NextContract ‣ Workflow ‣ My Tasks**, where
they click **Approve**, **Reject** or **Delegate**.

.. figure:: https://apps.odoocdn.com/apps/assets/19.0/nextcontract/doc_my_tasks.png
   :alt: The My Tasks list with approve and reject buttons
   :width: 100%

   My Tasks: every review and approval waiting for you, with Approve and Reject on each line.

**Result:** when the management approval is given, the contract is *Approved for Signature*. To send it back,
use **Return for Clarification**; to stop it, **Cancel Contract**.

Step 6: Negotiate with the counterparty
---------------------------------------

**Who:** Legal. The counterparty uses the Odoo portal; they need no Odoo licence.

.. important::
   **Give the counterparty's contact portal access first.** In **Contacts**, open the contact, then use
   **Action ‣ Grant portal access**. A counterparty who is given portal access only after the contract is sent does
   not see the contract.

#. On the contract (or on one of its draft versions), click **Send to Counterparty**. Check the version and the
   counterparty, add a response deadline and a note, then click **Send for Negotiation**.
#. The counterparty signs in to the portal and opens **Your Contracts**. They can read and download the shared
   version, and upload a revised document with comments.
#. Each exchange is a negotiation round in **NextContract ‣ Negotiation ‣ Negotiation Rounds**.
#. On a version, **Compare Versions** or **Compare with previous** shows a redline of the changes. Comparisons are
   kept in **NextContract ‣ Negotiation ‣ Version Comparisons**.

.. figure:: https://apps.odoocdn.com/apps/assets/19.0/nextcontract/doc_send_counterparty.png
   :alt: The Send to Counterparty dialog
   :width: 70%

   Sending a version to the counterparty, with a response deadline and a note.

.. figure:: https://apps.odoocdn.com/apps/assets/19.0/nextcontract/doc_portal_contract.png
   :alt: The counterparty's view of the contract in the Odoo portal
   :width: 85%

   What the counterparty sees in the portal: the shared version, and a form to upload their revision.

**Result:** the round is recorded with its deadline, and each revision is kept as a contract version.

Step 7: Sign
------------

**Who:** Legal sends; the signers sign with Odoo Sign.

#. Click **Prepare Signature Draft**. The final version becomes the *Signature Draft*. It must be active and have a
   document attached.
#. Click **Send for Signature**. Every signer needs a valid email address.
#. Follow progress on the **Signature & Execution** tab.

.. figure:: https://apps.odoocdn.com/apps/assets/19.0/nextcontract/doc_contract_signature.png
   :alt: An approved contract ready to be sent for signature
   :width: 100%

   An approved contract: Send for Signature routes it to Odoo Sign.

If the contract was signed on paper or outside Odoo, click **Mark as Manually Signed** instead. Upload the signed
PDF, enter the signing date and signatory, and confirm that the document is fully executed.

**Result:** when everyone has signed, the contract is *Executed*. The **Final Contract** tab has the signed contract,
with **Download PDF** and **Download DOCX**.

Step 8: Activate
----------------

**Who:** Legal.

#. Open the **Activation Checklist** tab and fill in anything still missing, usually the effective, expiry and
   signing dates.
#. Click **Activate Contract**.

.. figure:: https://apps.odoocdn.com/apps/assets/19.0/nextcontract/doc_activation_checklist.png
   :alt: The activation checklist of a contract
   :width: 100%

   The Activation Checklist shows what is still missing before the contract can go live.

**Result:** the contract is *Active*, and alerts for its key dates are created.

Step 9: Manage the live contract
--------------------------------

**Who:** Legal, Finance and the contract's owners.

- **Obligations**: payments, deliverables, SLAs, notices and renewals, each with an owner and a due date. Mark
  them **Complete** when done; overdue ones raise alerts.
- **Risk Assessment**: add risk factors and click **Recalculate Risk** to update the score and rating.
- **Alerts**: expiry, renewal, notice period, bank guarantee, insurance and other dates.
- **Create Amendment** starts a change to a signed contract. It goes through review and signature again; the signed
  version stays locked.
- **Renew** creates the renewed contract (recommended) or renews this one in place, with new dates and terms.
- **Terminate** records the date and reason and stops the alerts. **Mark Expired** and **Archive** close the
  contract.

.. figure:: https://apps.odoocdn.com/apps/assets/19.0/nextcontract/doc_obligations.png
   :alt: The obligations of an active contract
   :width: 100%

   Obligations on an active contract, each with its type, owner, due date and priority.

Click **View Lifecycle** on any contract to see its whole journey: which steps are done, skipped, current or to
come, who it is waiting on, and the overall progress.

.. figure:: https://apps.odoocdn.com/apps/assets/19.0/nextcontract/doc_lifecycle.png
   :alt: The lifecycle overview of a contract
   :width: 100%

   The lifecycle overview of an active contract.


The AI assistant
================

What it is for
--------------

A contract is long, and the facts that matter are scattered through it: who the parties are, when it starts and
ends, what it is worth, and whether the wording is the wording you agreed to use. Finding them means reading every
page and typing the answers into the system.

The **AI assistant** does the first reading for you. It reads a contract document and gives you two kinds of
suggestion:

.. list-table::
   :class: table table-bordered table-striped
   :header-rows: 1
   :widths: 24 38 38

   * - Analysis
     - What the AI does
     - What you get
   * - **Field extraction**
     - Reads the document and finds the contract's key data.
     - Suggested values for the title, effective and expiry dates, contract value, currency, counterparty and
       contract type, and for any of your own contract fields. Each comes with a confidence score, the page it
       was found on and the sentence it was taken from.
   * - **Clause analysis**
     - Compares each clause in the document with the standard wording in your clause library.
     - A classification (*Standard*, *Approved Alternative*, *Modified*, *Non-Standard* or *Rejected*), a risk level
       from *Low* to *Critical*, and a plain-language explanation of what is different.
   * - **Full analysis**
     - Both of the above in one run.
     - Everything above.

It saves the most time on:

- **drafts sent by the counterparty**, where you need to know quickly what differs from your standard terms;
- **contracts that already exist** and have to be entered into NextContract, one by one or as an import
  (`Import existing contracts`_);
- **any signed document** whose data nobody has typed in.

The AI suggests, people decide
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

AI can misread a document, so NextContract never lets it change a contract on its own.

- **Nothing is written until a Legal Reviewer confirms.** Suggestions are held in a review queue, and the
  contract stays as it was until then.
- **Every suggestion shows its evidence**: the page and the exact text it came from, so a reviewer can check it in
  seconds.
- **Doubtful answers are not hidden.** Low-confidence suggestions block the confirmation until someone has
  looked at them.
- **The AI can only suggest values for a short list of fields**: title, dates, value, currency, counterparty,
  contract type and your own contract fields. It cannot change approvals, signatures or statuses.
- **It is traceable.** Each run records who started it, which provider and model read the document, who gave
  consent, who reviewed it and when, and how many tokens it used.
- **It is off until you switch it on.** No contract content leaves Odoo until an administrator has connected a
  provider and, for a third-party provider, given consent (`AI features (optional)`_). With a provider on your own
  network, nothing leaves it at all.

Step 1: Start an analysis
-------------------------

**Who:** Legal Reviewer or Legal Admin.

#. Open the contract, go to the **Document Versions** tab and open the version to analyse. The version needs a
   document; upload or generate one first.
#. Click **Extract via AI**.

.. figure:: https://apps.odoocdn.com/apps/assets/19.0/nextcontract/doc_ai_extract_button.png
   :alt: The Extract via AI button on a contract version
   :width: 100%

   The **Extract via AI** button, next to the other actions of a contract version.

#. Choose the **Analysis Type**: *Field Extraction*, *Clause Analysis* or *Full Analysis (Fields + Clauses)*.
#. Choose the **AI Provider**. The first provider of the list is proposed.
#. Check the **Model**. It starts with the provider's default model; pick another one of the provider's models if
   you need to.
#. Click **Run AI Analysis**. The analysis job opens, with the status *Ready*.

.. figure:: https://apps.odoocdn.com/apps/assets/19.0/nextcontract/doc_ai_wizard.png
   :alt: The Extract via AI dialog with analysis type, provider and model
   :width: 70%

   Choose what the AI should look for, which provider reads the document, and with which model.

#. Check the job, then click **Run Analysis**.

.. figure:: https://apps.odoocdn.com/apps/assets/19.0/nextcontract/doc_ai_job_run.png
   :alt: A ready AI analysis job with the Run Analysis button
   :width: 100%

   A job that is *Ready*. Nothing is sent to the provider until you click **Run Analysis**.

**Result:** the AI reads the document while you wait. It takes from a few seconds to a few minutes, depending on
the length of the document and on the model. The job then shows *Completed*, and its suggestions wait in the review
queue. If the model is overloaded and a **Fallback Model** is set on the provider, the fallback is tried once,
automatically.

Step 2: Follow the analysis
---------------------------

Every run is listed in two places:

- on the version, in the **AI Analysis Jobs** tab;
- for reviewers, in **NextContract ‣ Extraction Reviews**, the queue of all runs.

.. figure:: https://apps.odoocdn.com/apps/assets/19.0/nextcontract/doc_ai_queue.png
   :alt: The Extraction Reviews queue
   :width: 100%

   The Extraction Reviews queue. *Status* tells you whether the AI has finished; *Review State* tells you whether a
   person has dealt with the result yet.

A job you start yourself goes from *Ready* to *Processing*, then to *Completed* or *Failed*. Jobs started in bulk,
by **Run New Extraction** or by a migration import, are *Pending* first: a background worker picks them up, usually
within a few minutes, so you can keep working.

When a job fails, the reason is shown on the job, for example missing consent, a document that is too long or
unreadable, or an error from the provider (see `Troubleshooting`_). Fix the cause, then click **Retry Analysis**
on the job.

Step 3: Review the suggested field values
-----------------------------------------

**Who:** Legal Reviewer or Legal Admin.

Open a completed run in **NextContract ‣ Extraction Reviews**. The top of the page shows which provider and model
read the document, how many pages it read, and the consent under which the document was sent. A banner says
whether the run is *Ready to confirm*, or what still needs attention.

.. figure:: https://apps.odoocdn.com/apps/assets/19.0/nextcontract/doc_ai_review.png
   :alt: An extraction review with one value that needs attention
   :width: 100%

   An extraction review. The banner says the run is not ready to confirm: one value came back with low confidence.

The suggestions are sorted into three tabs by confidence, using the thresholds set in
`AI features (optional)`_:

.. list-table::
   :class: table table-bordered table-striped
   :header-rows: 1
   :widths: 22 30 48

   * - Tab
     - Confidence
     - What to do
   * - **Needs Attention**
     - Below the low threshold, or a value that must be matched to an existing record
     - Check each one. Open it to read the source text, then click **Approve** if it is right, or **Reject** if it
       is not. When a value such as a counterparty or contract type must be matched to a record in Odoo, click
       **Resolve** (or **Map / Resolve**) and choose the right record. **The review cannot be confirmed until this
       tab is empty.**
   * - **Review**
     - Between the two thresholds
     - Probably right, but worth a look. Correct the suggested value in place if needed.
   * - **Verified**
     - At or above the high threshold
     - Very likely right. They are shown in full, with their source text, so you can still check them.

.. figure:: https://apps.odoocdn.com/apps/assets/19.0/nextcontract/doc_ai_review_mid.png
   :alt: The Review tab, with the source text next to each suggested value
   :width: 100%

   The **Review** tab. Each value is shown with the page and the sentence it was read from.

.. figure:: https://apps.odoocdn.com/apps/assets/19.0/nextcontract/doc_ai_review_verified.png
   :alt: The Verified tab
   :width: 100%

   The **Verified** tab: values the AI is very confident about.

Three more tabs help you understand a run: **Source Document** shows the text the AI read, **Field Configuration
Used** shows which fields were requested and with which hints, and **Cost** shows the input and output tokens the
provider reported. Your provider's billing page shows the actual charge.

.. note::
   A contract field marked as *required* must have a value, either from the AI or already on the contract. If it
   has neither, the banner tells you how many required fields are missing.

Step 4: Confirm and apply
-------------------------

When the banner turns green (*Ready to confirm*):

#. Click **Confirm & Apply**. Every suggestion that is still pending is written to the contract, and the run is
   marked *Reviewed* with your name and the date.
#. Or click **Reject Extraction** to discard the whole run. The contract is not changed.

**Result:** the contract's fields are filled in, and the run stays in the queue as a record of what was suggested
and who accepted it. To try again, for example after improving a field's hint, click **Run New Extraction**. It uses
the same provider and model, and runs in the background. Suggestions of the earlier run that nobody decided on are
replaced by the new ones.

.. tip::
   Reject any single value you do not want *before* you click **Confirm & Apply**: everything still pending is applied.

Step 5: Review clause deviations
--------------------------------

**Who:** Legal Reviewer or Legal Admin.

For *Clause Analysis* and *Full Analysis*, open the run from the version's **AI Analysis Jobs** tab and go to the
**Clause Deviations** tab. It lists every clause the AI compared with your library, with its classification and
risk.

.. figure:: https://apps.odoocdn.com/apps/assets/19.0/nextcontract/doc_ai_deviations.png
   :alt: The list of clause deviations found by the AI
   :width: 85%

   Clause deviations. The AI rates the standard confidentiality clause *Low*, a longer payment term *Medium* and
   uncapped liability *High*.

Click **Review** on a line to open it:

- **Text Comparison** puts your *Library Standard Wording* next to the *Wording in This Version*.
- **Classification & Risk** shows the AI's classification, risk level, confidence and explanation, which you cannot
  edit, next to the **Legal Confirmation**, which you fill in.

.. figure:: https://apps.odoocdn.com/apps/assets/19.0/nextcontract/doc_ai_deviation_compare.png
   :alt: A clause shown next to its standard wording
   :width: 85%

   The standard wording and the counterparty's wording, side by side.

.. figure:: https://apps.odoocdn.com/apps/assets/19.0/nextcontract/doc_ai_deviation_risk.png
   :alt: The AI's classification of a clause, and where Legal records its own
   :width: 85%

   The AI's suggestion on the left, and Legal's confirmed classification on the right.

Click **Confirm Classification** to accept the AI's view, or change the classification, risk level and rationale
first. The confirmed values are the authoritative ones; the AI's suggestion is kept for reference.

**Result:** deviations you confirmed as *High* or *Critical* are added as risk factors on the contract's **Risk
Assessment** tab the next time you click **Recalculate Risk**, if they belong to the contract's active version.

Getting the best results
------------------------

- **Use the original file.** A Word file or a PDF with selectable text is read most accurately. Scans work, but
  the quality of the scan matters.
- **Write good hints.** For each contract field, an **AI Extraction Hint** that says what to take and what to
  ignore makes the biggest difference (`Choose which contract fields the AI fills in`_).
- **Keep your clause library current.** Clause analysis compares with the standard wording of your **published**
  clauses (up to 50). With no published clause, it has nothing to compare with and is skipped.
- **Match the analysis to the need.** Use *Field Extraction* for data entry and *Clause Analysis* for incoming
  drafts. *Full Analysis* reads the document once for both.
- **Match the model to the work.** A stronger model pays off on clause analysis of complex agreements; a faster,
  cheaper one is often enough for field extraction and bulk imports. Create one provider per need, or keep several
  models on one provider and choose per run.
- **Set a fallback model.** Busy periods at a provider then cost you a short delay instead of a failed run.
- **Keep an eye on cost.** The **Cost** tab of each run shows the tokens used. Long documents, and the optional
  searchable text layer, use more.

More features
=============

Reports and Excel export
------------------------

**NextContract ‣ Dashboards** has the **Contract Portfolio**, **Expiry & Renewal**, **Risk Analysis** and
**Obligation Analysis** reports, as graphs, pivot tables or lists. **Export Excel** exports contracts, requests,
obligations or workflow tasks, filtered by status, dates, contract type or counterparty.

.. figure:: https://apps.odoocdn.com/apps/assets/19.0/nextcontract/doc_dashboard.png
   :alt: The contract portfolio report by contract type
   :width: 100%

   Contract Portfolio: the value of your contracts by contract type.

AI extraction and clause review
-------------------------------

Extraction of contract data and comparison with your clause library are described in full in `The AI assistant`_.
They need the `AI features (optional)`_ to be set up first.

Purchase orders
---------------

The contract's **Purchase Orders** tab lists its purchase orders, and each
purchase order shows its contract.

Import existing contracts
-------------------------

Legal Admins can bring existing contracts in through **NextContract ‣ Configuration ‣ Migration ‣ Migration
Batches**.

#. Prepare a ZIP file with ``contracts.csv`` (or ``contracts.xlsx``) and the contract PDFs. The columns are:
   *Vendor Name*, *Contract Title*, *Contract Number*, *Effective Date*, *Expiry Date*, *Execution Date*,
   *Contract Value*, *Legacy Source Reference* and *Document Path* (the PDF's path inside the ZIP).
#. Create a batch, upload the ZIP and click **Parse File**.
#. Click **Run Deduplication & Validation** and check the **Staging Records**.
#. Click **Import Valid Records**.
#. Optionally, click **Run AI Extraction** to fill in missing data from the PDFs. Choose which contracts to process
   (all of the batch, or only those missing a value or a date), the **AI Provider** and the **Model**. The runs are
   queued and processed in the background; review them in **NextContract ‣ Extraction Reviews**.

The ZIP may be up to 200 MB, or 1 GB once unpacked, with at most 2,000 files. Only PDF documents are accepted.


Frequently asked questions
==========================

**Can we use the Word contracts we already have?**
   Yes. Upload each one as a template's Document Template and replace the variable parts with placeholders.

**What happens to signed or drafted contracts when a template changes?**
   Nothing. A contract keeps the template version it was drafted with. Only new contracts use the new version.

**Can the counterparty change our document?**
   They upload a revised version through the portal. You compare it with yours and decide what to accept.

**Do we have to use Odoo Sign?**
   No. The Sign app must be installed, but you can sign on paper and use **Mark as Manually Signed**.

**Who can see contract values?**
   Legal, Finance and Approvers. Requesters, Procurement and read-only users do not.

**How are we reminded about renewals?**
   When a contract goes live, alerts are created for its expiry, renewal and notice dates. You can also add your own
   obligations with due dates.

**Can we change the approval steps?**
   Yes, in **NextContract ‣ Configuration ‣ Workflow Rules** (see `Approval rules`_).

**Is the AI required?**
   No. Everything works without it. Nothing is sent to an AI provider unless an administrator has connected one
   and, for a third-party provider, given consent.

**Which AI providers can we use?**
   Google Gemini, OpenAI, Anthropic Claude, and any server that offers an OpenAI-compatible API: Ollama, vLLM,
   LM Studio, OpenRouter or your own AI gateway. You can connect several and choose one for each run.

**Can we keep contract data on our own network?**
   Yes. Run an open model on your own server and connect it as a *Custom* provider with **Hosted by a third party**
   unticked (see `Keep contracts on your own network`_). The contract then never leaves your infrastructure.

**Can we try the AI without an API key?**
   Yes, to see the review screens. In **Extract via AI**, clear the **AI Provider** field and choose
   *Mock (Testing)* as **Built-in Provider**. It returns sample suggestions and sends nothing anywhere. The values
   are invented, so reject the run afterwards.

**Will the AI change my contracts by itself?**
   Never. It only makes suggestions. A Legal Reviewer checks them and clicks **Confirm & Apply** before anything is
   written to a contract.

**How do I know if I can trust a suggestion?**
   Each one shows a confidence score, the page and the sentence it was taken from. Low-confidence values must be
   checked before the review can be confirmed.

**What does the AI cost?**
   NextContract adds no charge. Your provider bills the use of your API key; a server you host yourself costs only
   its hardware. The **Cost** tab of each run shows the tokens used.

**What is sent to the AI provider?**
   The document of the version you analyse (or its text) and the list of fields to find. For clause analysis, also
   the standard wording of your published clauses. To a third-party provider, it is sent only after consent has been
   recorded for the company. **Test Connection** sends one short test sentence and no contract content.

**Can the AI read scanned contracts?**
   Yes, but results depend on the quality of the scan. Check the suggestions more carefully, and use the **Source
   Document** tab to see what the AI read.


Placeholder reference
=====================

Write placeholders with double braces in a Document Template, for example ``{{ contract_ref }}``. The codes of your
contract fields work the same way.

.. list-table::
   :class: table table-bordered table-striped
   :header-rows: 1
   :widths: 35 65

   * - Placeholder
     - Value
   * - ``partner_name``, ``partner_address``, ``partner_street``, ``partner_city``, ``partner_zip``,
       ``partner_country``, ``partner_email``, ``partner_phone``, ``partner_vat``
     - Counterparty details
   * - ``company_name``, ``company_address``, ``company_street``, ``company_city``, ``company_zip``,
       ``company_country``, ``company_email``, ``company_phone``, ``company_vat``
     - Your company's details
   * - ``contract_name``, ``contract_ref``, ``contract_type``
     - Contract title, reference and type
   * - ``effective_date``, ``expiry_date``, ``signing_date``, ``renewal_date``, ``today``
     - Dates (``today`` is the date the document is produced)
   * - ``notice_period_days``, ``notice_period``
     - Notice period as a number of days, or as text (for example *30 days*)
   * - ``contract_value``, ``currency``
     - Value and currency. The value is filled in only for people allowed to see it.
   * - ``auto_renewal``, ``termination_for_convenience``
     - *Yes* or *No*
   * - ``business_owner``, ``legal_owner``, ``procurement_owner``, ``finance_owner``, ``department``
     - Owners and department
   * - ``risk_level``, ``risk_rating``, ``confidentiality_level``
     - Risk and classification
   * - ``company_signatory``, ``company_signatory_title``, ``company_signature_label``
     - Your signatory's name and job title, and your party label
   * - ``partner_signatory``, ``partner_signatory_title``, ``partner_signature_label``
     - The counterparty's signatory name and job title, and their party label
   * - ``template_name``, ``template_version``
     - The template used

Clauses are inserted with a loop, which **Insert Clause Collection** writes for you::

   {% for section in sections %}
   {% for clause in section.clauses %}
   {{ clause.title }}
   {{ clause.content }}
   {% endfor %}
   {% endfor %}

Inside the loop you can also use ``clause.category``, ``clause.description``, ``clause.risk_level``,
``clause.editability``, ``clause.required``, ``clause.is_locked``, ``clause.is_restricted`` and
``section.title``.


Contract statuses
=================

.. list-table::
   :class: table table-bordered table-striped
   :header-rows: 1
   :widths: 32 68

   * - Status
     - Meaning
   * - Draft Request
     - Created, not yet submitted for review
   * - Under Legal Review
     - Legal is reviewing. The contract may wait on *Business*, *Finance*, *Procurement* or *Compliance* input.
   * - Internal Review Completed
     - Legal review done; waiting for management approval or negotiation
   * - Sent to Counterparty / Under Negotiation
     - Shared with the counterparty through the portal
   * - Approved for Signature
     - Approved; ready to send for signature
   * - Pending Signature
     - Out for signature with Odoo Sign
   * - Executed
     - Signed by everyone; not yet live
   * - Active / Expiring Soon
     - Live, and followed by alerts and obligations
   * - Renewed / Expired / Terminated / Archived / Cancelled
     - Closed


Upgrading
=========

#. Back up the database and the filestore.
#. Download the new version from your Odoo Apps orders page.
#. Replace the NextContract folders with the new ones. Replace whole folders; do not merge them.
#. If the release notes mention new requirements, run ``pip install -r requirements.txt``.
#. Restart Odoo and upgrade the modules::

      odoo -c <your config> -d <database> -u nextcontract --stop-after-init

   Then start Odoo normally. Or, in **Apps** with developer mode active, open **NextContract** and click
   **Upgrade**.

#. Ask users to refresh their browser once (Ctrl+F5).

Upgrading *NextContract* also upgrades the Word editor, the AI features, purchase-order links and contract migration, which are all part of the one module.


Troubleshooting
===============

.. list-table::
   :class: table table-bordered table-striped
   :header-rows: 1
   :widths: 40 60

   * - Problem
     - Solution
   * - "requires LibreOffice on the Odoo server" when previewing or exporting a PDF
     - Install LibreOffice (see `Installation`_) and restart Odoo.
   * - The Word editor stays blank or does not start
     - Behind a reverse proxy: serve ``.wasm`` as ``application/wasm``, allow ``X-Frame-Options: SAMEORIGIN`` and,
       with a Content-Security-Policy, ``'wasm-unsafe-eval'``. Use Chrome or Edge.
   * - The Word editor looks unchanged after an upgrade
     - Refresh the browser with Ctrl+F5.
   * - The **Word Editor** button is missing
     - It appears once the template has a Document Template. Upload a ``.docx`` on the **Document Template** tab.
   * - "Copy failed" in the Tags panel
     - Copying needs HTTPS. Select the placeholder text and copy it by hand.
   * - "Someone saved a newer Template Version"
     - Someone saved after you opened the editor. Use **Download my copy**, then **Reload latest**, and reapply your
       edits.
   * - The counterparty sees no contract in the portal
     - Their contact needs portal access *before* the contract is sent (see `Step 6: Negotiate with the
       counterparty`_). Send it again after granting access.
   * - "Cloud AI processing is not enabled"
     - Acknowledge cloud processing for the company (see `Step 1: Switch on AI and give consent`_), or use a
       provider on your own network.
   * - "Gemini API Key is not configured", or "needs a configured provider"
     - The run has no provider. Add one in **NextContract ‣ Configuration ‣ AI Providers**
       (see `Step 2: Add a provider`_) and choose it in **Extract via AI**.
   * - **Test Connection** says "Connection failed"
     - The message is the provider's own answer. Check the API key, the **Base URL** (with ``/v1`` for most
       OpenAI-compatible servers) and that the Odoo server can reach the address. A model ID must be written
       exactly as the vendor writes it; **Fetch Models** lists the valid ones.
   * - "A Custom provider needs a Base URL"
     - Enter the server's address, starting with ``http://`` or ``https://``.
   * - An AI run is *Failed* with the reason *Provider Error*
     - The provider did not accept the request. Check that the API key is valid and has quota, and that the model
       is still offered (**Fetch Models**, then choose another model). Set a **Fallback Model** for busy periods.
       Then click **Retry Analysis**.
   * - An AI run is *Failed* with the reason *Invalid Provider Response*
     - The model's answer could not be read. With Anthropic, raise **Max Output Tokens**. With a self-hosted model,
       set **Structured Output** to *JSON object*, or use a larger model.
   * - An AI run is *Failed* with the reason *Document Unreadable*
     - The document is a scan without text and the model cannot read images, or the file is not a PDF, Word or text
       document. Use a model that reads images, and keep **Reads Images** ticked on its provider.
   * - An AI run is *Failed* with the reason *Document Too Long*
     - Split the document, or raise the page limit in **Document Limits** (see `Other AI settings`_).
   * - An AI run stays *Ready*
     - Open it and click **Run Analysis**. Nothing is sent until you do.
   * - An AI run stays *Pending*
     - Bulk runs start within five minutes. If they do not, ask your administrator to check that the scheduled
       action *Process Pending AI Jobs* is active.
   * - **Confirm & Apply** is greyed out
     - The banner says why: open the **Needs Attention** tab and approve, reject or map every value on it
       (see `Step 3: Review the suggested field values`_).
   * - Clause analysis lists nothing
     - Publish clauses in **NextContract ‣ Library ‣ Clauses**. Clause analysis compares with published clauses only.
   * - "Only approved contracts can be sent for signature" or "No active Signature Draft found"
     - Finish the approvals, then click **Prepare Signature Draft** before **Send for Signature**.
   * - "Complete the Activation Checklist first"
     - Open the **Activation Checklist** tab and fill in what is missing.
   * - A user cannot see a contract
     - Check their NextContract role and allowed companies. Restricted contracts are visible only to Legal and to the
       contract's owners.


Data and privacy
================

- Contracts, documents and their versions stay in your Odoo database and filestore.
- **AI features** send contract text and files to the AI provider chosen for the run, with your API key. For a
  third-party provider (Google, OpenAI, Anthropic or any hosted service), only after an administrator has given
  consent for the company; consent can be revoked at any time. A provider on your own network keeps the content
  in-house.
- **API keys** are stored in your database and visible only to Legal Admins and administrators.
- **The Word editor** runs in the browser and saves to your Odoo server. Common fonts are included. Choosing a font
  that is not included may load it from Google Fonts, which receives the font name, not the document.
- **Counterparties** see only the contract versions shared with them through the portal.


Support
=======

Contact us from your order page on the Odoo Apps store. Please include your Odoo version, the NextContract version
(**Apps ‣ NextContract ‣ Module Info**), the steps that lead to the problem and any error message.

