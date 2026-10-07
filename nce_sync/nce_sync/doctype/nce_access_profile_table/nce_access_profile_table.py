"""Minimal NCE Access Profile Table child DocType controller for per-table access rows.

code-index:begin
{
 "purpose": "Minimal NCE Access Profile Table child DocType controller for per-table access rows.",
 "read_when": "When adding server-side validation or logic to NCE Access Profile Table child records.",
 "gotchas": [],
 "keywords": ["access", "profile", "table", "nce", "document", "frappe", "nceaccess"],
 "format": 1
}
code-index:end
"""

# Copyright (c) 2026, Oliver Reid and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class NCEAccessProfileTable(Document):
	pass
