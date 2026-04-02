# Copyright (c) 2026, Oliver Reid and contributors
# For license information, please see license.txt

import json

import frappe
from frappe import _
from frappe.model.document import Document

# Core Frappe DocType names - never drop these tables (safety guard)
_NEVER_DROP_DOCTYPES = frozenset(
	{
		"DocType",
		"DocField",
		"DocPerm",
		"DocType Action",
		"DocType Link",
		"User",
		"User Permission",
		"Role",
		"Module Def",
		"File",
		"Error Log",
		"Error Snapshot",
		"Scheduled Job Log",
		"Activity Log",
		"Singles",
		"DefaultValue",
		"Property Setter",
		"Custom Field",
		"Workflow",
		"Workflow State",
		"Workflow Action",
		"Workflow Transition",
	}
)


def _is_safe_to_drop_table(doctype_name):
	"""Return False if dropping this table could harm core Frappe."""
	if not doctype_name or "`" in doctype_name or ";" in doctype_name:
		return False
	if doctype_name in _NEVER_DROP_DOCTYPES:
		return False
	# Also block any non-custom DocType (core/system)
	if frappe.db.exists("DocType", doctype_name):
		is_custom = frappe.db.get_value("DocType", doctype_name, "custom")
		if not is_custom:
			return False
	return True


def _collect_soft_dependencies(doctype_name):
	"""
	Return a dict of all soft-dependency document names that reference doctype_name.
	These are artifacts Frappe does NOT clean up automatically on DocType deletion.
	"""

	def names(dt, filters):
		return frappe.get_all(dt, filters=filters, pluck="name")

	return {
		"Report": names("Report", {"ref_doctype": doctype_name}),
		"Dashboard Chart": names("Dashboard Chart", {"document_type": doctype_name}),
		"Number Card": names("Number Card", {"document_type": doctype_name}),
		"Client Script": names("Client Script", {"dt": doctype_name}),
		"Kanban Board": names("Kanban Board", {"reference_doctype": doctype_name}),
		"Print Format": names("Print Format", {"doc_type": doctype_name}),
	}


def _delete_mirrored_doctype(doctype_name):
	"""
	Fully remove a mirrored DocType and all its soft dependencies in the correct order
	so that Frappe's workspace validation never encounters a stale reference.

	Order:
	  1. Collect all soft-dependency artifacts (Reports, Charts, Scripts, etc.)
	  2. Remove their workspace links WHILE they still exist (validation passes)
	  3. Delete the artifacts
	  4. Remove the DocType workspace shortcut
	  5. Delete the DocType — Frappe drops the table and cleans hard dependencies
	"""
	if not _is_safe_to_drop_table(doctype_name):
		return

	from nce_sync.utils.workspace_utils import remove_from_workspace

	# Step 1: collect everything that references this DocType
	deps = _collect_soft_dependencies(doctype_name)

	# Step 2 + 4: clean workspace (shortcuts + all artifact links) before anything is deleted
	remove_from_workspace(doctype_name, soft_deps=deps)

	# Step 3: delete soft-dependency artifacts now that workspace is clean
	for doctype, names in deps.items():
		for name in names:
			try:
				frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
			except Exception as e:
				frappe.log_error(title=f"Delete {doctype} '{name}' failed", message=str(e))
	if any(deps.values()):
		frappe.db.commit()

	# Step 5: delete the DocType record
	if frappe.db.exists("DocType", doctype_name):
		try:
			frappe.delete_doc("DocType", doctype_name, force=True, ignore_permissions=True)
			frappe.db.commit()
		except Exception as e:
			frappe.log_error(title=f"Delete DocType failed: {doctype_name}", message=str(e))

	# Step 6: explicitly drop the DB table (Frappe doesn't always do this reliably)
	try:
		frappe.db.sql(f"DROP TABLE IF EXISTS `tab{doctype_name}`")
		frappe.db.commit()
	except Exception as e:
		frappe.log_error(title=f"Drop table failed: tab{doctype_name}", message=str(e))


class WPTables(Document):
	"""Tracks WordPress tables selected for mirroring."""

	def autoname(self):
		"""Set document name from nce_name if provided, otherwise use table_name."""
		self.name = self.nce_name or self.table_name

	def on_update(self):
		"""Invalidate the listen-for-changes cache so any toggle takes effect immediately."""
		from nce_sync.utils.live_sync import clear_sql_direct_cache

		clear_sql_direct_cache()

	def on_trash(self):
		"""Full cascade cleanup: delete Sync Logs, mirrored DocType (Mirror mode only), workspace shortcut, and clear live-sync cache."""
		from nce_sync.utils.live_sync import clear_sql_direct_cache

		clear_sql_direct_cache()
		# Delete associated Sync Log records
		sync_logs = frappe.get_all("Sync Log", filters={"wp_table": self.name}, pluck="name")
		for log_name in sync_logs:
			frappe.delete_doc("Sync Log", log_name, force=True, ignore_permissions=True)

		# Delete mirrored DocType (table + record) - drop table first so it always works
		if self.frappe_doctype and self.doctype_source != "Native":
			_delete_mirrored_doctype(self.frappe_doctype)

	def validate(self):
		"""Validate and enforce source-of-truth hierarchy."""
		# Legacy option removed from form — treat as Never (use Client Script for API push).
		if self.write_back_mode == "API Required":
			self.write_back_mode = "Never"

		if self.doctype_source == "Native":
			# Native entries only need a valid existing DocType — no WP table required
			if not self.frappe_doctype:
				frappe.throw(
					_(
						"Frappe DocType is required for Native mode. "
						"Select the existing DocType you want to link."
					)
				)
			if not frappe.db.exists("DocType", self.frappe_doctype):
				frappe.throw(
					_("DocType '{0}' does not exist. Please select a valid existing DocType.").format(
						self.frappe_doctype
					)
				)
		else:
			# Mirror mode — existing validation
			if self.nce_name:
				self._validate_doctype_name(self.nce_name)

	def _validate_doctype_name(self, name):
		"""
		Check if the proposed DocType name conflicts with existing DocTypes
		or database tables (which may belong to Frappe core or other apps).
		"""
		# Skip if this table already owns this DocType
		if self.frappe_doctype == name:
			return

		# Check if DocType already exists in the registry
		if frappe.db.exists("DocType", name):
			is_custom = frappe.db.get_value("DocType", name, "custom")
			if not is_custom:
				frappe.throw(
					_("'{0}' is a system DocType and cannot be used. Please choose a different name.").format(
						name
					)
				)
			else:
				other_table = frappe.db.get_value(
					"WP Tables", {"frappe_doctype": name, "name": ["!=", self.name]}, "name"
				)
				if other_table:
					frappe.throw(
						_(
							"'{0}' is already used by another mirrored table ({1}). Please choose a different name."
						).format(name, other_table)
					)
				else:
					frappe.throw(
						_(
							"A DocType named '{0}' already exists. Please choose a different name or delete the existing DocType first."
						).format(name)
					)

		# Check if a database table with this name already exists (possibly in use by another app)
		# Skip if this WP Tables entry owns that name (e.g. orphan table from a previous re-mirror)
		if frappe.db.sql("SHOW TABLES LIKE %s", f"tab{name}"):
			is_own_orphan = self.nce_name == name or self.frappe_doctype == name
			if not is_own_orphan:
				frappe.throw(
					_(
						"'{0}' cannot be used — it already exists, possibly in use by another app. Please choose a different name."
					).format(name)
				)

	@frappe.whitelist()
	def preview_schema(self, table_name_override=None):
		"""Introspect table schema and return proposed field mappings for user review."""
		from nce_sync.utils.schema_mirror import preview_table_schema

		wp_conn = frappe.get_single("WordPress Connection")
		if not wp_conn:
			frappe.throw(_("WordPress Connection not configured"))

		original_table_name = self.table_name
		if table_name_override:
			self.table_name = table_name_override

		try:
			return preview_table_schema(wp_conn, self)
		finally:
			self.table_name = original_table_name

	@frappe.whitelist()
	def link_external_doctype(self):
		"""
		Link an existing Native DocType: set mirror_status to 'Linked'.
		No WP table or column mapping is involved.
		"""
		if not self.frappe_doctype:
			frappe.throw(_("Frappe DocType is required."))
		if not frappe.db.exists("DocType", self.frappe_doctype):
			frappe.throw(_("DocType '{0}' does not exist.").format(self.frappe_doctype))

		self.mirror_status = "Linked"
		self.error_log = None
		self.save()

		frappe.msgprint(
			_("Native DocType '{0}' linked successfully.").format(self.frappe_doctype),
			indicator="green",
		)

	@frappe.whitelist()
	def unlink_external_doctype(self):
		"""
		Remove the Native link. Resets mirror_status to Pending.
		Does NOT delete the Frappe DocType — it belongs to another app.
		"""
		self.mirror_status = "Pending"
		self.error_log = None
		self.save()

		frappe.msgprint(
			_("Native DocType unlinked. This entry is now in Pending state."),
			indicator="orange",
		)

	@frappe.whitelist()
	def mirror_schema(
		self,
		field_overrides=None,
		label_overrides=None,
		matching_fields=None,
		name_field_column=None,
		auto_generated_columns=None,
		modified_ts_field=None,
		created_ts_field=None,
	):
		"""Mirror this specific table's schema to a Frappe DocType."""
		try:
			from nce_sync.utils.schema_mirror import mirror_table_schema

			if field_overrides and isinstance(field_overrides, str):
				field_overrides = json.loads(field_overrides)

			if label_overrides and isinstance(label_overrides, str):
				label_overrides = json.loads(label_overrides)

			# Matching fields should already be saved by JS before this is called
			# But update if provided and different (belt and suspenders)
			if matching_fields and matching_fields != self.matching_fields:
				self.matching_fields = matching_fields
				self.save()

			# Validate DocType name before mirroring
			doctype_name = self.nce_name or self.table_name
			self._validate_doctype_name(doctype_name)

			wp_conn = frappe.get_single("WordPress Connection")
			if not wp_conn:
				frappe.throw(_("WordPress Connection not configured"))

			mirror_table_schema(
				wp_conn,
				self,
				field_overrides=field_overrides,
				label_overrides=label_overrides,
				name_field_column=name_field_column or None,
				auto_generated_columns=auto_generated_columns or None,
				modified_ts_field=modified_ts_field or None,
				created_ts_field=created_ts_field or None,
			)

			frappe.msgprint(
				_("Successfully mirrored table: {0}").format(self.table_name),
				indicator="green",
			)

		except Exception as e:
			import traceback

			self.mirror_status = "Error"
			self.error_log = traceback.format_exc()
			self.save()
			frappe.log_error(title=f"Mirror Error: {self.table_name}", message=traceback.format_exc())
			frappe.throw(_("Failed to mirror table: {0}").format(str(e)))

	@frappe.whitelist()
	def delete_mirror(self):
		"""
		Delete the generated DocType and remove from workspace.
		Resets this WP Tables entry back to Pending so it can be re-mirrored.
		"""
		doctype_name = self.frappe_doctype
		if not doctype_name:
			frappe.throw(_("No mirrored DocType to delete"))

		_delete_mirrored_doctype(doctype_name)

		# Reset this WP Tables entry
		self.frappe_doctype = None
		self.mirror_status = "Pending"
		self.error_log = None
		self.save()

		frappe.msgprint(
			_("Deleted DocType '{0}' and removed from workspace. Ready to re-mirror.").format(doctype_name),
			indicator="green",
		)

	@frappe.whitelist()
	def remove_table(self):
		"""
		Full cleanup: delete the generated DocType, remove from workspace,
		and delete this WP Tables record itself.
		"""
		doctype_name = self.frappe_doctype
		if doctype_name:
			_delete_mirrored_doctype(doctype_name)

		# Delete this WP Tables record
		table_name = self.table_name
		frappe.delete_doc("WP Tables", self.name, force=True, ignore_permissions=True)
		frappe.db.commit()

		frappe.msgprint(
			_("Removed table '{0}' and all associated data.").format(table_name),
			indicator="green",
		)

	@frappe.whitelist()
	def add_to_workspace(self):
		"""Add the mirrored DocType as a shortcut in the Tables workspace."""
		from nce_sync.utils.workspace_utils import add_to_workspace

		if not self.frappe_doctype:
			frappe.throw(_("No mirrored DocType to add"))

		add_to_workspace(self.frappe_doctype, label=self.nce_name or self.frappe_doctype)
		frappe.msgprint(
			_("Added '{0}' to the workspace.").format(self.frappe_doctype),
			indicator="green",
			alert=True,
		)

	@frappe.whitelist()
	def regenerate_column_mapping(self):
		"""
		Regenerate the column mapping from the WordPress table schema.
		Useful for tables mirrored before column_mapping was added.
		Also detects virtual/generated columns for reverse sync protection.
		"""
		import json

		from nce_sync.utils.schema_mirror import get_table_schema, get_wp_connection, resolve_fieldname

		wp_conn = frappe.get_single("WordPress Connection")
		if not wp_conn:
			frappe.throw(_("WordPress Connection not configured"))

		conn = get_wp_connection(wp_conn)
		schema = get_table_schema(conn, self.table_name)
		conn.close()

		# Build column mapping: WP column name -> {fieldname, is_virtual[, is_name]}
		# When name_field_column is set, that column maps to "name" with is_name=True
		column_mapping = {}
		virtual_count = 0
		name_field_column = getattr(self, "name_field_column", None)
		for col in schema["columns"]:
			wp_col_name = col["COLUMN_NAME"]
			extra = col.get("EXTRA", "") or ""
			is_virtual = "VIRTUAL" in extra.upper() or "GENERATED" in extra.upper()
			if name_field_column and wp_col_name == name_field_column:
				column_mapping[wp_col_name] = {
					"fieldname": "name",
					"is_virtual": is_virtual,
					"is_name": True,
				}
			else:
				frappe_fieldname = resolve_fieldname(wp_col_name)
				column_mapping[wp_col_name] = {
					"fieldname": frappe_fieldname,
					"is_virtual": is_virtual,
				}
			if is_virtual:
				virtual_count += 1

		self.column_mapping = json.dumps(column_mapping)
		self.save()

		msg = _("Column mapping regenerated: {0} columns mapped").format(len(column_mapping))
		if virtual_count > 0:
			msg += _(", {0} virtual/computed columns detected").format(virtual_count)
		frappe.msgprint(msg, indicator="green")

	@frappe.whitelist()
	def truncate_data(self):
		"""
		Delete all records from the mirrored Frappe DocType.
		The DocType structure remains intact.
		"""
		if not self.frappe_doctype:
			frappe.throw(_("No Frappe DocType associated with this table"))

		frappe.db.delete(self.frappe_doctype)
		frappe.db.commit()

		# Reset sync status since data is gone
		self.last_synced = None
		self.last_sync_status = None
		self.last_sync_log = "Data truncated manually"
		self.save()

	@frappe.whitelist()
	def remap_schema(
		self,
		new_table_name=None,
		field_overrides=None,
		label_overrides=None,
		matching_fields=None,
		name_field_column=None,
		auto_generated_columns=None,
		modified_ts_field=None,
		created_ts_field=None,
	):
		"""
		Remap an existing mirrored DocType to a (possibly renamed) source table.
		Truncates data, updates source reference, adds any new columns, rebuilds
		the column mapping, then leaves the DocType ready for a fresh sync.
		The DocType and its SQL table are preserved so other apps' references stay intact.
		"""
		from nce_sync.utils.schema_mirror import mirror_table_schema

		if not self.frappe_doctype:
			frappe.throw(_("No mirrored DocType to remap"))

		if field_overrides and isinstance(field_overrides, str):
			field_overrides = json.loads(field_overrides)
		if label_overrides and isinstance(label_overrides, str):
			label_overrides = json.loads(label_overrides)

		# Update source table name if it changed
		if new_table_name and new_table_name != self.table_name:
			self.table_name = new_table_name
			self.save()

		# Update matching fields if provided
		if matching_fields and matching_fields != self.matching_fields:
			self.matching_fields = matching_fields
			self.save()

		# Truncate existing data
		frappe.db.delete(self.frappe_doctype)
		frappe.db.commit()

		wp_conn = frappe.get_single("WordPress Connection")
		if not wp_conn:
			frappe.throw(_("WordPress Connection not configured"))

		# Re-mirror: detects existing DocType and calls update_existing_doctype
		# which adds new columns without removing existing ones
		mirror_table_schema(
			wp_conn,
			self,
			field_overrides=field_overrides,
			label_overrides=label_overrides,
			name_field_column=name_field_column or None,
			auto_generated_columns=auto_generated_columns or None,
			modified_ts_field=modified_ts_field or None,
			created_ts_field=created_ts_field or None,
		)

		# Reset sync status
		self.last_synced = None
		self.last_sync_status = None
		self.last_sync_log = "Schema remapped — ready for sync"
		self.save()

		frappe.msgprint(
			_("Remapped '{0}' to source table '{1}'. Data cleared — run Sync Now to repopulate.").format(
				self.frappe_doctype, self.table_name
			),
			indicator="green",
		)

	@frappe.whitelist()
	def debug_sync_one_row(self):
		"""
		Debug: Sync just the first row and show detailed info about what's happening.
		"""
		from nce_sync.utils.schema_mirror import get_wp_connection

		if not self.frappe_doctype:
			frappe.throw(_("No Frappe DocType associated with this table"))

		wp_conn = frappe.get_single("WordPress Connection")
		conn = get_wp_connection(wp_conn)

		cursor = conn.cursor()

		# Get actual column names from information_schema
		cursor.execute(
			"""
			SELECT COLUMN_NAME
			FROM information_schema.COLUMNS
			WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s
			ORDER BY ORDINAL_POSITION
		""",
			(self.table_name,),
		)
		schema_columns = [r["COLUMN_NAME"] for r in cursor.fetchall()]

		cursor.execute(f"SELECT * FROM `{self.table_name}` LIMIT 1")
		row = cursor.fetchone()
		cursor.close()
		conn.close()

		if not row:
			frappe.throw(_("No rows in source table"))

		# Get Frappe DocType field info
		meta = frappe.get_meta(self.frappe_doctype)
		frappe_fields = {df.fieldname: df.fieldtype for df in meta.fields}

		# Build debug info
		debug_info = []

		debug_info.append("=== Schema Column Names (from information_schema) ===")
		for col in schema_columns:
			debug_info.append(f"  Schema: '{col}'")

		debug_info.append("\n=== WordPress Row Keys (from SELECT *) ===")
		for key in row.keys():
			debug_info.append(f"  WP: '{key}' = {repr(row[key])[:50]}")

		debug_info.append(f"\n=== Frappe DocType Fields ({len(frappe_fields)} fields) ===")
		for fname, ftype in frappe_fields.items():
			debug_info.append(f"  Frappe: '{fname}' ({ftype})")

		debug_info.append("\n=== Field Matching (with lowercase) ===")
		matched = 0
		unmatched_wp = []
		for key in row.keys():
			lowercase_key = key.lower()
			if lowercase_key in frappe_fields:
				debug_info.append(f"  MATCH: WP '{key}' -> Frappe '{lowercase_key}'")
				matched += 1
			else:
				unmatched_wp.append(key)
				debug_info.append(f"  NO MATCH: WP '{key}' (lowercase: '{lowercase_key}') not in Frappe")

		debug_info.append("\n=== Summary ===")
		debug_info.append(f"Matched: {matched}/{len(row)}")
		if unmatched_wp:
			debug_info.append(f"Unmatched WP columns: {unmatched_wp}")
		else:
			debug_info.append("All columns matched!")

		frappe.msgprint("<pre>" + "\n".join(debug_info) + "</pre>", title="Debug Sync Info")

	@frappe.whitelist()
	def sync_now(self):
		"""
		Manual trigger for syncing this table's data from WordPress to Frappe.
		Enqueues the sync as a background job so the user can keep working.
		Progress is reported via toast notifications.
		"""
		if self.mirror_status != "Mirrored":
			frappe.throw(_("Table must be mirrored before syncing"))

		if not self.frappe_doctype:
			frappe.throw(_("No Frappe DocType associated with this table"))

		frappe.enqueue(
			"nce_sync.utils.data_sync.run_sync_for_table",
			queue="default",
			timeout=3600,
			wp_table_name=self.name,
			user=frappe.session.user,
		)

		frappe.msgprint(
			_("Sync started in background for {0}. You'll see progress toasts in the bottom-right.").format(
				self.nce_name or self.table_name
			),
			indicator="blue",
			alert=True,
		)
