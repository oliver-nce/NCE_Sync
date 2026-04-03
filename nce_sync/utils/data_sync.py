# Copyright (c) 2026, Oliver Reid and contributors
# For license information, please see license.txt

"""
Data synchronization utilities for NCE_Sync.
Handles bidirectional sync between WordPress tables and Frappe DocTypes.
Primary direction: WordPress → Frappe (TS Compare / Truncate & Replace).
Reverse direction: Frappe → WordPress (INSERT new records, UPDATE existing).
"""

import json
from datetime import datetime, timedelta

import frappe
import pytz
from frappe import _
from frappe.utils import now_datetime

from nce_sync.utils.column_mapper import (
	build_reverse_mapping,
	get_frappe_fieldname,
	load_column_mapping,
)
from nce_sync.utils.connections import get_wp_connection, wp_connection
from nce_sync.utils.constants import (
	KEEP_SYNC_LOG_COUNT,
	MAX_ROW_ERROR_MESSAGES,
	SYNC_FREQUENCY_MAP,
	UPSERT_BATCH_SIZE,
	WHERE_IN_BATCH_SIZE,
)


def _normalize_key_value(value):
	"""
	Normalize a key value for consistent comparison between WP and Frappe.
	Converts to string to handle int/string type mismatches.

	Args:
		value: The value to normalize

	Returns:
		Normalized string value, or None if value is None
	"""
	if value is None:
		return None
	# Convert to string for consistent comparison
	return str(value)


def get_timezone(tz_name):
	"""
	Get a pytz timezone object, falling back to UTC if invalid.

	Args:
		tz_name: Timezone name (e.g., 'America/New_York')

	Returns:
		pytz timezone object
	"""
	if not tz_name:
		return pytz.UTC
	try:
		return pytz.timezone(tz_name)
	except pytz.UnknownTimeZoneError:
		return pytz.UTC


def convert_frappe_ts_to_wp_tz(ts, wp_tz_name):
	"""
	Convert a Frappe timestamp to WordPress timezone for queries.

	Args:
		ts: datetime object in Frappe timezone
		wp_tz_name: WordPress timezone name

	Returns:
		datetime in WordPress timezone (naive, for SQL queries)
	"""
	if ts is None:
		return None

	frappe_tz = get_timezone(frappe.utils.get_system_timezone())
	wp_tz = get_timezone(wp_tz_name)

	# Localize and convert
	if ts.tzinfo is None:
		ts = frappe_tz.localize(ts)

	return ts.astimezone(wp_tz).replace(tzinfo=None)


def sync_table(wp_table_doc):
	"""
	Main entry point for syncing a WordPress table to Frappe.

	Reads sync settings from the WP Tables document and dispatches
	to the appropriate sync method (TS Compare or Truncate & Replace).

	Args:
		wp_table_doc: WP Tables document

	Returns:
		dict with sync results (rows_synced, rows_deleted, etc.)
	"""
	if wp_table_doc.mirror_status not in ("Mirrored", "Linked"):
		frappe.throw(_("Table must be in Mirrored or Linked status before syncing"))

	if not wp_table_doc.frappe_doctype:
		frappe.throw(_("No Frappe DocType associated with this table"))

	# Get WordPress connection
	wp_conn_doc = frappe.get_single("WordPress Connection")
	if not wp_conn_doc:
		frappe.throw(_("WordPress Connection not configured"))

	# Determine sync method
	sync_method = wp_table_doc.sync_method or "TS Compare"

	# Get effective timestamp field
	ts_field = _get_effective_ts_field(wp_table_doc)

	frappe_doctype = wp_table_doc.frappe_doctype

	with wp_connection(wp_conn_doc) as conn:
		if sync_method == "Truncate & Replace":
			result = _sync_truncate_replace(conn, wp_table_doc, wp_conn_doc, frappe_doctype)
		else:
			# Default to TS Compare
			result = _sync_ts_compare(conn, wp_table_doc, wp_conn_doc, frappe_doctype, ts_field)

	# Reverse sync: push Frappe-created records back to WordPress
	# Only runs when direction is explicitly "Both" and the WP PK maps to Frappe name
	sync_direction = getattr(wp_table_doc, "sync_direction", "WP to Frappe") or "WP to Frappe"
	if sync_direction != "WP to Frappe" and getattr(wp_table_doc, "name_field_column", None):
		from nce_sync.utils.reverse_sync import sync_frappe_to_wp

		reverse_result = sync_frappe_to_wp(wp_table_doc)
		result["reverse_inserted"] = reverse_result.get("inserted", 0)
		result["reverse_updated"] = reverse_result.get("updated", 0)
		result["reverse_errors"] = reverse_result.get("errors", 0)

	return result


def _get_effective_ts_field(wp_table_doc):
	"""
	Returns the timestamp field to use for sync.

	Uses modified_timestamp_field if available, falls back to created_timestamp_field.
	For Truncate & Replace, returns None (not needed).

	Args:
		wp_table_doc: WP Tables document

	Returns:
		str: Field name or None
	"""
	if wp_table_doc.sync_method == "Truncate & Replace":
		return None

	ts_field = wp_table_doc.modified_timestamp_field or wp_table_doc.created_timestamp_field

	if not ts_field:
		frappe.throw(
			_(
				"No timestamp field configured for table '{0}'. "
				"Either set a timestamp field or use 'Truncate & Replace' sync method."
			).format(wp_table_doc.table_name)
		)

	return ts_field


def _convert_wp_ts_to_frappe_tz(ts, wp_tz_name):
	"""
	Convert a WordPress timestamp to Frappe server timezone.

	Args:
		ts: datetime object or string from WordPress
		wp_tz_name: WordPress timezone name (e.g., 'America/New_York')

	Returns:
		datetime in Frappe server timezone
	"""
	if ts is None:
		return None

	if isinstance(ts, str):
		ts = datetime.fromisoformat(ts)

	wp_tz = get_timezone(wp_tz_name)
	frappe_tz = get_timezone(frappe.utils.get_system_timezone())

	# Localize WordPress timestamp and convert to Frappe timezone
	if ts.tzinfo is None:
		ts = wp_tz.localize(ts)

	return ts.astimezone(frappe_tz).replace(tzinfo=None)


def _get_matching_keys(wp_table_doc):
	"""
	Parse matching_fields from the WP Tables document.
	Converts WP column names to Frappe fieldnames using column_mapping.
	When name_field_column is set, returns ["name"] for fast direct lookup.

	Args:
		wp_table_doc: WP Tables document

	Returns:
		list of Frappe fieldnames to use as matching keys
	"""
	# When name_field_column is set, use direct name lookup (faster)
	name_field_column = getattr(wp_table_doc, "name_field_column", None)
	if name_field_column:
		return ["name"]

	if not wp_table_doc.matching_fields:
		frappe.throw(
			_(
				"No matching fields configured for table '{0}'. Please re-mirror and select matching fields."
			).format(wp_table_doc.table_name)
		)

	# Get WP column names from matching_fields
	wp_columns = [f.strip() for f in wp_table_doc.matching_fields.split(",") if f.strip()]

	# Convert to Frappe fieldnames using column_mapping
	column_mapping = load_column_mapping(wp_table_doc)

	return [get_frappe_fieldname(wp_col, column_mapping) for wp_col in wp_columns]


def _get_wp_key_set(conn, table_name, matching_keys, reverse_mapping, column_mapping):
	"""
	Fetch all matching key values from WordPress and build a set for comparison.

	Args:
		conn: PyMySQL connection
		table_name: WordPress table name
		matching_keys: List of Frappe fieldnames to match on
		reverse_mapping: Dict mapping Frappe fieldnames to WP column names
		column_mapping: Dict mapping WP column names to Frappe fieldnames

	Returns:
		Set of key tuples (normalized to strings)
	"""
	# Build WP column list for query
	wp_key_columns = []
	for frappe_key in matching_keys:
		wp_col = reverse_mapping.get(frappe_key, frappe_key)
		wp_key_columns.append(f"`{wp_col}`")

	cursor = conn.cursor()
	cursor.execute(f"SELECT {', '.join(wp_key_columns)} FROM `{table_name}`")
	wp_rows = cursor.fetchall()
	cursor.close()

	# Build set of normalized key tuples
	wp_key_set = set()
	for row in wp_rows:
		converted_row = _convert_row(row, None, column_mapping)
		key_tuple = tuple(_normalize_key_value(converted_row.get(k)) for k in matching_keys)
		wp_key_set.add(key_tuple)

	return wp_key_set


def _get_cutoff_timestamp(frappe_doctype, frappe_ts_field, wp_tz, fallback_ts_field=None):
	"""
	Get the cutoff timestamp for incremental sync by finding the latest
	effective timestamp already stored in Frappe.

	Uses GREATEST(COALESCE(mod_ts, create_ts), create_ts) so that rows
	with NULL modified_ts are handled via their created_ts — mirroring
	the same logic applied on the WP query side.

	When no modified_ts field exists at all, uses created_ts directly.

	Args:
		frappe_doctype: Name of the Frappe DocType
		frappe_ts_field: Frappe fieldname for the modified timestamp (may be None)
		wp_tz: WordPress timezone name
		fallback_ts_field: Frappe fieldname for the created timestamp (may be None)

	Returns:
		Cutoff datetime in WP timezone, or None if no data exists
	"""
	if not frappe_ts_field and not fallback_ts_field:
		return None

	if frappe_ts_field and fallback_ts_field and frappe_ts_field != fallback_ts_field:
		ts_expr = f"GREATEST(COALESCE(`{frappe_ts_field}`, `{fallback_ts_field}`), `{fallback_ts_field}`)"
	elif frappe_ts_field:
		ts_expr = f"`{frappe_ts_field}`"
	else:
		ts_expr = f"`{fallback_ts_field}`"

	max_ts_result = frappe.db.sql(f"SELECT MAX({ts_expr}) FROM `tab{frappe_doctype}`")
	max_ts = max_ts_result[0][0] if max_ts_result else None

	if not max_ts:
		return None

	if isinstance(max_ts, str):
		max_ts = datetime.fromisoformat(max_ts)

	cutoff = max_ts

	# Convert from Frappe TZ to WP TZ for the query
	return convert_frappe_ts_to_wp_tz(cutoff, wp_tz)


def _publish_sync_progress(table_name, rows_processed, total_rows, user=None):
	"""
	Publish sync progress as toast notifications via realtime.
	Targets the user who triggered the sync so toasts reach their browser.

	Args:
		table_name: WP Tables document name
		rows_processed: Number of rows processed so far
		total_rows: Total rows expected
		user: Username to target (required for background jobs; worker has no session)
	"""
	message = f"{table_name}: {rows_processed} of {total_rows} rows uploaded"
	frappe.publish_realtime(
		"msgprint",
		{"message": message, "indicator": "blue", "alert": 1},
		user=user,
	)
	# Also update the doc so progress is visible on page refresh
	frappe.db.set_value(
		"WP Tables",
		table_name,
		"last_sync_log",
		f"Syncing: {rows_processed} of {total_rows} rows uploaded",
		update_modified=False,
	)
	frappe.db.commit()


def _build_ts_expr(ts_field, create_ts_field):
	"""Build a SQL expression that returns the effective timestamp for a row.

	Uses GREATEST of modified and created timestamps.  When modified_ts may be
	NULL, COALESCE ensures we fall back to created_ts.
	"""
	if create_ts_field and create_ts_field != ts_field:
		return f"GREATEST(COALESCE(`{ts_field}`, `{create_ts_field}`), `{create_ts_field}`)"
	return f"COALESCE(`{ts_field}`, '1970-01-01')"


def _count_rows_to_sync(conn, table_name, ts_field, create_ts_field, cutoff):
	"""
	Count how many rows will be synced, using the same WHERE clause as _fetch_changed_rows.

	Args:
		conn: PyMySQL connection
		table_name: WordPress table name
		ts_field: Modified timestamp field name
		create_ts_field: Created timestamp field name (may be None)
		cutoff: Cutoff datetime in WP timezone (None = count all)

	Returns:
		int: Number of rows to sync
	"""
	cursor = conn.cursor()

	if cutoff:
		ts_expr = _build_ts_expr(ts_field, create_ts_field)
		cursor.execute(
			f"SELECT COUNT(*) as cnt FROM `{table_name}` WHERE {ts_expr} > %s",
			(cutoff,),
		)
	else:
		cursor.execute(f"SELECT COUNT(*) as cnt FROM `{table_name}`")

	result = cursor.fetchone()
	cursor.close()
	return result["cnt"] if result else 0


def _fetch_changed_rows(conn, table_name, ts_field, create_ts_field, cutoff):
	"""
	Fetch rows from WordPress that have changed since the cutoff.

	Uses GREATEST(COALESCE(modified_ts, created_ts), created_ts) so rows
	with NULL modified_ts are correctly handled via their created_ts.

	Args:
		conn: PyMySQL connection
		table_name: WordPress table name
		ts_field: Modified timestamp field name
		create_ts_field: Created timestamp field name (may be None)
		cutoff: Cutoff datetime in WP timezone (None = fetch all)

	Returns:
		List of row dicts from WordPress
	"""
	cursor = conn.cursor()

	if cutoff:
		ts_expr = _build_ts_expr(ts_field, create_ts_field)
		cursor.execute(
			f"SELECT * FROM `{table_name}` WHERE {ts_expr} > %s",
			(cutoff,),
		)
	else:
		cursor.execute(f"SELECT * FROM `{table_name}`")

	rows = cursor.fetchall()
	cursor.close()
	return rows


def _fetch_rows_by_keys(conn, table_name, matching_keys, reverse_mapping, column_mapping, key_set):
	"""
	Fetch full rows from WP for a specific set of matching-key tuples.

	For single-column keys uses efficient WHERE IN queries (batched).
	For composite keys falls back to fetching all rows and filtering in Python.

	Args:
		conn: PyMySQL connection
		table_name: WordPress table name
		matching_keys: List of Frappe fieldnames used as matching keys
		reverse_mapping: Dict mapping Frappe fieldnames to WP column names
		column_mapping: Dict mapping WP column names to Frappe fieldnames
		key_set: Set of key tuples to fetch

	Returns:
		List of row dicts from WordPress
	"""
	if not key_set:
		return []

	if len(matching_keys) == 1:
		wp_col = reverse_mapping.get(matching_keys[0], matching_keys[0])
		values = [k[0] for k in key_set if k[0] is not None]
		if not values:
			return []
		rows = []
		cursor = conn.cursor()
		for i in range(0, len(values), WHERE_IN_BATCH_SIZE):
			batch = values[i : i + WHERE_IN_BATCH_SIZE]
			placeholders = ",".join(["%s"] * len(batch))
			cursor.execute(
				f"SELECT * FROM `{table_name}` WHERE `{wp_col}` IN ({placeholders})",
				batch,
			)
			rows.extend(cursor.fetchall())
		cursor.close()
		return rows

	# Composite key — fetch all and filter in Python
	cursor = conn.cursor()
	cursor.execute(f"SELECT * FROM `{table_name}`")
	all_rows = cursor.fetchall()
	cursor.close()

	result = []
	for row in all_rows:
		converted = _convert_row(row, None, column_mapping)
		key_tuple = tuple(_normalize_key_value(converted.get(k)) for k in matching_keys)
		if key_tuple in key_set:
			result.append(row)
	return result


def _sync_ts_compare(conn, wp_table_doc, wp_conn_doc, frappe_doctype, ts_field):
	"""
	Sync using timestamp comparison method.

	Steps:
	1. Pull matching keys from WP, diff against Frappe, delete orphans
	2. Pull changed rows (ts_field > last_synced), convert TZ
	3. Upsert into Frappe DocType by matching key

	Args:
		conn: PyMySQL connection
		wp_table_doc: WP Tables document
		wp_conn_doc: WordPress Connection document
		frappe_doctype: Name of the Frappe DocType
		ts_field: Timestamp field name for comparison

	Returns:
		dict with sync results
	"""
	table_name = wp_table_doc.table_name
	matching_keys = _get_matching_keys(wp_table_doc)
	wp_tz = wp_conn_doc.wp_timezone
	sync_user = getattr(wp_table_doc, "_sync_user", None)

	# Load column mapping (WP column name -> {fieldname, is_virtual})
	column_mapping = load_column_mapping(wp_table_doc)

	# Build reverse mapping for looking up WP column names from Frappe fieldnames
	reverse_mapping = build_reverse_mapping(column_mapping)

	# Step 1: Delete detection — get all matching keys from WP and delete orphans.
	# Also returns the set of keys currently in Frappe (after deletes).
	wp_key_set = _get_wp_key_set(conn, table_name, matching_keys, reverse_mapping, column_mapping)
	rows_deleted, frappe_key_set = _delete_orphans(frappe_doctype, matching_keys, wp_key_set)

	# Step 2a: Identify WP rows that are completely missing from Frappe
	missing_keys = wp_key_set - frappe_key_set

	# Step 2b: Get rows changed since last sync (timestamp-based)
	frappe_ts_field = get_frappe_fieldname(ts_field, column_mapping) if ts_field else None
	frappe_create_ts_field = (
		get_frappe_fieldname(wp_table_doc.created_timestamp_field, column_mapping)
		if wp_table_doc.created_timestamp_field
		else None
	)
	cutoff = _get_cutoff_timestamp(
		frappe_doctype, frappe_ts_field, wp_tz, fallback_ts_field=frappe_create_ts_field
	)

	changed_rows = _fetch_changed_rows(
		conn, table_name, ts_field, wp_table_doc.created_timestamp_field, cutoff
	)

	# Build set of keys already covered by the TS-changed fetch so we
	# don't double-count or double-process them in the missing pass.
	changed_keys = set()
	for row in changed_rows:
		converted = _convert_row(row, None, column_mapping)
		key_tuple = tuple(_normalize_key_value(converted.get(k)) for k in matching_keys)
		changed_keys.add(key_tuple)

	# Step 2c: Fetch missing rows that were NOT already in the TS-changed set
	# (these have old timestamps but were never synced into Frappe)
	missing_keys_only = missing_keys - changed_keys
	missing_rows = _fetch_rows_by_keys(
		conn, table_name, matching_keys, reverse_mapping, column_mapping, missing_keys_only
	)

	total_to_sync = len(changed_rows) + len(missing_rows)

	# Step 3: Upsert — first the TS-changed rows, then the missing rows
	rows_upserted = 0
	rows_inserted = 0
	rows_skipped = 0
	skip_errors = []

	def _upsert_batch(rows):
		nonlocal rows_upserted, rows_inserted, rows_skipped, skip_errors
		for i in range(0, len(rows), UPSERT_BATCH_SIZE):
			batch = rows[i : i + UPSERT_BATCH_SIZE]
			for row in batch:
				row_hint = str(dict(list(row.items())[:4]))[:150]
				frappe.db.savepoint("row_sync")
				try:
					converted_row = _convert_row(row, wp_tz, column_mapping)
					was_new = _upsert_record(frappe_doctype, matching_keys, converted_row)
					rows_upserted += 1
					if was_new:
						rows_inserted += 1
				except Exception as e:
					rows_skipped += 1
					if len(skip_errors) < MAX_ROW_ERROR_MESSAGES:
						skip_errors.append(f"{row_hint} — {str(e)[:180]}")
					try:
						frappe.db.rollback(save_point="row_sync")
					except Exception:
						pass
				if (rows_upserted + rows_skipped) % UPSERT_BATCH_SIZE == 0:
					_publish_sync_progress(
						wp_table_doc.name,
						rows_upserted,
						total_to_sync,
						user=sync_user,
					)
			frappe.db.commit()

	_upsert_batch(changed_rows)
	if missing_rows:
		_upsert_batch(missing_rows)

	if rows_skipped:
		frappe.log_error(
			title=f"Sync skipped rows: {wp_table_doc.table_name}",
			message=f"Skipped {rows_skipped} rows.\n" + "\n".join(skip_errors),
		)

	# Final progress update (always fires so small tables get at least one toast)
	_publish_sync_progress(
		wp_table_doc.name,
		rows_upserted,
		total_to_sync,
		user=sync_user,
	)

	return {
		"method": "TS Compare",
		"rows_upserted": rows_upserted,
		"rows_inserted": rows_inserted,
		"rows_deleted": rows_deleted,
		"rows_skipped": rows_skipped,
		"total_wp_rows": len(wp_key_set),
		"missing_rows_found": len(missing_rows),
	}


def _sync_truncate_replace(conn, wp_table_doc, wp_conn_doc, frappe_doctype):
	"""
	Sync using truncate and replace method.

	Deletes all Frappe records and re-inserts from WordPress.

	Args:
		conn: PyMySQL connection
		wp_table_doc: WP Tables document
		wp_conn_doc: WordPress Connection document
		frappe_doctype: Name of the Frappe DocType

	Returns:
		dict with sync results
	"""
	table_name = wp_table_doc.table_name
	wp_tz = wp_conn_doc.wp_timezone

	# Load column mapping (WP column name -> {fieldname, is_virtual})
	column_mapping = load_column_mapping(wp_table_doc)

	# Step 1: Delete all existing Frappe records
	frappe.db.delete(frappe_doctype)
	frappe.db.commit()

	# Step 2: Pre-count and get all rows from WordPress
	total_to_sync = _count_rows_to_sync(conn, table_name, None, None, None)

	cursor = conn.cursor()
	cursor.execute(f"SELECT * FROM `{table_name}`")
	all_rows = cursor.fetchall()
	cursor.close()

	# Step 3: Insert all rows in batches (with in_sync flag to prevent live push-back)
	rows_inserted = 0
	rows_skipped = 0
	skip_errors = []
	frappe.flags.in_sync = True
	try:
		for i in range(0, len(all_rows), UPSERT_BATCH_SIZE):
			batch = all_rows[i : i + UPSERT_BATCH_SIZE]

			for row in batch:
				row_hint = str(dict(list(row.items())[:4]))[:150]
				frappe.db.savepoint("row_sync")
				try:
					converted_row = _convert_row(row, wp_tz, column_mapping)
					_insert_record(frappe_doctype, converted_row)
					rows_inserted += 1
				except Exception as e:
					rows_skipped += 1
					if len(skip_errors) < MAX_ROW_ERROR_MESSAGES:
						skip_errors.append(f"{row_hint} — {str(e)[:180]}")
					try:
						frappe.db.rollback(save_point="row_sync")
					except Exception:
						pass

				if (rows_inserted + rows_skipped) % UPSERT_BATCH_SIZE == 0:
					_publish_sync_progress(
						wp_table_doc.name,
						rows_inserted,
						total_to_sync,
						user=getattr(wp_table_doc, "_sync_user", None),
					)

			frappe.db.commit()
	finally:
		frappe.flags.in_sync = False

	if rows_skipped:
		frappe.log_error(
			title=f"Sync skipped rows: {wp_table_doc.table_name}",
			message=f"Skipped {rows_skipped} rows.\n" + "\n".join(skip_errors),
		)

	# Final progress update (always fires)
	_publish_sync_progress(
		wp_table_doc.name,
		rows_inserted,
		total_to_sync,
		user=getattr(wp_table_doc, "_sync_user", None),
	)

	return {
		"method": "Truncate & Replace",
		"rows_inserted": rows_inserted,
		"rows_deleted": "all",
	}


def _convert_row(row, wp_tz, column_mapping=None):
	"""
	Convert a WordPress row for insertion into Frappe:
	- Maps WP column names to Frappe fieldnames using the stored mapping
	- Converts datetime fields from WP timezone to Frappe timezone

	Args:
		row: dict of column values from WordPress
		wp_tz: WordPress timezone name
		column_mapping: dict mapping WP column names to Frappe fieldnames
		                (can be old format: {wp_col: fieldname} or
		                 new format: {wp_col: {fieldname: ..., is_virtual: ...}})

	Returns:
		dict with Frappe fieldnames as keys
	"""
	converted = {}
	for wp_key, value in row.items():
		# Skip virtual/generated columns — they have no Frappe DB column
		if column_mapping and wp_key in column_mapping:
			mapping_info = column_mapping[wp_key]
			if isinstance(mapping_info, dict) and mapping_info.get("is_virtual"):
				continue

		frappe_key = get_frappe_fieldname(wp_key, column_mapping)

		if isinstance(value, datetime):
			converted[frappe_key] = _convert_wp_ts_to_frappe_tz(value, wp_tz)
		elif frappe_key == "name" and value is not None:
			# Frappe name is always varchar — cast to str so integer PKs
			# (e.g. WP auto_increment id) match correctly on subsequent syncs
			converted[frappe_key] = str(value)
		else:
			converted[frappe_key] = value
	return converted


def _upsert_record(frappe_doctype, matching_keys, row_data):
	"""
	Insert or update a single Frappe document by matching key lookup.
	When matching_keys is ["name"], uses direct frappe.db.exists for faster lookup.

	Sets frappe.flags.in_sync = True while saving so the live_sync hook
	does not push the inbound WP data back out.

	Args:
		frappe_doctype: Name of the Frappe DocType
		matching_keys: List of field names to match on
		row_data: Dict of field values from WordPress

	Returns:
		bool: True if a new record was inserted, False if an existing one was updated
	"""
	# Check if record exists - use direct name lookup when matching on name (faster)
	if matching_keys == ["name"]:
		name_value = row_data.get("name")
		# Always coerce to str — Frappe name is varchar, WP PK may be an integer
		if name_value is not None:
			name_value = str(name_value)
			row_data["name"] = name_value
		existing = frappe.db.exists(frappe_doctype, name_value) if name_value is not None else None
	else:
		filters = {key: row_data.get(key) for key in matching_keys}
		existing = frappe.db.get_value(frappe_doctype, filters, "name")

	# Get valid field names from DocType meta
	valid_fields = {df.fieldname for df in frappe.get_meta(frappe_doctype).fields}

	frappe.flags.in_sync = True
	try:
		if existing:
			doc = frappe.get_doc(frappe_doctype, existing)
			for key, value in row_data.items():
				if key in valid_fields and key != "name":
					doc.set(key, value)
			doc.flags.ignore_permissions = True
			doc.flags.ignore_mandatory = True
			doc.flags.ignore_links = True
			doc.save()
			return False
		else:
			_insert_record(frappe_doctype, row_data)
			return True
	finally:
		frappe.flags.in_sync = False


def _insert_record(frappe_doctype, row_data):
	"""
	Insert a new Frappe document.

	Args:
		frappe_doctype: Name of the Frappe DocType
		row_data: Dict of field values from WordPress
	"""
	doc = frappe.new_doc(frappe_doctype)

	# Get valid field names from DocType meta
	valid_fields = {df.fieldname for df in frappe.get_meta(frappe_doctype).fields}
	valid_fields.add("name")  # name is always valid

	for key, value in row_data.items():
		if key in valid_fields:
			# Frappe name is varchar — guard against integer values from WP PKs
			if key == "name" and value is not None:
				value = str(value)
			doc.set(key, value)

	doc.flags.ignore_permissions = True
	doc.flags.ignore_mandatory = True
	doc.flags.ignore_links = True
	doc.insert()


def _delete_orphans(frappe_doctype, matching_keys, wp_key_set):
	"""
	Delete Frappe records whose matching keys are not in the WordPress key set.

	Skips records with negative integer names — those are new records created
	locally in Frappe (temp IDs) that have not yet been pushed to WordPress.
	Only deletes records with real positive WP IDs that no longer exist in the source.

	Args:
		frappe_doctype: Name of the Frappe DocType
		matching_keys: List of field names to match on
		wp_key_set: Set of key tuples from WordPress

	Returns:
		tuple: (deleted_count, frappe_key_set) where frappe_key_set contains
		       all non-temp key tuples currently in Frappe
	"""
	frappe_records = frappe.get_all(frappe_doctype, fields=["name", *matching_keys], limit_page_length=0)

	deleted_count = 0
	frappe_key_set = set()

	for record in frappe_records:
		# Skip temp records (negative integer names) — not yet pushed to WP
		try:
			if int(record.name) < 0:
				continue
		except (ValueError, TypeError):
			pass

		frappe_key = tuple(_normalize_key_value(record.get(k)) for k in matching_keys)

		if frappe_key not in wp_key_set:
			frappe.delete_doc(frappe_doctype, record.name, force=True, ignore_permissions=True)
			deleted_count += 1
		else:
			frappe_key_set.add(frappe_key)

	if deleted_count > 0:
		frappe.db.commit()

	return deleted_count, frappe_key_set


def _get_sync_frequency_minutes():
	"""
	Get the global sync frequency from Sync Manager in minutes.

	Returns:
		int: Frequency in minutes (default 60)
	"""
	try:
		sync_manager = frappe.get_single("Sync Manager")
		return SYNC_FREQUENCY_MAP.get(sync_manager.sync_frequency, 60)
	except Exception:
		return 60  # Default to hourly


def run_scheduled_syncs():
	"""
	Scheduler entry point: sync all tables that are due.

	Called by Frappe scheduler based on hooks.py configuration.
	Checks each WP Table with auto_sync_active=1 and syncs
	if enough time has passed since last_synced.

	Uses global sync frequency from Sync Manager.
	Also updates Sync Manager status and cleans up old Sync Log records.
	"""
	# Check if syncing is globally enabled
	try:
		sync_manager = frappe.get_single("Sync Manager")
		if sync_manager.syncing_active != "Yes":
			return  # Global sync is disabled
	except Exception:
		return  # Sync Manager not configured

	# Get global sync frequency
	sync_frequency = _get_sync_frequency_minutes()

	# Get all tables eligible for auto-sync
	tables = frappe.get_all(
		"WP Tables",
		filters={
			"auto_sync_active": 1,
			"mirror_status": ["in", ["Mirrored", "Linked"]],
		},
		fields=["name", "table_name", "last_synced"],
	)

	now = now_datetime()
	tables_synced = 0
	tables_failed = 0
	log_messages = []

	for table_info in tables:
		try:
			# Check if sync is due
			last_synced = table_info.last_synced

			if last_synced:
				if isinstance(last_synced, str):
					last_synced = datetime.fromisoformat(last_synced)
				time_since_sync = (now - last_synced).total_seconds() / 60
				if time_since_sync < sync_frequency:
					continue  # Not due yet

			# Sync is due - run it
			wp_table_doc = frappe.get_doc("WP Tables", table_info.name)
			_run_sync_with_status(wp_table_doc)
			tables_synced += 1
			log_messages.append(f"✓ {table_info.table_name}")

		except Exception as e:
			# Log error but continue with other tables
			tables_failed += 1
			log_messages.append(f"✗ {table_info.table_name}: {str(e)[:100]}")
			frappe.log_error(title=f"Scheduled Sync Error: {table_info.table_name}", message=str(e))

	# Update Sync Manager status
	_update_sync_manager_status(sync_manager, sync_frequency, tables_synced, tables_failed, log_messages)

	# Cleanup old Sync Log records
	_cleanup_old_sync_logs(keep_count=KEEP_SYNC_LOG_COUNT)


def _update_sync_manager_status(sync_manager, sync_frequency, tables_synced, tables_failed, log_messages):
	"""
	Update Sync Manager with run status.

	Args:
		sync_manager: Sync Manager document
		sync_frequency: Frequency in minutes
		tables_synced: Count of successfully synced tables
		tables_failed: Count of failed syncs
		log_messages: List of log message strings
	"""
	now = now_datetime()

	sync_manager.last_run = now
	sync_manager.next_scheduled_run = now + timedelta(minutes=sync_frequency)

	if tables_failed == 0 and tables_synced > 0:
		sync_manager.last_run_status = "Success"
	elif tables_failed > 0 and tables_synced > 0:
		sync_manager.last_run_status = "Partial"
	elif tables_failed > 0:
		sync_manager.last_run_status = "Failed"
	else:
		sync_manager.last_run_status = "Success"  # No tables due

	# Build log summary
	if log_messages:
		sync_manager.last_run_log = f"Synced: {tables_synced}, Failed: {tables_failed}\n" + "\n".join(
			log_messages
		)
	else:
		sync_manager.last_run_log = "No tables due for sync"

	sync_manager.save(ignore_permissions=True)
	frappe.db.commit()


def _cleanup_old_sync_logs(keep_count=20):
	"""
	Delete old Sync Log records, keeping only the most recent ones.

	Args:
		keep_count: Number of recent records to keep (default 20)
	"""
	# Get all Sync Log names ordered by creation (newest first)
	all_logs = frappe.get_all(
		"Sync Log",
		fields=["name"],
		order_by="creation desc",
		limit_page_length=0,
	)

	# Delete records beyond keep_count
	if len(all_logs) > keep_count:
		logs_to_delete = all_logs[keep_count:]
		for log in logs_to_delete:
			frappe.delete_doc("Sync Log", log.name, force=True, ignore_permissions=True)

		frappe.db.commit()


def run_sync_for_table(wp_table_name, user=None):
	"""
	Background-job entry point: load the WP Tables doc by name and sync it.
	Sends toast notifications on completion or error to the user who triggered it.

	Args:
		wp_table_name: Name (primary key) of the WP Tables document
		user: Username to receive progress toasts (from frappe.session.user when enqueued)
	"""
	wp_table_doc = frappe.get_doc("WP Tables", wp_table_name)
	wp_table_doc._sync_user = user or frappe.session.user
	label = wp_table_doc.nce_name or wp_table_doc.table_name

	try:
		_run_sync_with_status(wp_table_doc, suppress_notifications=True)

		frappe.db.commit()
		frappe.publish_realtime(
			"msgprint",
			{"message": f"{label}: Sync complete ✓", "indicator": "green", "alert": True},
			user=wp_table_doc._sync_user,
		)
	except Exception as e:
		frappe.db.commit()
		frappe.publish_realtime(
			"msgprint",
			{"message": f"{label}: Sync failed — {str(e)[:120]}", "indicator": "red", "alert": True},
			user=wp_table_doc._sync_user,
		)
		raise
	finally:
		# Tell the open form to reload so the status badge updates
		frappe.publish_realtime(
			"doc_update",
			{"doctype": "WP Tables", "name": wp_table_name},
			doctype="WP Tables",
			docname=wp_table_name,
		)


def _run_sync_with_status(wp_table_doc, suppress_notifications=False):
	"""
	Run sync and update status fields on the WP Tables document.
	Also creates a Sync Log record for audit trail.

	Args:
		wp_table_doc: WP Tables document
	"""
	import traceback

	# Set status to Running
	wp_table_doc.last_sync_status = "Running"
	wp_table_doc.last_sync_log = "Sync started..."
	wp_table_doc.save()
	frappe.db.commit()

	sync_started = now_datetime()
	sync_method = wp_table_doc.sync_method or "TS Compare"

	if suppress_notifications:
		frappe.flags.in_import = True
		frappe.flags.mute_emails = True

	try:
		result = sync_table(wp_table_doc)

		# Check for anomaly: WP has rows but Frappe table is empty after sync
		total_wp_rows = result.get("total_wp_rows", 0) or result.get("rows_inserted", 0)
		frappe_count = frappe.db.count(wp_table_doc.frappe_doctype)

		if total_wp_rows > 0 and frappe_count == 0:
			wp_table_doc.last_sync_status = "Warning"
			wp_table_doc.last_sync_log = (
				f"ANOMALY: WP has {total_wp_rows} rows but Frappe table is empty. "
				f"last_synced NOT updated - next sync will do full pull. "
				f"Check matching keys and column mapping."
			)
			wp_table_doc.save()

			_create_sync_log(
				wp_table_doc.name,
				sync_method,
				sync_started,
				status="Partial",
				error_message=wp_table_doc.last_sync_log,
			)
			return

		# Calculate record counts
		rows_upserted = result.get("rows_upserted", 0)
		rows_inserted = result.get("rows_inserted", 0)
		rows_deleted = result.get("rows_deleted", 0)
		if rows_deleted == "all":
			rows_deleted = 0
		reverse_inserted = result.get("reverse_inserted", 0)
		reverse_updated = result.get("reverse_updated", 0)

		has_changes = (rows_upserted + rows_deleted + reverse_inserted + reverse_updated) > 0

		# Update status on success
		wp_table_doc.last_synced = now_datetime()
		wp_table_doc.last_sync_status = "Success"

		# Build summary log
		if result.get("method") == "Truncate & Replace":
			wp_table_doc.last_sync_log = f"Truncate & Replace: {rows_inserted} rows inserted"
			records_synced = rows_inserted
			records_created = rows_inserted
			records_updated = 0
		else:
			missing_found = result.get("missing_rows_found", 0)
			ts_rows_inserted = result.get("rows_inserted", 0)
			ts_rows_updated = rows_upserted - ts_rows_inserted

			parts = [f"TS Compare: {rows_upserted} upserted"]
			if ts_rows_inserted:
				parts.append(f"{ts_rows_inserted} new")
			if ts_rows_updated:
				parts.append(f"{ts_rows_updated} updated")
			if missing_found:
				parts.append(f"{missing_found} missing rows recovered")
			parts.append(f"{rows_deleted} deleted")
			parts.append(f"{result.get('total_wp_rows', 0)} total WP rows")
			parts.append(f"{frappe_count} in Frappe")

			wp_table_doc.last_sync_log = ", ".join(parts)
			records_synced = rows_upserted
			records_created = ts_rows_inserted
			records_updated = ts_rows_updated

		wp_table_doc.save()
		frappe.db.commit()

		# Only create a Sync Log record when something actually changed
		if has_changes:
			_create_sync_log(
				wp_table_doc.name,
				sync_method,
				sync_started,
				status="Success",
				records_synced=records_synced,
				records_created=records_created,
				records_updated=records_updated,
				records_deleted=rows_deleted if isinstance(rows_deleted, int) else 0,
			)

	except Exception as e:
		wp_table_doc.last_sync_status = "Error"
		wp_table_doc.last_sync_log = str(e)[:500]
		wp_table_doc.save()

		_create_sync_log(
			wp_table_doc.name,
			sync_method,
			sync_started,
			status="Failed",
			error_message=str(e)[:500],
			error_traceback=traceback.format_exc(),
		)

		frappe.log_error(title=f"Sync Error: {wp_table_doc.table_name}", message=str(e))
		raise

	finally:
		if suppress_notifications:
			frappe.flags.in_import = False
			frappe.flags.mute_emails = False


def _create_sync_log(
	wp_table_name,
	sync_method,
	sync_started,
	status="Success",
	records_synced=0,
	records_created=0,
	records_updated=0,
	records_deleted=0,
	error_message=None,
	error_traceback=None,
):
	"""Create a Sync Log record. Called only when there are actual changes or errors."""
	sync_log = frappe.new_doc("Sync Log")
	sync_log.wp_table = wp_table_name
	sync_log.sync_method = sync_method
	sync_log.status = status
	sync_log.sync_started = sync_started
	sync_log.sync_completed = now_datetime()
	sync_log.duration_seconds = (sync_log.sync_completed - sync_started).total_seconds()
	sync_log.records_synced = records_synced
	sync_log.records_created = records_created
	sync_log.records_updated = records_updated
	sync_log.records_deleted = records_deleted
	if error_message:
		sync_log.error_message = error_message
	if error_traceback:
		sync_log.error_traceback = error_traceback
	sync_log.insert(ignore_permissions=True)
	frappe.db.commit()
