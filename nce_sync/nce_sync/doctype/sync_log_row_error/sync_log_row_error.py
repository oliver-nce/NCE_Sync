"""Minimal Sync Log Row Error child DocType controller for row-level sync errors.

code-index:begin
{
 "purpose": "Minimal Sync Log Row Error child DocType controller for row-level sync errors.",
 "read_when": "When adding validation or logic to Sync Log Row Error child records.",
 "gotchas": [],
 "keywords": ["sync", "log", "row", "error", "document", "license"],
 "format": 1
}
code-index:end
"""

# Copyright (c) 2026, Oliver Reid and contributors
# For license information, please see license.txt
from frappe.model.document import Document
class SyncLogRowError(Document):
    pass
