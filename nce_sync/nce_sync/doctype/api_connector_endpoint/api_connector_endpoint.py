"""Minimal API Connector Endpoint child DocType controller class for endpoint row records.

code-index:begin
{
 "purpose": "Minimal API Connector Endpoint child DocType controller class for endpoint row records.",
 "read_when": "When adding validation or server-side logic to API Connector Endpoint child rows.",
 "gotchas": [],
 "keywords": ["api connector endpoint", "child doctype", "endpoint", "document", "frappe"],
 "format": 1
}
code-index:end
"""

# Copyright (c) 2026, Oliver Reid and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class APIConnectorEndpoint(Document):
	pass
