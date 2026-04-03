# Copyright (c) 2026, Oliver Reid and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class WordPressConnection(Document):
	"""Single DocType to manage WordPress database connection."""

	@frappe.whitelist()
	def test_connection(self):
		"""Test the WordPress database connection and detect timezone."""
		try:
			from nce_sync.utils.connections import get_wp_connection

			# Attempt connection
			conn = get_wp_connection(self)
			if conn:
				# Detect WordPress DB timezone
				cursor = conn.cursor()
				cursor.execute("SELECT @@global.time_zone AS tz")
				row = cursor.fetchone()
				wp_tz = row["tz"] if row else "SYSTEM"

				# If DB reports SYSTEM, try to get the system timezone
				if wp_tz == "SYSTEM":
					cursor.execute("SELECT @@system_time_zone AS stz")
					sys_row = cursor.fetchone()
					wp_tz = sys_row["stz"] if sys_row else "SYSTEM"

				cursor.close()
				conn.close()

				self.wp_timezone = wp_tz
				self.connection_status = "Connected"
				self.connection_message = _("Successfully connected. DB timezone: {0}").format(wp_tz)
				self.save()
				frappe.msgprint(
					_("Connection successful! DB timezone: {0}").format(wp_tz),
					indicator="green",
				)
			else:
				raise Exception(_("Unable to establish connection"))

		except Exception as e:
			self.connection_status = "Failed"
			self.connection_message = str(e)
			self.save()
			frappe.throw(_("Connection failed: {0}").format(str(e)))

	@frappe.whitelist()
	def discover_tables(self):
		"""Discover all tables and views from WordPress database."""
		try:
			from nce_sync.utils.connections import get_wp_connection
			from nce_sync.utils.schema_mirror import discover_tables_and_views

			conn = get_wp_connection(self)
			if not conn:
				frappe.throw(_("Connection not configured or failed"))

			tables_and_views = discover_tables_and_views(conn)
			conn.close()

			# Return as JSON for client-side dialog
			return tables_and_views

		except Exception as e:
			frappe.throw(_("Failed to discover tables: {0}").format(str(e)))

	@frappe.whitelist()
	def mirror_all(self):
		"""Mirror all selected WP_Tables to Frappe DocTypes."""
		try:
			from nce_sync.utils.schema_mirror import mirror_table_schema

			# Get all WP_Tables records
			wp_tables = frappe.get_all("WP Tables", fields=["name"])

			if not wp_tables:
				frappe.msgprint(_("No tables selected for mirroring."), indicator="orange")
				return

			success_count = 0
			error_count = 0

			for table in wp_tables:
				try:
					doc = frappe.get_doc("WP Tables", table.name)
					mirror_table_schema(self, doc)
					success_count += 1
				except Exception as e:
					error_count += 1
					frappe.log_error(title=f"Mirror Error: {table.name}", message=str(e))

			# Summary message
			if error_count == 0:
				frappe.msgprint(
					_("Successfully mirrored {0} table(s)").format(success_count), indicator="green"
				)
			else:
				frappe.msgprint(
					_("Mirrored {0} table(s) with {1} error(s). Check Error Log for details.").format(
						success_count, error_count
					),
					indicator="orange",
				)

		except Exception as e:
			frappe.throw(_("Mirror operation failed: {0}").format(str(e)))
