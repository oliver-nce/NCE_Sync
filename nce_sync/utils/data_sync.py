# Copyright (c) 2026, Oliver Reid and contributors
# For license information, please see license.txt

"""
Data synchronization utilities for NCE_Sync.
Handles bidirectional sync between WordPress tables and Frappe DocTypes.
Primary direction: WordPress → Frappe (TS Compare / Truncate & Replace).
Reverse direction: Frappe → WordPress (INSERT new records, UPDATE existing).
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import frappe
import pytz
from frappe import _
from frappe.utils import now_datetime

from nce_sync.utils.column_mapper import (
	build_reverse_mapping,
	get_frappe_fieldname,
	load_column_mapping,
)
from nce_sync.utils.connections import wp_connection
from nce_sync.utils.constants import (
	KEEP_SYNC_LOG_COUNT,
	MAX_ROW_ERROR_MESSAGES,
	SYNC_FREQUENCY_MAP,
	UPSERT_BATCH_SIZE,
	WHERE_IN_BATCH_SIZE,
)


# ---------------------------------------------------------------------------
# SyncContext — bundles the resolved state passed through sync helpers
# ---------------------------------------------------------------------------


@dataclass
class SyncContext:
	"""Bundles the resolved state needed by sync helper functions.

	Created once at the start of ``sync_table()`` and passed through to all
	internal helpers, replacing 5–10 individual parameters with a single
	``ctx`` argument.

	Attributes:
		conn: Active PyMySQL connection (managed externally via context manager).
		wp_table_doc: The WP Tables Frappe document driving this sync.
		wp_conn_doc: The WordPress Connection singleton.
		frappe_doctype: Target Frappe DocType name (e.g. ``"WP Orders"``).
		table_name: WordPress table name (e.g. ``"wp_wc_orders"``).
		wp_tz: WordPress timezone name (e.g. ``"America/New_York"``).
		column_mapping: Parsed column mapping ``{wp_col: {fieldname, …}}``.
		reverse_mapping: Reverse mapping ``{frappe_field: wp_col}``.
		matching_keys: Parsed matching keys (list of Frappe fieldnames).
		ts_field: WP modified-timestamp column (TS Compare only; None otherwise).
		create_ts_field: WP created-timestamp column (may be None).
		frappe_ts_field: Frappe fieldname for the modified timestamp.
		frappe_create_ts_field: Frappe fieldname for the created timestamp.
		sync_user: Username to target for realtime progress toasts.
	"""

	conn: Any
	wp_table_doc: Any
	wp_conn_doc: Any
	frappe_doctype: str
	table_name: str
	wp_tz: str
	column_mapping: Dict
	reverse_mapping: Dict
	matching_keys: List[str]
	ts_field: Optional[str] = None
	create_ts_field: Optional[str] = None
	frappe_ts_field: Optional[str] = None
	frappe_create_ts_field: Optional[str] = None
	sync_user: Optional[str] = None


# ---------------------------------------------------------------------------
# Sync-method strategy registry
# ---------------------------------------------------------------------------
# Each strategy is a callable(ctx: SyncContext) → dict with sync results.
# Register new sync methods by adding an entry to SYNC_STRATEGIES.
# The key must match the "Sync Method" select value on the WP Tables DocType.

SYNC_STRATEGIES: Dict[str, Any] = {}  # populated after function definitions


def _register_sync_strategy(name):
	"""Decorator that registers a sync-method implementation.

	Usage::

		@_register_sync_strategy("My Method")
		def _sync_my_method(ctx: SyncContext) -> dict:
			...
	"""

	def decorator(fn):
		SYNC_STRATEGIES[name] = fn
		return fn

	return decorator


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

	# Resolve all derived state once and bundle into SyncContext
	frappe_doctype = wp_table_doc.frappe_doctype
	column_mapping = load_column_mapping(wp_table_doc)
	reverse_mapping = build_reverse_mapping(column_mapping)
	matching_keys = _get_matching_keys(wp_table_doc)
	create_ts_field = wp_table_doc.created_timestamp_field or None

	frappe_ts_field = (
		get_frappe_fieldname(ts_field, column_mapping) if ts_field else None
	)
	frappe_create_ts_field = (
		get_frappe_fieldname(create_ts_field, column_mapping) if create_ts_field else None
	)

	with wp_connection(wp_conn_doc) as conn:
		ctx = SyncContext(
			conn=conn,
			wp_table_doc=wp_table_doc,
			wp_conn_doc=wp_conn_doc,
			frappe_doctype=frappe_doctype,
			table_name=wp_table_doc.table_name,
			wp_tz=wp_conn_doc.wp_timezone,
			column_mapping=column_mapping,
			reverse_mapping=reverse_mapping,
			matching_keys=matching_keys,
			ts_field=ts_field,
			create_ts_field=create_ts_field,
			frappe_ts_field=frappe_ts_field,
			frappe_create_ts_field=frappe_create_ts_field,
			sync_user=getattr(wp_table_doc, "_sync_user", None),
		)

		strategy = SYNC_STRATEGIES.get(sync_method)
		if not strategy:
			frappe.throw(
				_("Unknown sync method '{0}'. Available: {1}").format(
					sync_method, ", ".join(sorted(SYNC_STRATEGIES.keys()))
				)
			)
		result = strategy(ctx)

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


def _get_wp_key_set(ctx):
	"""
	Fetch all matching key values from WordPress and build a set for comparison.

	Args:
		ctx: SyncContext

	Returns:
		Set of key tuples (normalized to strings)
	"""
	# Build WP column list for query
	wp_key_columns = []
	for frappe_key in ctx.matching_keys:
		wp_col = ctx.reverse_mapping.get(frappe_key, frappe_key)
		wp_key_columns.append(f"`{wp_col}`")

	cursor = ctx.conn.cursor()
	cursor.execute(f"SELECT {', '.join(wp_key_columns)} FROM `{ctx.table_name}`")
	wp_rows = cursor.fetchall()
	cursor.close()

	# Build set of normalized key tuples
	wp_key_set = set()
	for row in wp_rows:
		converted_row = _convert_row(row, None, ctx.column_mapping)
		key_tuple = tuple(_normalize_key_value(converted_row.get(k)) for k in ctx.matching_keys)
		wp_key_set.add(key_tuple)

	return wp_key_set


def _get_cutoff_timestamp(ctx):
	"""
	Get the cutoff timestamp for incremental sync by finding the latest
	effective timestamp already stored in Frappe.

	Uses GREATEST(COALESCE(mod_ts, create_ts), create_ts) so that rows
	with NULL modified_ts are handled via their created_ts — mirroring
	the same logic applied on the WP query side.

	When no modified_ts field exists at all, uses created_ts directly.

	Args:
		ctx: SyncContext (uses frappe_doctype, frappe_ts_field,
		     frappe_create_ts_field, wp_tz)

	Returns:
		Cutoff datetime in WP timezone, or None if no data exists
	"""
	frappe_ts_field = ctx.frappe_ts_field
	fallback_ts_field = ctx.frappe_create_ts_field

	if not frappe_ts_field and not fallback_ts_field:
		return None

	if frappe_ts_field and fallback_ts_field and frappe_ts_field != fallback_ts_field:
		ts_expr = f"GREATEST(COALESCE(`{frappe_ts_field}`, `{fallback_ts_field}`), `{fallback_ts_field}`)"
	elif frappe_ts_field:
		ts_expr = f"`{frappe_ts_field}`"
	else:
		ts_expr = f"`{fallback_ts_field}`"

	max_ts_result = frappe.db.sql(f"SELECT MAX({ts_expr}) FROM `tab{ctx.frappe_doctype}`")
	max_ts = max_ts_result[0][0] if max_ts_result else None

	if not max_ts:
		return None

	if isinstance(max_ts, str):
		max_ts = datetime.fromisoformat(max_ts)

	cutoff = max_ts

	# Convert from Frappe TZ to WP TZ for the query
	return convert_frappe_ts_to_wp_tz(cutoff, ctx.wp_tz)


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


def _count_rows_to_sync(ctx, cutoff):
	"""
	Count how many rows will be synced, using the same WHERE clause as _fetch_changed_rows.

	Args:
		ctx: SyncContext
		cutoff: Cutoff datetime in WP timezone (None = count all)

	Returns:
		int: Number of rows to sync
	"""
	cursor = ctx.conn.cursor()

	if cutoff:
		ts_expr = _build_ts_expr(ctx.ts_field, ctx.create_ts_field)
		cursor.execute(
			f"SELECT COUNT(*) as cnt FROM `{ctx.table_name}` WHERE {ts_expr} > %s",
			(cutoff,),
		)
	else:
		cursor.execute(f"SELECT COUNT(*) as cnt FROM `{ctx.table_name}`")

	result = cursor.fetchone()
	cursor.close()
	return result["cnt"] if result else 0


def _fetch_changed_rows(ctx, cutoff):
	"""
	Fetch rows from WordPress that have changed since the cutoff.

	Uses GREATEST(COALESCE(modified_ts, created_ts), created_ts) so rows
	with NULL modified_ts are correctly handled via their created_ts.

	Args:
		ctx: SyncContext
		cutoff: Cutoff datetime in WP timezone (None = fetch all)

	Returns:
		List of row dicts from WordPress
	"""
	cursor = ctx.conn.cursor()

	if cutoff:
		ts_expr = _build_ts_expr(ctx.ts_field, ctx.create_ts_field)
		cursor.execute(
			f"SELECT * FROM `{ctx.table_name}` WHERE {ts_expr} > %s",
			(cutoff,),
		)
	else:
		cursor.execute(f"SELECT * FROM `{ctx.table_name}`")

	rows = cursor.fetchall()
	cursor.close()
	return rows


def _fetch_rows_by_keys(ctx, key_set):
	"""
	Fetch full rows from WP for a specific set of matching-key tuples.

	For single-column keys uses efficient WHERE IN queries (batched).
	For composite keys falls back to fetching all rows and filtering in Python.

	Args:
		ctx: SyncContext
		key_set: Set of key tuples to fetch

	Returns:
		List of row dicts from WordPress
	"""
	if not key_set:
		return []

	if len(ctx.matching_keys) == 1:
		wp_col = ctx.reverse_mapping.get(ctx.matching_keys[0], ctx.matching_keys[0])
		values = [k[0] for k in key_set if k[0] is not None]
		if not values:
			return []
		rows = []
		cursor = ctx.conn.cursor()
		for i in range(0, len(values), WHERE_IN_BATCH_SIZE):
			batch = values[i : i + WHERE_IN_BATCH_SIZE]
			placeholders = ",".join(["%s"] * len(batch))
			cursor.execute(
				f"SELECT * FROM `{ctx.table_name}` WHERE `{wp_col}` IN ({placeholders})",
				batch,
			)
			rows.extend(cursor.fetchall())
		cursor.close()
		return rows

	# Composite key — fetch all and filter in Python
	cursor = ctx.conn.cursor()
	cursor.execute(f"SELECT * FROM `{ctx.table_name}`")
	all_rows = cursor.fetchall()
	cursor.close()

	result = []
	for row in all_rows:
		converted = _convert_row(row, None, ctx.column_mapping)
		key_tuple = tuple(_normalize_key_value(converted.get(k)) for k in ctx.matching_keys)
		if key_tuple in key_set:
			result.append(row)
	return result


def _batch_process_rows(rows, row_processor, ctx, total_to_sync):
	"""
	Process WordPress rows in batches with savepoints, error collection,
	and progress reporting.

	This is the shared inner loop used by both TS Compare and Truncate & Replace.

	Args:
		rows: List of raw WordPress row dicts to process.
		row_processor: Callable(row) that processes a single row.  Should
		               return True if the row was newly inserted, False if updated.
		ctx: SyncContext (used for table name, sync_user, and progress reporting).
		total_to_sync: Total expected row count (for progress denominator).

	Returns:
		dict with rows_processed, rows_inserted, rows_skipped, skip_errors.
	"""
	rows_processed = 0
	rows_inserted = 0
	rows_skipped = 0
	skip_errors = []

	for i in range(0, len(rows), UPSERT_BATCH_SIZE):
		batch = rows[i : i + UPSERT_BATCH_SIZE]
		for row in batch:
			row_hint = str(dict(list(row.items())[:4]))[:150]
			frappe.db.savepoint("row_sync")
			try:
				was_new = row_processor(row)
				rows_processed += 1
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
			if (rows_processed + rows_skipped) % UPSERT_BATCH_SIZE == 0:
				_publish_sync_progress(
					ctx.wp_table_doc.name, rows_processed, total_to_sync, user=ctx.sync_user,
				)
		frappe.db.commit()

	if rows_skipped:
		frappe.log_error(
			title=f"Sync skipped rows: {ctx.table_name}",
			message=f"Skipped {rows_skipped} rows.\n" + "\n".join(skip_errors),
		)

	# Final progress update (always fires so small tables get at least one toast)
	_publish_sync_progress(
		ctx.wp_table_doc.name, rows_processed, total_to_sync, user=ctx.sync_user,
	)

	return {
		"rows_processed": rows_processed,
		"rows_inserted": rows_inserted,
		"rows_skipped": rows_skipped,
		"skip_errors": skip_errors,
	}


@_register_sync_strategy("TS Compare")
def _sync_ts_compare(ctx):
	"""
	Sync using timestamp comparison method.

	Steps:
	1. Pull matching keys from WP, diff against Frappe, delete orphans.
	2. Pull changed rows (ts_field > last_synced) + rows missing from Frappe.
	3. Upsert into Frappe DocType by matching key.

	Args:
		ctx: SyncContext

	Returns:
		dict with sync results
	"""
	# Step 1: Delete orphans and get key sets
	wp_key_set = _get_wp_key_set(ctx)
	rows_deleted, frappe_key_set = _delete_orphans(ctx, wp_key_set)

	# Step 2: Collect changed + missing rows
	changed_rows, missing_rows = _collect_rows_to_sync(ctx, wp_key_set, frappe_key_set)

	# Step 3: Upsert via shared batch processor
	total_to_sync = len(changed_rows) + len(missing_rows)

	def upsert_one(row):
		converted = _convert_row(row, ctx.wp_tz, ctx.column_mapping)
		return _upsert_record(ctx.frappe_doctype, ctx.matching_keys, converted)

	result_changed = _batch_process_rows(changed_rows, upsert_one, ctx, total_to_sync)
	result_missing = _batch_process_rows(missing_rows, upsert_one, ctx, total_to_sync) if missing_rows else {
		"rows_processed": 0, "rows_inserted": 0, "rows_skipped": 0,
	}

	rows_upserted = result_changed["rows_processed"] + result_missing["rows_processed"]
	rows_inserted = result_changed["rows_inserted"] + result_missing["rows_inserted"]

	return {
		"method": "TS Compare",
		"rows_upserted": rows_upserted,
		"rows_inserted": rows_inserted,
		"rows_deleted": rows_deleted,
		"rows_skipped": result_changed["rows_skipped"] + result_missing["rows_skipped"],
		"total_wp_rows": len(wp_key_set),
		"missing_rows_found": len(missing_rows),
	}


def _collect_rows_to_sync(ctx, wp_key_set, frappe_key_set):
	"""
	Identify WP rows that need syncing: timestamp-changed rows + rows
	missing from Frappe (deduped).

	Args:
		ctx: SyncContext
		wp_key_set: Set of key tuples from WordPress
		frappe_key_set: Set of key tuples currently in Frappe

	Returns:
		tuple: (changed_rows, missing_rows)
	"""
	# Cutoff: latest effective timestamp already stored in Frappe
	cutoff = _get_cutoff_timestamp(ctx)

	changed_rows = _fetch_changed_rows(ctx, cutoff)

	# Build set of keys already covered by the TS-changed fetch to avoid double-processing
	changed_keys = set()
	for row in changed_rows:
		converted = _convert_row(row, None, ctx.column_mapping)
		key_tuple = tuple(_normalize_key_value(converted.get(k)) for k in ctx.matching_keys)
		changed_keys.add(key_tuple)

	# Rows missing from Frappe that weren't in the TS-changed set
	missing_keys = wp_key_set - frappe_key_set
	missing_keys_only = missing_keys - changed_keys
	missing_rows = _fetch_rows_by_keys(ctx, missing_keys_only)

	return changed_rows, missing_rows


@_register_sync_strategy("Truncate & Replace")
def _sync_truncate_replace(ctx):
	"""
	Sync using truncate and replace method.

	Deletes all Frappe records and re-inserts from WordPress.

	Args:
		ctx: SyncContext

	Returns:
		dict with sync results
	"""
	# Step 1: Delete all existing Frappe records
	frappe.db.delete(ctx.frappe_doctype)
	frappe.db.commit()

	# Step 2: Fetch all rows from WordPress
	cursor = ctx.conn.cursor()
	cursor.execute(f"SELECT * FROM `{ctx.table_name}`")
	all_rows = cursor.fetchall()
	cursor.close()
	total_to_sync = len(all_rows)

	# Step 3: Insert all rows via shared batch processor (with in_sync flag)
	def insert_one(row):
		converted = _convert_row(row, ctx.wp_tz, ctx.column_mapping)
		_insert_record(ctx.frappe_doctype, converted)
		return True  # Always a new insert

	frappe.flags.in_sync = True
	try:
		result = _batch_process_rows(all_rows, insert_one, ctx, total_to_sync)
	finally:
		frappe.flags.in_sync = False

	return {
		"method": "Truncate & Replace",
		"rows_inserted": result["rows_processed"],
		"rows_deleted": "all",
		"total_wp_rows": total_to_sync,
		"rows_skipped": result.get("rows_skipped", 0),
		"skip_errors": result.get("skip_errors") or [],
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
		# --- Virtual column skip DISABLED ---
		# Virtual WP columns are currently mapped as regular Frappe Data fields
		# (the virtual-field translation in schema_mirror.py is commented out),
		# so they DO have real DB columns and should receive data during sync.
		# Re-enable this block if virtual-field translation is restored.
		#
		# if column_mapping and wp_key in column_mapping:
		# 	mapping_info = column_mapping[wp_key]
		# 	if isinstance(mapping_info, dict) and mapping_info.get("is_virtual"):
		# 		continue

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


def _delete_orphans(ctx, wp_key_set):
	"""
	Delete Frappe records whose matching keys are not in the WordPress key set.

	Skips records with negative integer names — those are new records created
	locally in Frappe (temp IDs) that have not yet been pushed to WordPress.
	Only deletes records with real positive WP IDs that no longer exist in the source.

	Args:
		ctx: SyncContext
		wp_key_set: Set of key tuples from WordPress

	Returns:
		tuple: (deleted_count, frappe_key_set) where frappe_key_set contains
		       all non-temp key tuples currently in Frappe
	"""
	frappe_records = frappe.get_all(ctx.frappe_doctype, fields=["name", *ctx.matching_keys], limit_page_length=0)

	deleted_count = 0
	frappe_key_set = set()

	for record in frappe_records:
		# Skip temp records (negative integer names) — not yet pushed to WP
		try:
			if int(record.name) < 0:
				continue
		except (ValueError, TypeError):
			pass

		frappe_key = tuple(_normalize_key_value(record.get(k)) for k in ctx.matching_keys)

		if frappe_key not in wp_key_set:
			frappe.delete_doc(ctx.frappe_doctype, record.name, force=True, ignore_permissions=True)
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
		row = frappe.db.get_value(
			"WP Tables", wp_table_name, ["last_sync_status", "last_sync_log"], as_dict=True
		)
		final_status = (row or {}).get("last_sync_status")
		final_log = ((row or {}).get("last_sync_log") or "")[:200]
		if final_status == "Success":
			frappe.publish_realtime(
				"msgprint",
				{"message": f"{label}: Sync complete ✓", "indicator": "green", "alert": True},
				user=wp_table_doc._sync_user,
			)
		elif final_status in ("Error", "Warning"):
			frappe.publish_realtime(
				"msgprint",
				{
					"message": f"{label}: {final_status} — {final_log or 'See WP Tables and Error Log.'}",
					"indicator": "orange" if final_status == "Warning" else "red",
					"alert": True,
				},
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


def _build_sync_summary(result, frappe_count):
	"""
	Build a human-readable sync summary and extract record counts from a
	sync result dict.

	Args:
		result: dict returned by sync_table() (contains method, rows_*, etc.)
		frappe_count: Number of records currently in the Frappe DocType.

	Returns:
		dict with keys: log_message, records_synced, records_created,
		records_updated, rows_deleted, has_changes.
	"""
	rows_upserted = result.get("rows_upserted", 0)
	rows_inserted = result.get("rows_inserted", 0)
	rows_deleted = result.get("rows_deleted", 0)
	if rows_deleted == "all":
		rows_deleted = 0
	reverse_inserted = result.get("reverse_inserted", 0)
	reverse_updated = result.get("reverse_updated", 0)

	has_changes = (rows_upserted + rows_deleted + reverse_inserted + reverse_updated) > 0

	if result.get("method") == "Truncate & Replace":
		log_message = f"Truncate & Replace: {rows_inserted} rows inserted"
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

		log_message = ", ".join(parts)
		records_synced = rows_upserted
		records_created = ts_rows_inserted
		records_updated = ts_rows_updated

	return {
		"log_message": log_message,
		"records_synced": records_synced,
		"records_created": records_created,
		"records_updated": records_updated,
		"rows_deleted": rows_deleted,
		"has_changes": has_changes,
	}


def _run_sync_with_status(wp_table_doc, suppress_notifications=False):
	"""
	Run sync and update status fields on the WP Tables document.
	Also creates a Sync Log record for audit trail.

	Args:
		wp_table_doc: WP Tables document
		suppress_notifications: If True, mutes emails and import notifications.
	"""
	import traceback

	from nce_sync.utils.sync_gate import clear_doctype_syncing, mark_doctype_syncing

	frappe_dt = wp_table_doc.frappe_doctype
	gate_armed = bool(frappe_dt and wp_table_doc.mirror_status in ("Mirrored", "Linked"))
	if gate_armed:
		mark_doctype_syncing(frappe_dt)

	try:
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

			frappe_count = frappe.db.count(wp_table_doc.frappe_doctype)

			# Truncate & Replace: WP had data but every row failed → not Success
			if (
				sync_method == "Truncate & Replace"
				and result.get("method") == "Truncate & Replace"
			):
				tw_tr = int(result.get("total_wp_rows") or 0)
				inserted_tr = int(result.get("rows_inserted") or 0)
				if tw_tr > 0 and inserted_tr == 0:
					skip_errs = result.get("skip_errors") or []
					msg = _(
						"Truncate & Replace failed: WordPress had {0} row(s) but 0 were saved in Frappe. "
						"Check Error Log for row-level DB/validation errors."
					).format(tw_tr)
					if skip_errs:
						msg = f"{msg} " + _("First errors: {0}").format("; ".join(skip_errs[:5])[:400])
					wp_table_doc.last_sync_status = "Error"
					wp_table_doc.last_sync_log = msg[:500]
					wp_table_doc.save()
					frappe.db.commit()
					_create_sync_log(
						wp_table_doc.name,
						sync_method,
						sync_started,
						status="Failed",
						error_message=wp_table_doc.last_sync_log,
						error_traceback="\n".join(skip_errs) if skip_errs else None,
					)
					frappe.log_error(
						title=f"Truncate & Replace: 0 inserts ({wp_table_doc.table_name})",
						message=f"{msg}\n\n" + "\n".join(skip_errs) if skip_errs else msg,
					)
					return

			# Check for anomaly: WP has rows but Frappe table is empty after sync
			total_wp_rows = result.get("total_wp_rows", 0) or result.get("rows_inserted", 0)

			if total_wp_rows > 0 and frappe_count == 0:
				wp_table_doc.last_sync_status = "Warning"
				wp_table_doc.last_sync_log = (
					f"ANOMALY: WP has {total_wp_rows} rows but Frappe table is empty. "
					f"last_synced NOT updated - next sync will do full pull. "
					f"Check matching keys and column mapping."
				)
				wp_table_doc.save()
				_create_sync_log(
					wp_table_doc.name, sync_method, sync_started,
					status="Partial", error_message=wp_table_doc.last_sync_log,
				)
				return

			# Build summary and update status
			summary = _build_sync_summary(result, frappe_count)

			wp_table_doc.last_synced = now_datetime()
			wp_table_doc.last_sync_status = "Success"
			wp_table_doc.last_sync_log = summary["log_message"]
			wp_table_doc.save()
			frappe.db.commit()

			if summary["has_changes"]:
				_create_sync_log(
					wp_table_doc.name, sync_method, sync_started,
					status="Success",
					records_synced=summary["records_synced"],
					records_created=summary["records_created"],
					records_updated=summary["records_updated"],
					records_deleted=summary["rows_deleted"],
				)

		except Exception as e:
			wp_table_doc.last_sync_status = "Error"
			wp_table_doc.last_sync_log = str(e)[:500]
			wp_table_doc.save()

			_create_sync_log(
				wp_table_doc.name, sync_method, sync_started,
				status="Failed", error_message=str(e)[:500],
				error_traceback=traceback.format_exc(),
			)

			frappe.log_error(title=f"Sync Error: {wp_table_doc.table_name}", message=str(e))
			raise

		finally:
			if suppress_notifications:
				frappe.flags.in_import = False
				frappe.flags.mute_emails = False

	finally:
		if gate_armed:
			clear_doctype_syncing(frappe_dt)


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
