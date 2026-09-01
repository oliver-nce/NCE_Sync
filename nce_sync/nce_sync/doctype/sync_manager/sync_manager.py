# Copyright (c) 2026, Oliver Reid and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


class SyncManager(Document):
	def validate(self):
		if self.sync_frequency == "Other" and cint(self.custom_sync_interval_minutes) < 1:
			frappe.throw(_("Enter a custom interval of at least 1 minute."))

	@frappe.whitelist()
	def run_sync_now(self):
		"""Enqueue an immediate sync for all enabled mirrored tables."""
		from nce_sync.utils.constants import MAX_SYNC_JOB_RUNTIME_SEC
		from nce_sync.utils.data_sync import run_sync_for_table

		tables = frappe.get_all(
			"WP Tables",
			filters={"auto_sync_active": 1, "mirror_status": ["in", ["Mirrored", "Linked"]]},
			pluck="name",
		)

		if not tables:
			frappe.msgprint(_("No tables with auto-sync enabled"))
			return _("No tables to sync")

		user = frappe.session.user
		for table_name in tables:
			frappe.enqueue(
				run_sync_for_table,
				wp_table_name=table_name,
				user=user,
				queue="default",
				timeout=MAX_SYNC_JOB_RUNTIME_SEC,
				is_async=True,
			)

		return _("{0} sync job(s) queued").format(len(tables))

	@frappe.whitelist()
	def load_wp_tables(self):
		"""
		Populate the tables_to_sync child table with all mirrored WP Tables.
		Only adds tables that aren't already in the list.
		"""
		# Get all mirrored WP Tables
		wp_tables = frappe.get_all(
			"WP Tables",
			filters={"mirror_status": ["in", ["Mirrored", "Linked"]]},
			fields=["name", "table_name", "frappe_doctype"],
		)

		# Get existing table names in the list
		existing = {row.wp_table for row in self.tables_to_sync}

		# Add missing tables
		for table in wp_tables:
			if table.name not in existing:
				self.append(
					"tables_to_sync",
					{
						"wp_table": table.name,
						"table_name": table.table_name,
						"frappe_doctype": table.frappe_doctype,
						"enabled": 1,
					},
				)

		self.save()
