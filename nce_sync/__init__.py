__version__ = "2.1.2"

import frappe
import frappe.email.queue as _email_queue
from frappe.email.email_body import EMail as _EMail

# ---------------------------------------------------------------------------
# Patch 1: Suppress unsubscribe footer when Email Account has it disabled.
# Frappe treats empty string as falsy and falls back to the default
# "Unsubscribe" text.  This honours the empty string passed by _notify().
# ---------------------------------------------------------------------------
_original_get_unsubscribe_message = _email_queue.get_unsubscribe_message


def _patched_get_unsubscribe_message(unsubscribe_message, expose_recipients):
	if unsubscribe_message and unsubscribe_message.strip():
		return _original_get_unsubscribe_message(unsubscribe_message, expose_recipients)
	return frappe._dict(html="", text="")


_email_queue.get_unsubscribe_message = _patched_get_unsubscribe_message

# ---------------------------------------------------------------------------
# Patch 2: Omit Reply-To header from outgoing emails.
# Frappe v15 forces Reply-To to the session user's email when no incoming
# account exists — even if the From was already replaced by the outgoing
# account.  Clearing reply_to after validate() causes the header-building
# code to skip it, so mail clients reply to the From address.
# Fixed natively in Frappe v16 via PR #36774.
# ---------------------------------------------------------------------------
_original_email_validate = _EMail.validate


def _patched_email_validate(self):
	_original_email_validate(self)
	self.reply_to = ""


_EMail.validate = _patched_email_validate
