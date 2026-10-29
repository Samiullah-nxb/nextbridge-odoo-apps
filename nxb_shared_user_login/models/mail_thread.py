# -*- coding: utf-8 -*-
# Copyright 2026 Nextbridge. License OPL-1.
from email.utils import formataddr

from odoo import models


class MailThread(models.AbstractModel):
    _inherit = "mail.thread"

    def _message_compute_author(self, author_id=None, email_from=None, raise_on_email=True):
        """Messages written by a shared account carry the signed-in employee as author: chatter messages,
        logged notes, field tracking, Discuss and outgoing e-mails, on every model."""
        if author_id is None and not email_from:
            emp = self.env["res.users"]._nxb_active_employee()
            if emp:
                partner = emp.work_contact_id
                if not partner:
                    emp._create_work_contacts()
                    partner = emp.work_contact_id
                if partner:
                    address = emp.work_email or self.env.user.email
                    return super()._message_compute_author(
                        partner.id, formataddr((emp.name, address)) if address else None, raise_on_email)
        return super()._message_compute_author(author_id, email_from, raise_on_email)
