# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import fields, models


_CLM_NOTIFICATION_EVENTS = [
    ('intake_submitted', 'Intake Submitted'),
    ('intake_accepted', 'Intake Accepted'),
    ('intake_rejected', 'Intake Rejected'),
    ('intake_converted', 'Intake Converted to Contract'),
    ('task_assigned', 'Workflow Task Assigned'),
    ('task_approved', 'Workflow Task Approved'),
    ('task_rejected', 'Workflow Task Rejected'),
    ('negotiation_sent', 'Negotiation Round Sent'),
    ('signature_requested', 'Signature Requested'),
    ('contract_activated', 'Contract Activated'),
    ('contract_renewed', 'Contract Renewed'),
    ('contract_terminated', 'Contract Terminated'),
    ('contract_archived', 'Contract Archived'),
    ('contract_expired', 'Contract Expired'),
    ('expiry_alert', 'Expiry / Renewal Alert'),
    ('obligation_overdue', 'Obligation Overdue'),
]


class ClmNotificationRule(models.Model):
    """Per-event on/off switches for NextContract email notifications."""

    _name = 'clm.notification.rule'
    _description = 'NextContract Notification Rule'
    _order = 'sequence, id'

    name = fields.Char(string='Notification', required=True)
    event_code = fields.Selection(
        _CLM_NOTIFICATION_EVENTS,
        string='Event',
        required=True,
        index=True,
    )
    template_xmlid = fields.Char(
        string='Mail Template XML ID',
        required=True,
        help='Technical XML ID of the mail.template used for this event.',
    )
    enabled = fields.Boolean(
        string='Enabled',
        default=True,
        help='Turn this notification email on or off.',
    )
    description = fields.Text(string='Description')
    sequence = fields.Integer(default=10)
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        default=lambda self: self.env.company,
    )

    _event_code_company_uniq = models.Constraint(
        'unique(event_code, company_id)',
        'Only one notification rule is allowed per event and company.',
    )
