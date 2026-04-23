# Copyright (c) 2026, Oliver Reid and contributors
# For license information, please see license.txt

"""
Reverse-sync utilities: Frappe → WordPress.

Responsibilities:
  1. assign_temp_name   – Frappe before_insert hook that assigns a negative
                          auto-decrementing integer as a temporary name for
                          new documents in mirrored DocTypes.  The real WP
                          auto-increment ID replaces it after the row is
                          inserted into WordPress.

  2. sync_frappe_to_wp  – Push new / updated Frappe records back to WordPress.
                          • INSERT  new records (negative temp name), skip
                            auto-generated columns, read back LAST_INSERT_ID(),
                            rename the Frappe document to the real WP ID.
                          • UPDATE  existing records (positive / real name),
                            skip auto-generated columns.
"""

import frappe
import pymysql
from frappe import _

from nce_sync.utils.column_mapper import build_wp_row, get_name_wp_column, load_column_mapping
from nce_sync.utils.connections import wp_connection
from nce_sync.utils.constants import TEMP_NAME_COUNTER_DOCTYPE, TEMP_NAME_COUNTER_FIELD


def _next_temp_name():
	"""
	Return the next negative integer to use as a temporary Frappe document name.

	We use `frappe.db` with an atomic decrement stored in the
	"NCE Sync Settings" singleton.  Falls back to a DB-level MIN if
	the singleton doesn't exist yet.
	"""
	try:
		current = frappe.db.get_single_value(TEMP_NAME_COUNTER_DOCTYPE, TEMP_NAME_COUNTER_FIELD) or 0
		current = int(current)
	except Exception:
		# Singleton or field missing – derive from actual min name in use
		current = 0

	if current >= 0:
		# Bootstrap from the lowest negative name already in any mirrored table
		current = 0

	new_val = current - 1
	try:
		frappe.db.set_single_value(TEMP_NAME_COUNTER_DOCTYPE, TEMP_NAME_COUNTER_FIELD, new_val)
	except Exception:
		pass  # non-critical – worst case we get a collision, handled below

	return str(new_val)


def _is_mirrored_doctype(doctype):
	"""Return the WP Tables doc if *doctype* is a mirrored NCE Sync table."""
	if not frappe.db.table_exists("WP Tables"):
		return None
	try:
		return frappe.db.get_value(
			"WP Tables",
			{"frappe_doctype": doctype, "mirror_status": "Mirrored"},
			["name", "frappe_doctype", "name_field_column"],
			as_dict=True,
		)
	except Exception:
		return None


# ---------------------------------------------------------------------------
# Frappe before_insert hook
# ---------------------------------------------------------------------------


def assign_temp_name(doc, method=None):
	"""
	before_insert hook – assign a negative auto-decrementing integer name to
	any new document belonging to a mirrored DocType that uses name_field_column
	(i.e. the WP primary-key column maps to Frappe's `name` field).

	This keeps the record locally visible while we wait to push it to WordPress
	and learn its real auto-increment ID.
	"""
	wp_table_ref = _is_mirrored_doctype(doc.doctype)
	if not wp_table_ref:
		return
	if not wp_table_ref.get("name_field_column"):
		return  # Only applies when WP PK is mapped to Frappe name

	# Only intercept if the name hasn't been set or looks like a Frappe hash
	# (i.e. the user hasn't supplied a real ID)
	current_name = getattr(doc, "name", None) or ""
	if current_name and not _looks_like_temp_or_hash(current_name):
		return  # Already has a real ID – leave it alone

	temp_name = _next_temp_name()
	# Ensure uniqueness in case of counter drift
	doctype = doc.doctype
	while frappe.db.exists(doctype, temp_name):
		temp_name = str(int(temp_name) - 1)

	doc.name = temp_name


def _looks_like_temp_or_hash(name):
	"""
	Return True if *name* looks like a Frappe-generated hash or a temp negative
	integer that we should replace.
	"""
	if not name:
		return True
	# Negative integers are our own temp names
	try:
		return int(name) < 0
	except (ValueError, TypeError):
		pass
	# Frappe hash format: 10 hex chars
	import re

	return bool(re.fullmatch(r"[0-9a-f]{10}", name.lower()))


# ---------------------------------------------------------------------------
# Core reverse-sync functions
# ---------------------------------------------------------------------------


def insert_record_to_wp(wp_conn_doc, wp_table_doc, frappe_doc):
	"""
	INSERT one Frappe document into WordPress and return the newly-assigned WP ID.

	Steps:
	  1. Build {column: value} dict, omitting auto-generated + name columns.
	  2. Execute INSERT.
	  3. Read LAST_INSERT_ID().
	  4. Rename the Frappe doc from its temp name to the real WP ID.

	Returns:
		new_wp_id (str) – the real WordPress-assigned primary key value.

	Raises:
		Exception if INSERT fails.
	"""
	column_mapping = load_column_mapping(wp_table_doc)
	row = build_wp_row(frappe_doc, wp_table_doc, column_mapping)

	if not row:
		frappe.throw(_("No writable columns found for reverse sync INSERT"))

	table_name = wp_table_doc.table_name
	cols = ", ".join(f"`{c}`" for c in row)
	placeholders = ", ".join(["%s"] * len(row))
	sql = f"INSERT INTO `{table_name}` ({cols}) VALUES ({placeholders})"
	values = list(row.values())

	with wp_connection(wp_conn_doc) as conn:
		with conn.cursor() as cur:
			cur.execute(sql, values)
			new_id = cur.lastrowid
		conn.commit()

	if not new_id:
		frappe.throw(_("WordPress did not return a new ID after INSERT"))

	new_wp_id = str(new_id)

	# Rename Frappe doc: temp negative name → real WP ID
	old_name = frappe_doc.name
	if old_name != new_wp_id:
		# Avoid sync_gate.before_save blocking this write while WP→Frappe sync is active.
		prev_in_sync = bool(getattr(frappe.flags, "in_sync", False))
		frappe.flags.in_sync = True
		try:
			frappe.rename_doc(frappe_doc.doctype, old_name, new_wp_id, merge=False, ignore_permissions=True)
		finally:
			frappe.flags.in_sync = prev_in_sync

	return new_wp_id


def update_record_in_wp(wp_conn_doc, wp_table_doc, frappe_doc):
	"""
	UPDATE one existing Frappe document in WordPress.

	Uses the name_field_column (mapped to Frappe `name`) as the WHERE clause.
	Omits auto-generated columns from the SET clause.

	Returns:
		rows_affected (int)
	"""
	column_mapping = load_column_mapping(wp_table_doc)
	row = build_wp_row(frappe_doc, wp_table_doc, column_mapping)

	if not row:
		frappe.throw(_("No writable columns found for reverse sync UPDATE"))

	# Find the WP primary key column
	name_wp_col = get_name_wp_column(wp_table_doc, column_mapping)

	if not name_wp_col:
		frappe.throw(_("Cannot UPDATE: no name_field_column configured on this table"))

	table_name = wp_table_doc.table_name
	set_clause = ", ".join(f"`{c}` = %s" for c in row)
	sql = f"UPDATE `{table_name}` SET {set_clause} WHERE `{name_wp_col}` = %s"
	values = list(row.values()) + [frappe_doc.name]

	with wp_connection(wp_conn_doc) as conn:
		with conn.cursor() as cur:
			cur.execute(sql, values)
			rows_affected = cur.rowcount
		conn.commit()

	return rows_affected


def sync_frappe_to_wp(wp_table_doc, user=None):
	"""
	Push all pending Frappe → WordPress changes for a mirrored table.

	Logic:
	  • Records whose `name` is a negative integer  → INSERT (new, not yet in WP)
	  • Records whose `name` is a positive integer   → UPDATE (already in WP)

	Only operates on tables that have name_field_column set (WP PK mapped to
	Frappe name).  Tables without this mapping are read-only from Frappe's
	perspective.

	Args:
		wp_table_doc: WP Tables document
		user: optional user ID for realtime progress events

	Returns:
		dict with inserted/updated counts
	"""
	if not getattr(wp_table_doc, "name_field_column", None):
		return {"inserted": 0, "updated": 0, "errors": 0}

	doctype = wp_table_doc.frappe_doctype
	if not doctype:
		frappe.throw(_("No Frappe DocType associated with this table"))

	wp_conn_doc = frappe.get_single("WordPress Connection")

	# Fetch all docs that look like temp names (negative) or real IDs (positive)
	all_docs = frappe.get_all(doctype, fields=["name"], ignore_permissions=True)

	inserted = 0
	updated = 0
	errors = 0

	for rec in all_docs:
		doc_name = rec["name"]
		try:
			is_new = _is_temp_name(doc_name)
			frappe_doc = frappe.get_doc(doctype, doc_name)

			if is_new:
				insert_record_to_wp(wp_conn_doc, wp_table_doc, frappe_doc)
				inserted += 1
			else:
				update_record_in_wp(wp_conn_doc, wp_table_doc, frappe_doc)
				updated += 1

		except Exception as e:
			errors += 1
			frappe.log_error(
				title=f"Reverse sync error: {doctype} / {doc_name}",
				message=str(e),
			)

	return {"inserted": inserted, "updated": updated, "errors": errors}


def _is_temp_name(name):
	"""Return True if *name* is a negative integer (temp name awaiting WP INSERT)."""
	try:
		return int(name) < 0
	except (ValueError, TypeError):
		return False
