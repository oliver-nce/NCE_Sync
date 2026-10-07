"""Minimal Sync Log DocType controller class for sync run records.

code-index:begin
{
 "purpose": "Minimal Sync Log DocType controller class for sync run records.",
 "read_when": "When adding server-side validation or hooks to Sync Log records.",
 "gotchas": [],
 "keywords": ["sync", "log", "document", "frappe", "license", "synclog"],
 "format": 1
}
code-index:end
"""

# Copyright (c) 2026, Oliver Reid and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class SyncLog(Document):
	pass
