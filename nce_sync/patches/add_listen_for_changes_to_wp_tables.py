# Copyright (c) 2026, Oliver Reid and contributors
# For license information, please see license.txt

"""Add listen_for_changes column to WP Tables table.

code-index:begin
{
 "purpose": "Migration patch adding the listen_for_changes column to the tabWP Tables database table.",
 "read_when": "Debugging or extending schema migrations for WP Tables live-sync toggle storage.",
 "gotchas": [],
 "keywords": ["patch", "listen_for_changes", "WP Tables", "migration", "ALTER TABLE", "schema"],
 "format": 1
}
code-index:end
"""

import frappe


def execute():
	"""Add the listen_for_changes column to the `tabWP Tables` table."""
	if not frappe.db.table_exists("WP Tables"):
		return

	# Check if the column already exists
	if frappe.db.has_column("WP Tables", "listen_for_changes"):
		return

	# Add the column with default value 0
	frappe.db.sql(
		"""
		ALTER TABLE `tabWP Tables`
		ADD COLUMN `listen_for_changes` TINYINT(1) NOT NULL DEFAULT 0
		"""
	)
	frappe.db.commit()
