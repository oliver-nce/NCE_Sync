# Copyright (c) 2026, Oliver Reid and contributors
# For license information, please see license.txt

"""
Live write-back from Frappe to WordPress.

Handles the on_update / after_insert wildcard hook for all DocTypes.
Only acts on DocTypes whose WP Tables record has listen_for_changes = 1,
write_back_mode = SQL Direct, and mirror_status = Mirrored.

New records (identified by a negative temp name assigned by reverse_sync) are
INSERTed into WordPress; the Frappe doc is then renamed to the real WP auto-
increment ID.  Existing records are UPDATEd.

Auto-generated WP columns (e.g. computed/virtual columns) are never written.

Decision logic (two rules only):
  A. If frappe.flags.in_sync is set, the change was caused by a WP→Frappe sync
     task — ignore it to prevent feedback loops.
  B. If the DocType is in the listen map (listen_for_changes = 1), queue the
     write-back job on the "default" queue.

All sync and write-back jobs share the default queue. A single worker executes
them in order, so write-backs naturally run after any in-flight sync job
finishes — no deferred queue or sync_in_progress flags needed.
"""

import frappe
from frappe import _

from nce_sync.utils.column_mapper import build_wp_row, load_column_mapping
from nce_sync.utils.connections import wp_connection
from nce_sync.utils.constants import CACHE_KEY_LISTEN_TABLES
from nce_sync.utils.write_back_dispatch import run_write_back_for_doc


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------


def _get_listen_map():
	"""
	Return a dict of {frappe_doctype: wp_table_name} for all WP Tables
	with listen_for_changes = 1, write_back_mode = SQL Direct, and mirror_status = Mirrored.

	Result is cached in Redis; invalidated (via clear_sql_direct_cache) whenever
	a WP Tables record is saved or trashed.
	"""
	cached = frappe.cache().get_value(CACHE_KEY_LISTEN_TABLES)
	if cached is not None:
		return cached

	try:
		rows = frappe.get_all(
			"WP Tables",
			filters={
				"mirror_status": "Mirrored",
				"listen_for_changes": 1,
				"write_back_mode": "SQL Direct",
			},
			fields=["name", "frappe_doctype"],
		)
	except Exception:
		# Column may not exist yet during migration — treat as empty
		return {}
	mapping = {r.frappe_doctype: r.name for r in rows if r.frappe_doctype}
	frappe.cache().set_value(CACHE_KEY_LISTEN_TABLES, mapping)
	return mapping


def clear_sql_direct_cache():
	"""Invalidate the listen-for-changes table map so it is rebuilt on next access."""
	frappe.cache().delete_value(CACHE_KEY_LISTEN_TABLES)


# ---------------------------------------------------------------------------
# Hook handler
# ---------------------------------------------------------------------------


def on_record_change(doc, method):
	"""
	Wildcard doc_events handler (on_update / after_insert).

	Rule A: if frappe.flags.in_sync → ignore (change came from WP→Frappe sync).
	Rule B: if DocType is in listen map → queue write-back job on default queue.
	"""
	if getattr(frappe.flags, "in_sync", False):
		return

	listen_map = _get_listen_map()
	if doc.doctype not in listen_map:
		return

	wp_table_name = listen_map[doc.doctype]

	frappe.enqueue(
		run_write_back_for_doc,
		wp_table_name=wp_table_name,
		doctype=doc.doctype,
		docname=doc.name,
		queue="default",
		is_async=True,
	)


# ---------------------------------------------------------------------------
# Background job
# ---------------------------------------------------------------------------


def push_record_to_wp(wp_table_name, doctype, docname):
	"""
	Background job: push one Frappe record to the matching WordPress table via SQL.

	Decision logic:
	- Negative integer name  → new record (temp name from assign_temp_name hook)
	  → INSERT, read back LAST_INSERT_ID(), rename Frappe doc to real WP ID.
	- Any other name         → existing record → UPDATE WHERE <pk> = name.

	Auto-generated and primary-key WP columns are excluded from all writes.
	Errors are written to Frappe Error Log and re-raised so the worker retries.
	"""
	try:
		frappe_doc = frappe.get_doc(doctype, docname)
	except frappe.DoesNotExistError:
		return

	wp_table_doc = frappe.get_doc("WP Tables", wp_table_name)

	column_mapping = load_column_mapping(wp_table_doc)

	name_wp_col = wp_table_doc.name_field_column
	if not name_wp_col:
		frappe.log_error(
			title=f"Live sync skip: {doctype}",
			message=f"No name_field_column set on WP Tables '{wp_table_name}'",
		)
		return

	# Check if this is a new record (negative temp name) or existing record
	record_id = frappe_doc.name
	is_new_record = False
	try:
		if int(record_id) < 0:
			is_new_record = True
	except (ValueError, TypeError):
		pass

	# Build the row data
	row = build_wp_row(frappe_doc, wp_table_doc, column_mapping)

	if not row:
		frappe.log_error(
			title=f"Live sync skip: {doctype} {docname}",
			message="No writable columns found for push",
		)
		return

	wp_conn_doc = frappe.get_single("WordPress Connection")
	with wp_connection(wp_conn_doc) as conn:
		try:
			cursor = conn.cursor()

			if is_new_record:
				# INSERT new record, skip auto-generated and name columns
				table_name = wp_table_doc.table_name
				cols = ", ".join(f"`{c}`" for c in row.keys())
				placeholders = ", ".join(["%s"] * len(row))
				sql = f"INSERT INTO `{table_name}` ({cols}) VALUES ({placeholders})"
				values = list(row.values())

				cursor.execute(sql, values)
				new_id = cursor.lastrowid

				if new_id:
					# Rename Frappe doc: temp negative name -> real WP ID
					old_name = frappe_doc.name
					frappe.rename_doc(doctype, old_name, str(new_id), merge=False, ignore_permissions=True)
				conn.commit()
			else:
				# UPDATE existing record
				table_name = wp_table_doc.table_name
				set_clause = ", ".join(f"`{c}` = %s" for c in row.keys())
				sql = f"UPDATE `{table_name}` SET {set_clause} WHERE `{name_wp_col}` = %s"
				values = list(row.values()) + [record_id]

				cursor.execute(sql, values)
				conn.commit()

			cursor.close()
		except Exception as e:
			conn.rollback()
			frappe.log_error(
				title=f"Live sync error: {doctype} {docname}",
				message=str(e),
			)
			raise
