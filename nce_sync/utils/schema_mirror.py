# Copyright (c) 2026, Oliver Reid and contributors
# For license information, please see license.txt

"""
Schema mirroring utilities for NCE_Sync.
Handles WordPress database introspection and Frappe DocType generation.
"""

import frappe
import pymysql
from frappe import _

from nce_sync.utils.connections import get_wp_connection, wp_connection
from nce_sync.utils.constants import PICK_LIST_DISTINCT_LIMIT
from nce_sync.utils.derived_column_sql import sql_generation_to_frappe_bare_sql
from nce_sync.utils.workspace_utils import add_to_workspace

# Frappe reserved fieldnames - cannot be used as custom field names
# These are system fields used internally by Frappe
RESTRICTED_FIELDNAMES = (
	"name",
	"parent",
	"creation",
	"owner",
	"modified",
	"modified_by",
	"parentfield",
	"parenttype",
	"file_list",
	"flags",
	"docstatus",
)


def sanitize_fieldname(fieldname):
	"""
	Sanitize a fieldname to avoid Frappe restricted names.

	Args:
		fieldname: Original fieldname (lowercase)

	Returns:
		Safe fieldname (appends '_field' if restricted)
	"""
	if fieldname.lower() in RESTRICTED_FIELDNAMES:
		return f"{fieldname}_field"
	return fieldname


def resolve_fieldname(col_name, label_overrides=None):
	"""
	Determine the Frappe fieldname for a WP column.

	For restricted source names (e.g. 'name'), if a label override is provided
	the fieldname is derived from the custom label (e.g. 'Event Name' -> 'event_name').
	Otherwise falls back to sanitize_fieldname (which appends '_field').
	"""
	lower = col_name.lower()
	if lower in RESTRICTED_FIELDNAMES and label_overrides and col_name in label_overrides:
		derived = frappe.scrub(label_overrides[col_name])
		if derived and derived not in RESTRICTED_FIELDNAMES:
			return derived
	return sanitize_fieldname(lower)


def get_matching_fields_list(wp_table_doc):
	"""
	Parse matching_fields from WP Tables document into a list.

	Args:
		wp_table_doc: WP Tables document

	Returns:
		List of matching field column names
	"""
	if not wp_table_doc.matching_fields:
		return []
	return [f.strip() for f in wp_table_doc.matching_fields.split(",") if f.strip()]


def build_frappe_field(col, schema, wp_table_doc, field_overrides=None, label_overrides=None, idx=1, read_only_fieldnames=None, pick_list_options=None, bold_fieldnames=None):
	"""
	Build a Frappe field dict from a WordPress column definition.

	Args:
		col: Column dict from schema['columns']
		schema: Full schema dict (for unique_keys, indexes)
		wp_table_doc: WP Tables document (for timestamp fields)
		field_overrides: Optional dict of {column_name: fieldtype}
		label_overrides: Optional dict of {column_name: label}
		idx: Field index

	Returns:
		Dict suitable for DocType field definition
	"""
	col_name = col["COLUMN_NAME"]
	safe_fieldname = resolve_fieldname(col_name, label_overrides)
	field_mapping = map_mariadb_to_frappe_type(col)

	# Apply user override if provided
	if field_overrides and col_name in field_overrides:
		field_mapping["fieldtype"] = field_overrides[col_name]

	# Use label override if provided, otherwise generate from column name
	label = col_name.replace("_", " ").title()
	if label_overrides and col_name in label_overrides:
		label = label_overrides[col_name]

	field = {
		"fieldname": safe_fieldname,
		"fieldtype": field_mapping["fieldtype"],
		"label": label,
		"reqd": 1 if col["IS_NULLABLE"] == "NO" else 0,
		"idx": idx,
	}

	# Add type-specific properties
	if "length" in field_mapping:
		field["length"] = field_mapping["length"]
	if "precision" in field_mapping:
		field["precision"] = field_mapping["precision"]
	if "options" in field_mapping:
		field["options"] = field_mapping["options"]

	# Mark unique columns (only from actual DB unique constraints)
	if any(col_name in uk for uk in schema["unique_keys"].values()):
		field["unique"] = 1

	# Mark indexed columns (from DB indexes OR matching fields OR timestamp fields)
	matching_fields = get_matching_fields_list(wp_table_doc)
	is_indexed = any(col_name in idx_cols for idx_cols in schema["indexes"].values())
	is_matching = col_name in matching_fields
	is_timestamp = col_name in (wp_table_doc.modified_timestamp_field, wp_table_doc.created_timestamp_field)

	if is_indexed or is_matching or is_timestamp:
		field["search_index"] = 1

	# Pick List: override fieldtype to Select with DISTINCT source values as options
	if pick_list_options and safe_fieldname in pick_list_options:
		field["fieldtype"] = "Select"
		field["options"] = pick_list_options[safe_fieldname]

	# Mark field as read-only if user selected it in the preview dialog
	if read_only_fieldnames and safe_fieldname in read_only_fieldnames:
		field["read_only"] = 1

	# Mark field as bold if user selected it in the preview dialog
	if bold_fieldnames and safe_fieldname in bold_fieldnames:
		field["bold"] = 1

	# --- Virtual/generated columns (commented out — always treat as read-only Data) ---
	# extra = col.get("EXTRA", "") or ""
	# is_virtual_col = "VIRTUAL" in extra.upper() or "GENERATED" in extra.upper()
	# if is_virtual_col:
	# 	from nce_sync.utils.sql_to_python import sql_generation_to_python
	#
	# 	gen_expr = col.get("GENERATION_EXPRESSION", "") or ""
	# 	python_expr = sql_generation_to_python(gen_expr, label_overrides)
	# 	if python_expr:
	# 		field["is_virtual"] = 1
	# 		field["options"] = python_expr
	# 	else:
	# 		# Translation failed — make it read-only so it won't be written back
	# 		field["read_only"] = 1
	# 	field["reqd"] = 0

	return field


def discover_tables_and_views(conn):
	"""
	Discover all tables and views from WordPress database.

	Args:
		conn: PyMySQL connection

	Returns:
		List of dicts with table_name and table_type
	"""
	try:
		cursor = conn.cursor()
		query = """
			SELECT TABLE_NAME, TABLE_TYPE
			FROM information_schema.TABLES
			WHERE TABLE_SCHEMA = DATABASE()
			ORDER BY TABLE_NAME
		"""
		cursor.execute(query)
		results = cursor.fetchall()
		cursor.close()

		# Transform to simpler format
		tables_and_views = []
		for row in results:
			table_type = "View" if row["TABLE_TYPE"] == "VIEW" else "Table"
			tables_and_views.append({"table_name": row["TABLE_NAME"], "table_type": table_type})

		return tables_and_views

	except Exception as e:
		frappe.log_error(title="Table Discovery Error", message=str(e))
		raise


def detect_timestamp_fields(conn, table_name):
	"""
	Auto-detect created and modified timestamp fields.

	Args:
		conn: PyMySQL connection
		table_name: Name of the table

	Returns:
		Dict with 'created' and 'modified' field names (or None)
	"""
	try:
		cursor = conn.cursor()
		query = """
			SELECT COLUMN_NAME, DATA_TYPE, COLUMN_DEFAULT, EXTRA
			FROM information_schema.COLUMNS
			WHERE TABLE_SCHEMA = DATABASE()
			AND TABLE_NAME = %s
			AND DATA_TYPE IN ('datetime', 'timestamp')
			ORDER BY ORDINAL_POSITION
		"""
		cursor.execute(query, (table_name,))
		timestamp_columns = cursor.fetchall()
		cursor.close()

		created_field = None
		modified_field = None

		# Common patterns for created timestamp
		created_patterns = [
			"created_at",
			"created",
			"created_date",
			"create_time",
			"date_created",
		]
		# Common patterns for modified timestamp
		modified_patterns = [
			"modified_at",
			"updated_at",
			"modified",
			"updated",
			"last_modified",
			"last_updated",
			"update_time",
		]

		for col in timestamp_columns:
			col_name_lower = col["COLUMN_NAME"].lower()

			# Check for created field
			if not created_field:
				if col_name_lower in created_patterns:
					created_field = col["COLUMN_NAME"]
				# Also check for CURRENT_TIMESTAMP default without ON UPDATE
				elif (
					"CURRENT_TIMESTAMP" in (col["COLUMN_DEFAULT"] or "").upper()
					and "on update" not in (col["EXTRA"] or "").lower()
				):
					created_field = col["COLUMN_NAME"]

			# Check for modified field
			if not modified_field:
				if col_name_lower in modified_patterns:
					modified_field = col["COLUMN_NAME"]
				# Also check for ON UPDATE CURRENT_TIMESTAMP
				elif "on update" in (col["EXTRA"] or "").lower():
					modified_field = col["COLUMN_NAME"]

		return {"created": created_field, "modified": modified_field}

	except Exception as e:
		frappe.log_error(title="Timestamp Detection Error", message=str(e))
		return {"created": None, "modified": None}


def get_table_schema(conn, table_name):
	"""
	Get full schema information for a table.

	Args:
		conn: PyMySQL connection
		table_name: Name of the table

	Returns:
		Dict with columns, primary_key, unique_keys, indexes
	"""
	try:
		cursor = conn.cursor()

		# Get columns (including GENERATION_EXPRESSION for virtual/computed columns)
		query = """
			SELECT
				COLUMN_NAME,
				DATA_TYPE,
				CHARACTER_MAXIMUM_LENGTH,
				NUMERIC_PRECISION,
				NUMERIC_SCALE,
				IS_NULLABLE,
				COLUMN_DEFAULT,
				EXTRA,
				COLUMN_TYPE,
				GENERATION_EXPRESSION
			FROM information_schema.COLUMNS
			WHERE TABLE_SCHEMA = DATABASE()
			AND TABLE_NAME = %s
			ORDER BY ORDINAL_POSITION
		"""
		cursor.execute(query, (table_name,))
		columns = cursor.fetchall()

		# Get primary key
		query = """
			SELECT COLUMN_NAME
			FROM information_schema.KEY_COLUMN_USAGE
			WHERE TABLE_SCHEMA = DATABASE()
			AND TABLE_NAME = %s
			AND CONSTRAINT_NAME = 'PRIMARY'
			ORDER BY ORDINAL_POSITION
		"""
		cursor.execute(query, (table_name,))
		primary_key = [row["COLUMN_NAME"] for row in cursor.fetchall()]

		# Get unique keys
		query = """
			SELECT CONSTRAINT_NAME, COLUMN_NAME
			FROM information_schema.KEY_COLUMN_USAGE
			WHERE TABLE_SCHEMA = DATABASE()
			AND TABLE_NAME = %s
			AND CONSTRAINT_NAME != 'PRIMARY'
			ORDER BY CONSTRAINT_NAME, ORDINAL_POSITION
		"""
		cursor.execute(query, (table_name,))
		unique_key_rows = cursor.fetchall()

		# Group by constraint name
		unique_keys = {}
		for row in unique_key_rows:
			constraint = row["CONSTRAINT_NAME"]
			if constraint not in unique_keys:
				unique_keys[constraint] = []
			unique_keys[constraint].append(row["COLUMN_NAME"])

		# Get indexes
		query = """
			SELECT DISTINCT INDEX_NAME, COLUMN_NAME
			FROM information_schema.STATISTICS
			WHERE TABLE_SCHEMA = DATABASE()
			AND TABLE_NAME = %s
			AND INDEX_NAME != 'PRIMARY'
			AND NON_UNIQUE = 1
			ORDER BY INDEX_NAME, SEQ_IN_INDEX
		"""
		cursor.execute(query, (table_name,))
		index_rows = cursor.fetchall()

		# Group by index name
		indexes = {}
		for row in index_rows:
			index = row["INDEX_NAME"]
			if index not in indexes:
				indexes[index] = []
			indexes[index].append(row["COLUMN_NAME"])

		cursor.close()

		return {
			"columns": columns,
			"primary_key": primary_key,
			"unique_keys": unique_keys,
			"indexes": indexes,
		}

	except Exception as e:
		frappe.log_error(title="Schema Introspection Error", message=str(e))
		raise


def map_mariadb_to_frappe_type(column):
	"""
	Map MariaDB column type to Frappe field type.

	Args:
		column: Column dict from information_schema.COLUMNS

	Returns:
		Dict with fieldtype and additional properties
	"""
	data_type = column["DATA_TYPE"].lower()
	column_type = column["COLUMN_TYPE"].lower()
	max_length = column["CHARACTER_MAXIMUM_LENGTH"]

	# VARCHAR
	# Frappe Data fields default to 140 chars, but can be longer
	# Use Data for varchar up to 255 (single-line input), Small Text only for longer
	if data_type == "varchar":
		if max_length and max_length <= 255:
			return {"fieldtype": "Data", "length": max_length}
		else:
			return {"fieldtype": "Small Text"}

	# CHAR
	elif data_type == "char":
		return {"fieldtype": "Data", "length": max_length}

	# TINYINT(1) -> Check
	elif data_type == "tinyint" and "tinyint(1)" in column_type:
		return {"fieldtype": "Check"}

	# Integer types
	elif data_type in ("tinyint", "smallint", "mediumint", "int", "bigint", "integer"):
		return {"fieldtype": "Int"}

	# Float types
	elif data_type in ("float", "double"):
		return {"fieldtype": "Float"}

	# Decimal
	elif data_type == "decimal":
		scale = column["NUMERIC_SCALE"] or 2
		return {"fieldtype": "Float", "precision": scale}

	# Date/Time types
	elif data_type == "date":
		return {"fieldtype": "Date"}
	elif data_type in ("datetime", "timestamp"):
		return {"fieldtype": "Datetime"}
	elif data_type == "time":
		return {"fieldtype": "Time"}

	# Text types
	elif data_type == "text":
		return {"fieldtype": "Text"}
	elif data_type == "mediumtext":
		return {"fieldtype": "Long Text"}
	elif data_type == "longtext":
		return {"fieldtype": "Long Text"}

	# ENUM -> Select
	elif data_type == "enum":
		# Parse enum values from COLUMN_TYPE
		# Format: enum('value1','value2','value3')
		if "enum(" in column_type:
			values_str = column_type[column_type.find("(") + 1 : column_type.rfind(")")]
			# Remove quotes and split
			values = [v.strip("'\"") for v in values_str.split(",")]
			options = "\n".join(values)
			return {"fieldtype": "Select", "options": options}
		return {"fieldtype": "Data"}

	# SET -> Data (no direct equivalent)
	elif data_type == "set":
		return {"fieldtype": "Data"}

	# JSON
	elif data_type == "json":
		return {"fieldtype": "JSON"}

	# BLOB types
	elif data_type in ("blob", "mediumblob", "longblob"):
		return {"fieldtype": "Long Text"}

	# Default fallback
	else:
		return {"fieldtype": "Data"}


def _parse_comma_columns(columns, label_overrides=None):
	"""
	Parse a comma-separated column string (or list) into a set of resolved
	Frappe fieldnames.  Used for read-only, bold, and pick-list column lists.

	Args:
		columns: Comma-separated string, list, or None.
		label_overrides: Optional label overrides for fieldname resolution.

	Returns:
		set of Frappe fieldnames (may be empty).
	"""
	if not columns:
		return set()
	if isinstance(columns, str):
		col_list = [c.strip() for c in columns.split(",") if c.strip()]
	else:
		col_list = list(columns)
	return {resolve_fieldname(c, label_overrides) for c in col_list}


def _parse_column_defaults_payload(column_defaults):
	"""Normalize API arg to dict[str, str] | None (None = do not change any defaults)."""
	import json

	if column_defaults is None:
		return None
	if isinstance(column_defaults, str):
		if not column_defaults.strip():
			return {}
		return json.loads(column_defaults)
	return dict(column_defaults) if column_defaults else {}


def _resolve_fieldtype_for_default_mirror(wp_col, entry, field_overrides):
	if entry.get("is_pick_list"):
		return "Select"
	if field_overrides and wp_col in field_overrides:
		return field_overrides[wp_col]
	if entry.get("is_name"):
		return "Data"
	return entry.get("original_fieldtype") or "Data"


def _resolve_fieldtype_for_default_update(entry, doctype):
	meta = frappe.get_meta(doctype)
	fn = entry.get("fieldname")
	if fn:
		df = meta.get_field(fn)
		if df:
			return df.fieldtype
	return entry.get("original_fieldtype") or "Data"


def apply_column_defaults_to_mapping(column_mapping, column_defaults, *, resolve_fieldtype):
	"""
	Merge validated script defaults into ``column_mapping`` (WP column keys).
	Pass ``column_defaults=None`` to leave stored defaults unchanged.
	``resolve_fieldtype(wp_col, entry)`` returns the Frappe fieldtype for validation.
	"""
	from nce_sync.utils.script_default_validation import validate_and_normalize_script_default

	payload = _parse_column_defaults_payload(column_defaults)
	if payload is None or not column_mapping:
		return

	for wp_col, raw in payload.items():
		if wp_col not in column_mapping:
			continue
		entry = column_mapping[wp_col]
		if not isinstance(entry, dict):
			continue
		raw_str = raw if isinstance(raw, str) else ("" if raw is None else str(raw))
		if not raw_str.strip():
			entry.pop("default_value", None)
			continue
		ft = resolve_fieldtype(wp_col, entry)
		entry["default_value"] = validate_and_normalize_script_default(ft, raw_str, wp_col)


def _restore_previous_selections(wp_table_doc):
	"""
	Restore previous user selections from a WP Tables document for the
	preview dialog (matching fields, name column, auto-generated, read-only,
	pick-list, bold, timestamps).

	Returns:
		dict with all the ``previous_*`` keys expected by the JS dialog.
	"""
	import json

	previous_matching = []
	if wp_table_doc.matching_fields:
		previous_matching = [f.strip() for f in wp_table_doc.matching_fields.split(",") if f.strip()]

	previous_name_column = getattr(wp_table_doc, "name_field_column", None) or None

	previous_auto_gen = []
	auto_gen_raw = getattr(wp_table_doc, "auto_generated_columns", None) or ""
	if auto_gen_raw:
		previous_auto_gen = [c.strip().lower() for c in auto_gen_raw.split(",") if c.strip()]

	existing_field_labels = {}
	existing_columns = set()
	previous_read_only = []
	previous_pick_list = []
	previous_bold = []

	# Read saved_field_settings from delete_mirror snapshot (Tab 2 attributes)
	saved_settings_raw = getattr(wp_table_doc, "saved_field_settings", None)
	if saved_settings_raw:
		saved = json.loads(saved_settings_raw)
		previous_read_only.extend(saved.get("read_only", []))
		previous_pick_list.extend(saved.get("pick_list", []))
		previous_bold.extend(saved.get("bold", []))

	# Also read column_mapping — older mirrors may have flags stored there
	col_map_raw = getattr(wp_table_doc, "column_mapping", None)
	if col_map_raw:
		col_map = json.loads(col_map_raw)
		existing_columns = set(col_map.keys())
		for wp_col, info in col_map.items():
			if isinstance(info, dict):
				if info.get("is_read_only") and wp_col.lower() not in previous_read_only:
					previous_read_only.append(wp_col.lower())
				if info.get("is_pick_list") and wp_col.lower() not in previous_pick_list:
					previous_pick_list.append(wp_col.lower())
				if info.get("is_bold") and wp_col.lower() not in previous_bold:
					previous_bold.append(wp_col.lower())

	# Label lookup requires the DocType to exist (deleted during Reconfigure)
	if wp_table_doc.frappe_doctype and frappe.db.exists("DocType", wp_table_doc.frappe_doctype):
		meta = frappe.get_meta(wp_table_doc.frappe_doctype)
		fieldname_to_label = {df.fieldname: df.label for df in meta.fields}

		for wp_col in existing_columns:
			mapping_info = col_map.get(wp_col, {})
			fn = mapping_info.get("fieldname", wp_col.lower()) if isinstance(mapping_info, dict) else mapping_info
			if fn in fieldname_to_label:
				existing_field_labels[wp_col] = fieldname_to_label[fn]

	return {
		"previous_matching_fields": previous_matching,
		"previous_name_field_column": previous_name_column,
		"previous_title_field_column": getattr(wp_table_doc, "title_field_column", None) or None,
		"previous_auto_generated_columns": previous_auto_gen,
		"previous_modified_ts": getattr(wp_table_doc, "modified_timestamp_field", None) or "",
		"previous_created_ts": getattr(wp_table_doc, "created_timestamp_field", None) or "",
		"previous_read_only_columns": previous_read_only,
		"previous_pick_list_columns": previous_pick_list,
		"previous_bold_columns": previous_bold,
		"existing_field_labels": existing_field_labels,
		"existing_columns": existing_columns,
	}


def preview_table_schema(wp_conn_doc, wp_table_doc):
	"""
	Introspect a WordPress table and return proposed field mappings
	for user review before creating the DocType.

	Always fetches fresh schema from WordPress to detect any column changes.
	Restores previous user selections (matching fields) if available.

	Args:
		wp_conn_doc: WordPress Connection document
		wp_table_doc: WP Tables document

	Returns:
		Dict with fields, timestamps, doctype_name, and previous_matching_fields
	"""
	with wp_connection(wp_conn_doc) as conn:
		table_name = wp_table_doc.table_name
		schema = get_table_schema(conn, table_name)
		timestamps = detect_timestamp_fields(conn, table_name)

	prev = _restore_previous_selections(wp_table_doc)
	existing_field_labels = prev.pop("existing_field_labels")
	existing_columns = prev.pop("existing_columns")

	import json

	col_defaults_by_wp = {}
	if getattr(wp_table_doc, "column_mapping", None):
		try:
			_cm = json.loads(wp_table_doc.column_mapping)
			for _w, _info in (_cm or {}).items():
				if isinstance(_info, dict) and _info.get("default_value") not in (None, ""):
					dv = _info.get("default_value")
					col_defaults_by_wp[_w] = dv if isinstance(dv, str) else json.dumps(dv)
		except Exception:
			pass

	preview = []
	for col in schema["columns"]:
		col_name = col["COLUMN_NAME"]
		field_mapping = map_mariadb_to_frappe_type(col)

		is_unique = any(col_name in uk for uk in schema["unique_keys"].values())
		is_indexed = any(col_name in idx_cols for idx_cols in schema["indexes"].values())
		is_pk = col_name in schema.get("primary_key", [])

		extra = col.get("EXTRA", "") or ""
		is_virtual = "VIRTUAL" in extra.upper() or "GENERATED" in extra.upper()
		is_auto_increment = "AUTO_INCREMENT" in extra.upper()
		gen_src = (col.get("GENERATION_EXPRESSION") or "") or ""
		expr_display = None
		if is_virtual and str(gen_src).strip():
			res = _make_wp_to_frappe_resolve(
				schema, existing_field_labels or None, getattr(wp_table_doc, "name_field_column", None)
			)
			expr_display = sql_generation_to_frappe_bare_sql(gen_src, res) or None

		label = existing_field_labels.get(col_name, col_name.replace("_", " ").title())

		preview.append(
			{
				"column_name": col_name,
				"db_type": col["COLUMN_TYPE"],
				"proposed_fieldtype": field_mapping["fieldtype"],
				"label": label,
				"is_nullable": col["IS_NULLABLE"],
				"is_primary_key": is_pk,
				"is_unique": is_unique,
				"is_indexed": is_indexed,
				"is_virtual": is_virtual,
				"is_auto_increment": is_auto_increment,
				"is_existing": col_name in existing_columns,
				"length": field_mapping.get("length", 0),
				"precision": field_mapping.get("precision", 0),
				"options": field_mapping.get("options", ""),
				"generation_expression": gen_src or None,
				"sql_expression_frappe": expr_display,
				"default_value": col_defaults_by_wp.get(col_name, ""),
			}
		)

	return {
		"fields": preview,
		"timestamps": timestamps,
		"doctype_name": wp_table_doc.nce_name or table_name,
		**prev,
	}


def _fetch_pick_list_options(conn, table_name, pick_list_columns, label_overrides=None):
	"""
	Query DISTINCT values for pick-list columns from WordPress.

	Args:
		conn: PyMySQL connection.
		table_name: WordPress table name.
		pick_list_columns: Comma-separated string, list, or None.
		label_overrides: Optional label overrides for fieldname resolution.

	Returns:
		dict of {frappe_fieldname: "opt1\\nopt2\\n..."}.
	"""
	pick_list_options = {}
	if not pick_list_columns:
		return pick_list_options
	if isinstance(pick_list_columns, str):
		pl_cols = [c.strip() for c in pick_list_columns.split(",") if c.strip()]
	else:
		pl_cols = list(pick_list_columns)

	cursor = conn.cursor()
	for col_name in pl_cols:
		try:
			cursor.execute(
				f"SELECT DISTINCT `{col_name}` FROM `{table_name}` "
				f"WHERE `{col_name}` IS NOT NULL "
				f"ORDER BY `{col_name}` LIMIT {PICK_LIST_DISTINCT_LIMIT}"
			)
			values = [str(row[col_name]) for row in cursor.fetchall() if row[col_name] is not None]
			if values:
				fieldname = resolve_fieldname(col_name, label_overrides)
				pick_list_options[fieldname] = "\n".join(values)
		except Exception:
			pass  # Column may not exist — skip silently
	cursor.close()
	return pick_list_options


def _make_wp_to_frappe_resolve(schema, label_overrides, name_field_column):
	"""
	Build a resolver from source column name → Frappe fieldname for this table
	(used to rewrite ``GENERATION_EXPRESSION`` to Frappe identifier SQL).
	"""
	lookup = {}
	for col in schema["columns"]:
		wp = col["COLUMN_NAME"]
		fn = resolve_fieldname(wp, label_overrides)
		if name_field_column and wp == name_field_column:
			fn = "name"
		lookup[wp] = fn
		lookup[wp.lower()] = fn

	def resolve(name: str) -> str:
		if name in lookup:
			return lookup[name]
		nl = name.lower()
		if nl in lookup:
			return lookup[nl]
		return resolve_fieldname(name, label_overrides)

	return resolve


def _merge_column_mapping_for_mirror(fresh, previous):
	"""
	Merge a freshly built column_mapping with the previous doc JSON on re-mirror.

	* Source columns that disappeared are dropped (only keys in ``fresh`` are kept).
	* For each column, ``{**old_entry, **new_entry}`` so Desk-only custom keys
	  survive while introspection-owned fields (``fieldname``, ``is_derived``,
	  ``sql_expression``, flags) are updated from the current source schema.

	See also ``column_mapper`` module docstring for a ``column_mapping`` example.
	"""
	if not previous:
		return fresh
	out = {}
	for wp_key, new_val in fresh.items():
		old_val = previous.get(wp_key)
		if isinstance(old_val, dict) and isinstance(new_val, dict):
			out[wp_key] = {**old_val, **new_val}
		else:
			out[wp_key] = new_val
	return out


def _build_column_mapping(
	schema, label_overrides, name_field_column, auto_generated_columns,
	read_only_fieldnames, pick_list_options, bold_fieldnames,
):
	"""
	Build the column_mapping JSON dict from a WordPress schema.

	Each entry maps ``wp_column_name`` → ``{fieldname, is_virtual, is_auto_generated, ...}``.
	For MariaDB generated / VIRTUAL columns, also ``is_derived`` and
	``sql_expression`` (Frappe fieldname SQL; see :func:`sql_generation_to_frappe_bare_sql`).

	Returns:
		tuple: (column_mapping dict, stored_auto_gen comma-separated string)
	"""
	auto_gen_set = set()
	if auto_generated_columns:
		auto_gen_set = {c.strip().lower() for c in auto_generated_columns if c.strip()}

	resolve_expr = _make_wp_to_frappe_resolve(schema, label_overrides, name_field_column)
	column_mapping = {}
	for col in schema["columns"]:
		wp_col_name = col["COLUMN_NAME"]
		extra = col.get("EXTRA", "") or ""
		gen_raw = (col.get("GENERATION_EXPRESSION") or "") or ""
		is_virtual = "VIRTUAL" in extra.upper() or "GENERATED" in extra.upper()
		is_auto_increment = "AUTO_INCREMENT" in extra.upper()
		is_auto_generated = wp_col_name.lower() in auto_gen_set or is_auto_increment
		frappe_fieldname = resolve_fieldname(wp_col_name, label_overrides)
		is_read_only = frappe_fieldname in read_only_fieldnames if read_only_fieldnames else False
		is_pick_list = frappe_fieldname in pick_list_options if pick_list_options else False
		is_bold = frappe_fieldname in bold_fieldnames if bold_fieldnames else False

		# Determine the base Frappe fieldtype BEFORE any pick-list override
		base_mapping = map_mariadb_to_frappe_type(col)
		original_fieldtype = base_mapping["fieldtype"]

		entry = {
			"fieldname": frappe_fieldname,
			"is_virtual": is_virtual,
			"is_auto_generated": is_auto_generated,
			"is_read_only": is_read_only,
			"is_pick_list": is_pick_list,
			"is_bold": is_bold,
			"original_fieldtype": original_fieldtype,
		}

		if name_field_column and wp_col_name == name_field_column:
			entry["fieldname"] = "name"
			entry["is_name"] = True

		if is_virtual:
			# I_S: GENERATION_EXPRESSION is set for generated (stored/virtual) columns
			entry["is_derived"] = True
			entry["sql_expression"] = (
				sql_generation_to_frappe_bare_sql(gen_raw, resolve_expr) if str(gen_raw).strip() else None
			)
		# else: omit is_derived / sql_expression — not an expression column

		column_mapping[wp_col_name] = entry

	stored_auto_gen = ",".join(
		wp_col for wp_col, info in column_mapping.items() if info.get("is_auto_generated")
	)
	return column_mapping, stored_auto_gen


def _create_or_update_doctype(
	doctype_name, schema, wp_table_doc, field_overrides, label_overrides,
	name_field_column, title_fieldname, read_only_fieldnames,
	pick_list_options, bold_fieldnames,
):
	"""
	Create a new DocType or update an existing one.  Handles the broken-DocType
	recovery case (duplicate field → delete and recreate).
	"""
	doctype_kwargs = dict(
		title_fieldname=title_fieldname,
		read_only_fieldnames=read_only_fieldnames,
		pick_list_options=pick_list_options,
		bold_fieldnames=bold_fieldnames,
	)

	if frappe.db.exists("DocType", doctype_name):
		frappe.msgprint(
			_("DocType {0} already exists. Updating fields...").format(doctype_name),
			indicator="orange",
		)
		try:
			update_existing_doctype(
				doctype_name, schema, wp_table_doc, field_overrides,
				label_overrides, name_field_column, **doctype_kwargs,
			)
		except Exception as update_error:
			if "appears multiple times" in str(update_error):
				frappe.log_error(
					title=f"Recreating Broken DocType: {doctype_name}",
					message=f"Update failed with duplicate field error. Deleting and recreating.\n\nError: {update_error!s}",
				)
				frappe.msgprint(
					_("DocType {0} appears to be in a broken state. Deleting and recreating...").format(doctype_name),
					indicator="orange",
				)
				frappe.delete_doc("DocType", doctype_name, force=True, ignore_permissions=True)
				frappe.db.commit()
				create_custom_doctype(
					doctype_name, schema, wp_table_doc, field_overrides,
					label_overrides, name_field_column, **doctype_kwargs,
				)
			else:
				raise
	else:
		create_custom_doctype(
			doctype_name, schema, wp_table_doc, field_overrides,
			label_overrides, name_field_column, **doctype_kwargs,
		)


def mirror_table_schema(
	wp_conn_doc, wp_table_doc, field_overrides=None, label_overrides=None,
	name_field_column=None, title_field_column=None, auto_generated_columns=None,
	modified_ts_field=None, created_ts_field=None, read_only_columns=None,
	pick_list_columns=None, bold_columns=None, column_defaults=None,
):
	"""
	Mirror a WordPress table schema to a Frappe Custom DocType.

	Orchestrates: schema introspection → DocType creation/update →
	column mapping build → WP Tables record update → workspace registration.

	Args:
		wp_conn_doc: WordPress Connection document
		wp_table_doc: WP Tables document
		field_overrides: Optional dict of {column_name: fieldtype} from user review
		label_overrides: Optional dict of {column_name: label} from user review
		name_field_column: Optional WP column name that maps directly to Frappe name
		title_field_column: Optional WP column name to use as DocType title_field
		auto_generated_columns: Columns to mark as auto-generated
		modified_ts_field: User-selected modified timestamp field
		created_ts_field: User-selected created timestamp field
		read_only_columns: Comma-separated WP columns to mark read-only
		pick_list_columns: Comma-separated WP columns to turn into Select fields
		bold_columns: Comma-separated WP columns to mark bold
		column_defaults: Optional dict ``{wp_column: text}`` for script hint defaults
		    (validated by field type; empty string clears). None = leave prior values
		    inside merged mapping except for columns being explicitly cleared — caller
		    should pass a full dict from the dialog when editing.
	"""
	import json

	try:
		# --- Phase 1: Fetch schema from WordPress ---
		with wp_connection(wp_conn_doc) as conn:
			table_name = wp_table_doc.table_name
			schema = get_table_schema(conn, table_name)

			# Auto-detect timestamp fields if not already set
			if not wp_table_doc.created_timestamp_field or not wp_table_doc.modified_timestamp_field:
				timestamps = detect_timestamp_fields(conn, table_name)
				if not wp_table_doc.created_timestamp_field and timestamps["created"]:
					wp_table_doc.created_timestamp_field = timestamps["created"]
				if not wp_table_doc.modified_timestamp_field and timestamps["modified"]:
					wp_table_doc.modified_timestamp_field = timestamps["modified"]

			pick_list_options = _fetch_pick_list_options(conn, table_name, pick_list_columns, label_overrides)

		# --- Phase 2: Resolve column option sets ---
		doctype_name = wp_table_doc.nce_name or table_name
		title_fieldname = resolve_fieldname(title_field_column, label_overrides) if title_field_column else None
		read_only_fieldnames = _parse_comma_columns(read_only_columns, label_overrides)
		bold_fieldnames = _parse_comma_columns(bold_columns, label_overrides)

		# --- Phase 2.5: Save existing layout before reconfigure ---
		saved_layout = None
		if frappe.db.exists("DocType", doctype_name):
			saved_layout = save_doctype_layout(doctype_name)

		# --- Phase 3: Create or update the DocType ---
		_create_or_update_doctype(
			doctype_name, schema, wp_table_doc, field_overrides, label_overrides,
			name_field_column, title_fieldname, read_only_fieldnames,
			pick_list_options, bold_fieldnames,
		)

		# --- Phase 4: Build & save column mapping ---
		previous_colmap = {}
		if getattr(wp_table_doc, "column_mapping", None):
			try:
				previous_colmap = json.loads(wp_table_doc.column_mapping)
			except Exception:
				previous_colmap = {}

		column_mapping, stored_auto_gen = _build_column_mapping(
			schema, label_overrides, name_field_column, auto_generated_columns,
			read_only_fieldnames, pick_list_options, bold_fieldnames,
		)
		column_mapping = _merge_column_mapping_for_mirror(column_mapping, previous_colmap)
		apply_column_defaults_to_mapping(
			column_mapping,
			column_defaults,
			resolve_fieldtype=lambda wc, e: _resolve_fieldtype_for_default_mirror(
				wc, e, field_overrides
			),
		)

		wp_table_doc.frappe_doctype = doctype_name
		wp_table_doc.mirror_status = "Mirrored"
		wp_table_doc.error_log = None
		wp_table_doc.column_mapping = json.dumps(column_mapping)
		wp_table_doc.name_field_column = name_field_column or None
		wp_table_doc.title_field_column = title_field_column or None
		wp_table_doc.auto_generated_columns = stored_auto_gen or None
		if modified_ts_field:
			wp_table_doc.modified_timestamp_field = modified_ts_field
		if created_ts_field:
			wp_table_doc.created_timestamp_field = created_ts_field
		wp_table_doc.save()

		# --- Phase 5: Register in workspace ---
		add_to_workspace(doctype_name, label=wp_table_doc.nce_name or doctype_name)
		frappe.db.commit()

	except Exception as e:
		wp_table_doc.mirror_status = "Error"
		wp_table_doc.error_log = str(e)
		wp_table_doc.save()
		frappe.db.commit()
		raise


def create_custom_doctype(
	doctype_name, schema, wp_table_doc, field_overrides=None, label_overrides=None, name_field_column=None,
	title_fieldname=None, read_only_fieldnames=None, pick_list_options=None, bold_fieldnames=None,
):
	"""
	Create a new Custom DocType programmatically.

	Args:
		doctype_name: Name for the new DocType
		schema: Schema dict from get_table_schema
		wp_table_doc: WP Tables document
		field_overrides: Optional dict of {column_name: fieldtype} from user review
		label_overrides: Optional dict of {column_name: label} from user review
		name_field_column: Optional WP column that maps directly to Frappe name (skips field creation)
		title_fieldname: Optional Frappe fieldname to use as title_field (display name in list/link views)
	"""
	# Determine naming rule
	# When name_field_column is set: use "prompt" (allows direct name assignment during insert)
	# Otherwise use first matching field or hash
	autoname = "hash"  # Default fallback
	if name_field_column:
		autoname = "prompt"
	else:
		matching_fields = get_matching_fields_list(wp_table_doc)
		if matching_fields:
			first_match_field = matching_fields[0]
			safe_fieldname = resolve_fieldname(first_match_field, label_overrides)
			autoname = f"field:{safe_fieldname}"

	# Build fields using shared helper - skip the name column (no DocType field for it)
	fields = []
	idx = 1
	for col in schema["columns"]:
		col_name = col["COLUMN_NAME"]
		if name_field_column and col_name == name_field_column:
			continue  # Skip - value goes directly into Frappe name
		field = build_frappe_field(col, schema, wp_table_doc, field_overrides, label_overrides, idx, read_only_fieldnames=read_only_fieldnames, pick_list_options=pick_list_options, bold_fieldnames=bold_fieldnames)
		fields.append(field)
		idx += 1

	# Build DocType definition
	doctype_dict = {
		"doctype": "DocType",
		"name": doctype_name,
		"module": "NCE Sync",
		"custom": 1,
		"autoname": autoname,
		"fields": fields,
		"permissions": [
			{
				"role": "System Manager",
				"read": 1,
				"write": 1,
				"create": 1,
				"delete": 1,
				"submit": 0,
				"cancel": 0,
				"amend": 0,
			}
		],
		"track_changes": 1,
	}

	# Set title field — controls display name in list views and link fields
	if title_fieldname:
		doctype_dict["title_field"] = title_fieldname
		doctype_dict["show_title_field_in_link"] = 1
		doctype_dict["search_fields"] = title_fieldname

	# Create DocType document
	doctype_doc = frappe.get_doc(doctype_dict)

	doctype_doc.insert(ignore_permissions=True)
	frappe.db.commit()


def update_existing_doctype(
	doctype_name, schema, wp_table_doc, field_overrides=None, label_overrides=None, name_field_column=None,
	title_fieldname=None, read_only_fieldnames=None, pick_list_options=None, bold_fieldnames=None,
):
	"""
	Update an existing DocType with new fields from schema.
	Adds missing fields without removing existing ones.

	Note: If the autoname setting changes (e.g., from hash to field:wp_id),
	existing records will NOT be renamed. To apply new naming to all records,
	delete the DocType and re-mirror, or manually rename records.

	Args:
		doctype_name: Name of the existing DocType
		schema: Schema dict from get_table_schema
		wp_table_doc: WP Tables document
		field_overrides: Optional dict of {column_name: fieldtype} from user review
		label_overrides: Optional dict of {column_name: label} from user review
		name_field_column: Optional WP column that maps directly to Frappe name (skips field creation)
		title_fieldname: Optional Frappe fieldname to use as title_field (display name in list/link views)
	"""
	doctype_doc = frappe.get_doc("DocType", doctype_name)

	# Update title field
	if title_fieldname:
		doctype_doc.title_field = title_fieldname
		doctype_doc.show_title_field_in_link = 1
		doctype_doc.search_fields = title_fieldname

	# Update autoname
	if name_field_column:
		new_autoname = "prompt"
	else:
		matching_fields = get_matching_fields_list(wp_table_doc)
		if matching_fields:
			first_match_field = matching_fields[0]
			safe_fieldname = resolve_fieldname(first_match_field, label_overrides)
			new_autoname = f"field:{safe_fieldname}"
		else:
			new_autoname = doctype_doc.autoname or "hash"
	if doctype_doc.autoname != new_autoname:
		doctype_doc.autoname = new_autoname
		frappe.msgprint(
			_("Updated naming rule. Note: Existing records will keep their old names."),
			indicator="orange",
		)

	# Get existing field names
	existing_fields = {f.fieldname for f in doctype_doc.fields}

	# Find new fields to add - skip name column when name_field_column is set
	new_fields_added = False
	idx = len(doctype_doc.fields) + 1

	for col in schema["columns"]:
		col_name = col["COLUMN_NAME"]
		if name_field_column and col_name == name_field_column:
			continue  # Skip - no DocType field for name column
		safe_fieldname = resolve_fieldname(col_name, label_overrides)

		if safe_fieldname not in existing_fields:
			field = build_frappe_field(col, schema, wp_table_doc, field_overrides, label_overrides, idx, read_only_fieldnames=read_only_fieldnames, pick_list_options=pick_list_options, bold_fieldnames=bold_fieldnames)
			doctype_doc.append("fields", field)
			new_fields_added = True
			idx += 1

	# Update existing field properties (read_only, bold, label, pick_list)
	fields_updated = False
	for field in doctype_doc.fields:
		fn = field.fieldname

		# Read Only
		should_ro = 1 if (read_only_fieldnames and fn in read_only_fieldnames) else 0
		if field.read_only != should_ro:
			field.read_only = should_ro
			fields_updated = True

		# Bold
		should_bold = 1 if (bold_fieldnames and fn in bold_fieldnames) else 0
		if field.bold != should_bold:
			field.bold = should_bold
			fields_updated = True

		# Pick List → Select with options
		if pick_list_options and fn in pick_list_options:
			if field.fieldtype != "Select" or field.options != pick_list_options[fn]:
				field.fieldtype = "Select"
				field.options = pick_list_options[fn]
				fields_updated = True

	if new_fields_added or fields_updated:
		doctype_doc.save(ignore_permissions=True)
		frappe.db.commit()
	else:
		frappe.msgprint(_("No changes to apply to {0}").format(doctype_name), indicator="blue")


def apply_field_settings(
	doctype_name,
	title_fieldname=None,
	label_map=None,
	read_only_fieldnames=None,
	bold_fieldnames=None,
	pick_list_fieldnames=None,
	pick_list_options=None,
	column_mapping=None,
):
	"""
	Lightweight update of display-only field properties on an existing DocType.
	Does NOT re-introspect WordPress schema or touch structural mapping.

	Used by the 'Frappe Field Settings' tab to apply label, read_only, bold,
	pick_list, and title changes without requiring a full remap or re-sync.

	Args:
		doctype_name: Name of the existing Frappe DocType.
		title_fieldname: Frappe fieldname to use as title_field (or None to clear).
		label_map: Dict of {frappe_fieldname: new_label} (only changed labels).
		read_only_fieldnames: Set of fieldnames that should be read-only.
		bold_fieldnames: Set of fieldnames that should be bold.
		pick_list_fieldnames: Set of fieldnames that should be Select (pick-list).
		pick_list_options: Dict of {fieldname: "opt1\\nopt2\\n..."} for new/updated pick-lists.
		column_mapping: Existing column_mapping dict (used for original_fieldtype lookup on pick-list revert).

	Returns:
		int: Number of fields that were modified.
	"""
	doctype_doc = frappe.get_doc("DocType", doctype_name)
	read_only_fieldnames = read_only_fieldnames or set()
	bold_fieldnames = bold_fieldnames or set()
	pick_list_fieldnames = pick_list_fieldnames or set()
	pick_list_options = pick_list_options or {}
	label_map = label_map or {}
	column_mapping = column_mapping or {}

	# Build a reverse lookup: frappe_fieldname → original_fieldtype from column_mapping
	original_types = {}
	for wp_col, entry in column_mapping.items():
		fn = entry.get("fieldname")
		if fn and entry.get("original_fieldtype"):
			original_types[fn] = entry["original_fieldtype"]

	changes = 0
	for field in doctype_doc.fields:
		fn = field.fieldname
		changed = False

		# Label
		if fn in label_map and field.label != label_map[fn]:
			field.label = label_map[fn]
			changed = True

		# Read Only
		should_ro = 1 if fn in read_only_fieldnames else 0
		if field.read_only != should_ro:
			field.read_only = should_ro
			changed = True

		# Bold
		should_bold = 1 if fn in bold_fieldnames else 0
		if field.bold != should_bold:
			field.bold = should_bold
			changed = True

		# Pick List ON → set fieldtype to Select with options
		if fn in pick_list_fieldnames:
			if fn in pick_list_options:
				if field.fieldtype != "Select" or field.options != pick_list_options[fn]:
					field.fieldtype = "Select"
					field.options = pick_list_options[fn]
					changed = True
		else:
			# Pick List OFF → revert to original type if currently a pick-list Select
			# Check column_mapping to see if this was a pick-list (not a native ENUM Select)
			was_pick_list = any(
				e.get("fieldname") == fn and e.get("is_pick_list")
				for e in column_mapping.values()
			)
			if was_pick_list and field.fieldtype == "Select":
				revert_type = original_types.get(fn, "Data")
				field.fieldtype = revert_type
				field.options = None
				changed = True

		if changed:
			changes += 1

	# Update title field
	old_title = doctype_doc.title_field
	if title_fieldname and title_fieldname != old_title:
		doctype_doc.title_field = title_fieldname
		doctype_doc.show_title_field_in_link = 1
		doctype_doc.search_fields = title_fieldname
		changes += 1
	elif not title_fieldname and old_title:
		doctype_doc.title_field = None
		doctype_doc.show_title_field_in_link = 0
		doctype_doc.search_fields = None
		changes += 1

	if changes:
		doctype_doc.save(ignore_permissions=True)
		frappe.db.commit()

	return changes


# ---------------------------------------------------------------------------
#  DocType layout save / restore — for reconfigure
# ---------------------------------------------------------------------------

BREAK_TYPES = ("Section Break", "Column Break", "Tab Break", "Fold")


def save_doctype_layout(doctype_name):
	"""Snapshot the current field layout of a DocType.

	Returns a list of dicts: [{fieldname, idx, fieldtype, parentfield}, ...]
	Includes Section/Column/Tab Break entries so their positions can be
	replayed after a reconfigure.

	Args:
		doctype_name: Frappe DocType name

	Returns:
		list[dict] — ordered layout snapshot, or [] if DocType not found
	"""
	if not frappe.db.exists("DocType", doctype_name):
		return []

	doctype_doc = frappe.get_doc("DocType", doctype_name)
	layout = []

	for i, field in enumerate(doctype_doc.fields):
		layout.append({
			"fieldname": field.fieldname,
			"idx": i + 1,  # 1-based position (same as idx column)
			"fieldtype": field.fieldtype,
			"parentfield": field.parentfield,  # "fields"
		})

	return layout


def restore_doctype_layout(doctype_name, layout):
	"""Restore a saved field layout onto a DocType after reconfigure.

	For each entry in the layout:
	- If the field exists in the DocType, set its idx
	- If a break field (Section/Column/Tab Break) doesn't exist, insert it
	  at the correct position

	The layout is replayed by rebuilding the fields list in saved order.

	Args:
		doctype_name: Frappe DocType name
		layout: list[dict] from save_doctype_layout()

	Returns:
		bool — True if layout was restored, False if nothing to do
	"""
	if not layout or not doctype_name:
		return False

	doctype_doc = frappe.get_doc("DocType", doctype_name)
	if not doctype_doc.fields:
		return False

	# Build a lookup of current fields by fieldname
	current_by_name = {f.fieldname: f for f in doctype_doc.fields}

	# First pass: update idx on all matching fields
	for entry in layout:
		fn = entry["fieldname"]
		if fn in current_by_name:
			current_by_name[fn].idx = entry["idx"]

	# Second pass: insert missing break fields at their saved positions
	for entry in layout:
		fn = entry["fieldname"]
		if fn not in current_by_name and entry["fieldtype"] in BREAK_TYPES:
			# Create the break field at the saved idx
			break_field = {
				"fieldname": fn,
				"fieldtype": entry["fieldtype"],
				"idx": entry["idx"],
			}
			# Extract label from fieldname for break fields
			if entry["fieldtype"] != "Column Break":
				label = fn.replace("_", " ").title()
				# Strip common suffixes
				for suffix in ("Section", "Column", "Tab", "Break"):
					label = label.replace(suffix.strip(), "").strip()
				if label:
					break_field["label"] = label
			doctype_doc.append("fields", break_field)

	# Re-number all fields sequentially to avoid idx gaps/conflicts
	doctype_doc.fields.sort(key=lambda f: _layout_sort_key(f, layout))
	for i, field in enumerate(doctype_doc.fields):
		field.idx = i + 1

	doctype_doc.save(ignore_permissions=True)
	frappe.db.commit()

	return True


def _layout_sort_key(field, layout):
	"""Return the saved layout position for a field, or a large number if not in layout."""
	for entry in layout:
		if entry["fieldname"] == field.fieldname:
			return entry["idx"]
	# New fields not in the saved layout go to the end
	return 9999
