# Copyright (c) 2026, Oliver Reid and contributors
# For license information, please see license.txt

"""
Sync gate: block user saves on mirrored DocTypes while a WP→Frappe sync is running.

Three cases for any write to a mirrored DocType:
  1. frappe.flags.in_sync is set  → sync process itself is writing; allow, skip write-back.
  2. Flag not set AND DocType is busy → user edit during sync; frappe.throw so no save occurs.
  3. Flag not set AND not busy        → normal user edit; allow save, write-back will be enqueued.

enforce_sync_gate (validate hook) covers all save paths including Submit.
Desk-specific JS is intentionally absent — frappe.throw renders natively in every client.
"""

import frappe
from frappe import _

BUSY_KEY_PREFIX = "nce_sync:sync_busy:"


def _busy_key(frappe_doctype: str) -> str:
	return f"{BUSY_KEY_PREFIX}{frappe_doctype}"


def mark_doctype_syncing(frappe_doctype: str | None) -> None:
	"""Set the Redis busy flag for a DocType at sync start."""
	if not frappe_doctype:
		return
	frappe.cache().set_value(_busy_key(frappe_doctype), 1, expires_in_sec=900)


def clear_doctype_syncing(frappe_doctype: str | None) -> None:
	"""Clear the Redis busy flag for a DocType at sync end."""
	if not frappe_doctype:
		return
	frappe.cache().delete_value(_busy_key(frappe_doctype))


def is_doctype_syncing(doctype: str | None) -> bool:
	"""True if a WP→Frappe sync is currently running for this DocType."""
	if not doctype:
		return False
	return frappe.cache().get_value(_busy_key(doctype)) is not None


def enforce_sync_gate(doc, method=None):
	"""
	Wildcard validate hook.

	Allow the write if called by the sync process (frappe.flags.in_sync).
	Throw for every other write while the DocType is sync-busy.
	"""
	if getattr(frappe.flags, "in_sync", False):
		return
	if not doc.doctype:
		return
	if not is_doctype_syncing(doc.doctype):
		return
	frappe.throw(
		_("Sync in progress — wait for it to finish, then save again."),
		title=_("Sync in progress"),
	)
