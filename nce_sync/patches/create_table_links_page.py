# Copyright (c) 2026, Oliver Reid and contributors
# For license information, please see license.txt

"""Create the Table Links page if it does not exist.

code-index:begin
{
 "purpose": "Migration patch that creates the Table Links desk page if it does not already exist.",
 "read_when": "Debugging Table Links page bootstrap on upgrade or fresh installs missing the page record.",
 "gotchas": [],
 "keywords": ["Table Links", "create", "execute", "frappe", "links", "page", "table"],
 "format": 1
}
code-index:end
"""

import frappe


def execute():
	if frappe.db.exists("Page", "table-links"):
		return

	page = frappe.new_doc("Page")
	page.page_name = "table-links"
	page.title = "Table Links"
	page.module = "NCE Sync"
	page.standard = "Yes"
	page.icon = "link"
	page.insert(ignore_permissions=True)
	frappe.db.commit()
