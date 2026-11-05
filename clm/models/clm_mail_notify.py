# Part of NextContract. See LICENSE file for full copyright and licensing details.

import logging

from odoo import api, models

_logger = logging.getLogger(__name__)


class ClmMailNotify(models.AbstractModel):
    """Shared helper to send NextContract notification emails without breaking workflows."""

    _name = 'clm.mail.notify'
    _description = 'NextContract Mail Notification Helper'

    @api.model
    def is_enabled(self, template_xmlid=None, event_code=None):
        """Return False if a matching notification rule exists and is disabled."""
        Rule = self.env['clm.notification.rule'].sudo()
        domain = [('company_id', 'in', [False, self.env.company.id])]
        if event_code:
            domain = [('event_code', '=', event_code)] + domain
        elif template_xmlid:
            domain = [('template_xmlid', '=', template_xmlid)] + domain
        else:
            return True
        rule = Rule.search(domain, limit=1)
        if not rule:
            # No config row yet → allow send (safe default for upgrades)
            return True
        return bool(rule.enabled)

    @api.model
    def send(self, records, template_xmlid, email_to=None, force_send=True, event_code=None):
        """
        Send a mail.template for each record.

        Honors NextContract → Configuration → Notifications on/off switches.
        By default force_send=True so emails go out immediately via SMTP.
        """
        if not self.is_enabled(template_xmlid=template_xmlid, event_code=event_code):
            _logger.info(
                'NextContract notification skipped (disabled): template=%s event=%s',
                template_xmlid, event_code,
            )
            return False

        template = self.env.ref(template_xmlid, raise_if_not_found=False)
        if not template:
            _logger.warning('NextContract mail template missing: %s', template_xmlid)
            return False

        sent = False
        for record in records:
            email_values = {}
            if email_to:
                email_values['email_to'] = email_to
            try:
                template.send_mail(
                    record.id,
                    force_send=force_send,
                    email_values=email_values or None,
                )
                if hasattr(record, 'message_post'):
                    record.message_post(
                        body='NextContract notification %s: %s' % (
                            'sent' if force_send else 'queued',
                            template.name,
                        ),
                    )
                sent = True
            except Exception:
                _logger.exception(
                    'NextContract notification failed for %s (id=%s) template=%s',
                    record._name, record.id, template_xmlid,
                )
        return sent

    @api.model
    def emails_for_users(self, users):
        """Return comma-separated emails for active users with an email address."""
        emails = users.sudo().filtered(lambda u: u.active and u.email).mapped('email')
        return ','.join(dict.fromkeys(emails))

    @api.model
    def emails_for_group(self, group_xmlid):
        group = self.env.ref(group_xmlid, raise_if_not_found=False)
        if not group:
            return ''
        return self.emails_for_users(group.sudo().user_ids)

    @api.model
    def emails_for_partners(self, partners):
        emails = partners.sudo().filtered(lambda p: p.email).mapped('email')
        return ','.join(dict.fromkeys(emails))
