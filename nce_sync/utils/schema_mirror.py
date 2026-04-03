# Copyright (c) 2026, Oliver Reid and contributors
# For license information, please see license.txt

"""
Schema mirroring utilities for NCE_Sync.
Handles WordPress database introspection and Frappe DocType generation.
"""

import frappe
import pymysql
from frappe import _

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


def build_frappe_field(col, schema, wp_table_doc, field_overrides=None, label_overrides=None, idx=1, read_only_fieldnames=None, pick_list_options=None):
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

	# Virtual/generated columns → Frappe virtual field with Python expression
	extra = col.get("EXTRA", "") or ""
	is_virtual_col = "VIRTUAL" in extra.upper() or "GENERATED" in extra.upper()
	if is_virtual_col:
		from nce_sync.utils.sql_to_python import sql_generation_to_python

		gen_expr = col.get("GENERATION_EXPRESSION", "") or ""
		python_expr = sql_generation_to_python(gen_expr, label_overrides)
		field["is_virtual"] = 1
		field["reqd"] = 0  # virtual fields can't be required
		if python_expr:
			field["options"] = python_expr

	return field


def get_wp_connection(wp_conn_doc):
	"""
	Establish PyMySQL connection to WordPress database.

	Args:
		wp_conn_doc: WordPress Connection document

	Returns:
		pymysql.Connection or None
	"""
	try:
		conn = pymysql.connect(
			host=wp_conn_doc.host,
			port=wp_conn_doc.port or 3306,
			user=wp_conn_doc.username,
			password=wp_conn_doc.get_password("password"),
			database=wp_conn_doc.database,
			charset="utf8mb4",
			cursorclass=pymysql.cursors.DictCursor,
		)
		return conn
	except Exception as e:
		frappe.log_error(title="WordPress Connection Error", message=str(e))
		raise


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
	import json

	conn = get_wp_connection(wp_conn_doc)
	table_name = wp_table_doc.table_name

	schema = get_table_schema(conn, table_name)

	# Auto-detect timestamp fields
	timestamps = detect_timestamp_fields(conn, table_name)
	conn.close()

	# Get previously selected matching fields
	previous_matching = []
	if wp_table_doc.matching_fields:
		previous_matching = [f.strip() for f in wp_table_doc.matching_fields.split(",") if f.strip()]

	# Get previously selected name field column (if any)
	previous_name_column = getattr(wp_table_doc, "name_field_column", None) or None

	# Get previously saved auto-generated columns
	previous_auto_gen = []
	auto_gen_raw = getattr(wp_table_doc, "auto_generated_columns", None) or ""
	if auto_gen_raw:
		previous_auto_gen = [c.strip().lower() for c in auto_gen_raw.split(",") if c.strip()]

	# Build lookup of existing Frappe field labels, read-only and pick-list state (for remap)
	existing_field_labels = {}
	existing_columns = set()
	previous_read_only = []
	previous_pick_list = []
	if wp_table_doc.frappe_doctype and frappe.db.exists("DocType", wp_table_doc.frappe_doctype):
		col_map_raw = getattr(wp_table_doc, "column_mapping", None)
		if col_map_raw:
			col_map = json.loads(col_map_raw)
			existing_columns = set(col_map.keys())
			# Extract previously saved read-only and pick-list columns (WP column names, lowercased for JS)
			for wp_col, info in col_map.items():
				if isinstance(info, dict):
					if info.get("is_read_only"):
						previous_read_only.append(wp_col.lower())
					if info.get("is_pick_list"):
						previous_pick_list.append(wp_col.lower())

		meta = frappe.get_meta(wp_table_doc.frappe_doctype)
		fieldname_to_label = {df.fieldname: df.label for df in meta.fields}

		for wp_col in existing_columns:
			mapping_info = col_map.get(wp_col, {})
			fn = mapping_info.get("fieldname", wp_col.lower()) if isinstance(mapping_info, dict) else mapping_info
			if fn in fieldname_to_label:
				existing_field_labels[wp_col] = fieldname_to_label[fn]

	preview = []
	for col in schema["columns"]:
		col_name = col["COLUMN_NAME"]
		field_mapping = map_mariadb_to_frappe_type(col)

		is_unique = any(col_name in uk for uk in schema["unique_keys"].values())
		is_indexed = any(col_name in idx_cols for idx_cols in schema["indexes"].values())
		is_pk = col_name in schema.get("primary_key", [])

		# Check if this is a virtual/generated column
		extra = col.get("EXTRA", "") or ""
		is_virtual = "VIRTUAL" in extra.upper() or "GENERATED" in extra.upper()
		is_auto_increment = "AUTO_INCREMENT" in extra.upper()

		# Use existing Frappe label if available, otherwise auto-generate
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
			}
		)

	return {
		"fields": preview,
		"timestamps": timestamps,
		"doctype_name": wp_table_doc.nce_name or table_name,
		"previous_matching_fields": previous_matching,
		"previous_name_field_column": previous_name_column,
		"previous_title_field_column": getattr(wp_table_doc, "title_field_column", None) or None,
		"previous_auto_generated_columns": previous_auto_gen,
		"previous_modified_ts": getattr(wp_table_doc, "modified_timestamp_field", None) or "",
		"previous_created_ts": getattr(wp_table_doc, "created_timestamp_field", None) or "",
		"previous_read_only_columns": previous_read_only,
		"previous_pick_list_columns": previous_pick_list,
	}


def mirror_table_schema(wp_conn_doc, wp_table_doc, field_overrides=None, label_overrides=None, name_field_column=None, title_field_column=None, auto_generated_columns=None, modified_ts_field=None, created_ts_field=None, read_only_columns=None, pick_list_columns=None):
	"""
	Mirror a WordPress table schema to a Frappe Custom DocType.

	Args:
		wp_conn_doc: WordPress Connection document
		wp_table_doc: WP Tables document
		field_overrides: Optional dict of {column_name: fieldtype} from user review
		label_overrides: Optional dict of {column_name: label} from user review
		name_field_column: Optional WP column name that maps directly to Frappe name (skips field creation)
		title_field_column: Optional WP column name to use as DocType title_field (display name)
	"""
	try:
		# Connect to WordPress DB
		conn = get_wp_connection(wp_conn_doc)
		table_name = wp_table_doc.table_name

		# Get schema
		schema = get_table_schema(conn, table_name)

		# Auto-detect timestamp fields if not already set by user
		if not wp_table_doc.created_timestamp_field or not wp_table_doc.modified_timestamp_field:
			timestamps = detect_timestamp_fields(conn, table_name)
			# Only set if user hasn't provided values (source-of-truth enforcement)
			if not wp_table_doc.created_timestamp_field and timestamps["created"]:
				wp_table_doc.created_timestamp_field = timestamps["created"]
			if not wp_table_doc.modified_timestamp_field and timestamps["modified"]:
				wp_table_doc.modified_timestamp_field = timestamps["modified"]

		# Query DISTINCT values for pick-list columns before closing the connection
		pick_list_options = {}  # {frappe_fieldname: "opt1\nopt2\n..."}
		if pick_list_columns:
			if isinstance(pick_list_columns, str):
				pl_cols = [c.strip() for c in pick_list_columns.split(",") if c.strip()]
			else:
				pl_cols = list(pick_list_columns)
			cursor = conn.cursor()
			for col_name in pl_cols:
				try:
					# Limit to 500 distinct values to avoid absurdly long option lists
					cursor.execute(
						f"SELECT DISTINCT `{col_name}` FROM `{table_name}` "
						f"WHERE `{col_name}` IS NOT NULL AND `{col_name}` != '' "
						f"ORDER BY `{col_name}` LIMIT 500"
					)
					values = [str(row[col_name]) for row in cursor.fetchall() if row[col_name] is not None]
					if values:
						fieldname = resolve_fieldname(col_name, label_overrides)
						pick_list_options[fieldname] = "\n".join(values)
				except Exception:
					# If the query fails (e.g. column doesn't exist), skip silently
					pass
			cursor.close()

		conn.close()

		# Determine DocType name
		doctype_name = wp_table_doc.nce_name or table_name

		# Resolve title_field_column to Frappe fieldname (if provided)
		title_fieldname = None
		if title_field_column:
			title_fieldname = resolve_fieldname(title_field_column, label_overrides)

		# Parse read_only_columns (comma-separated WP column names) into a set of Frappe fieldnames
		read_only_fieldnames = set()
		if read_only_columns:
			if isinstance(read_only_columns, str):
				ro_cols = [c.strip() for c in read_only_columns.split(",") if c.strip()]
			else:
				ro_cols = list(read_only_columns)
			for col_name in ro_cols:
				read_only_fieldnames.add(resolve_fieldname(col_name, label_overrides))

		# Check if DocType already exists
		if frappe.db.exists("DocType", doctype_name):
			# Update existing DocType
			frappe.msgprint(
				_("DocType {0} already exists. Updating fields...").format(doctype_name),
				indicator="orange",
			)
			try:
				update_existing_doctype(
					doctype_name, schema, wp_table_doc, field_overrides, label_overrides, name_field_column,
					title_fieldname=title_fieldname,
					read_only_fieldnames=read_only_fieldnames,
					pick_list_options=pick_list_options,
				)
			except Exception as update_error:
				# If update fails (e.g., duplicate field error), try deleting and recreating
				if "appears multiple times" in str(update_error):
					frappe.log_error(
						title=f"Recreating Broken DocType: {doctype_name}",
						message=f"Update failed with duplicate field error. Deleting and recreating.\n\nError: {update_error!s}",
					)
					frappe.msgprint(
						_("DocType {0} appears to be in a broken state. Deleting and recreating...").format(
							doctype_name
						),
						indicator="orange",
					)
					# Delete the broken DocType
					frappe.delete_doc("DocType", doctype_name, force=True, ignore_permissions=True)
					frappe.db.commit()
					# Recreate it
					create_custom_doctype(
						doctype_name, schema, wp_table_doc, field_overrides, label_overrides, name_field_column,
						title_fieldname=title_fieldname,
						read_only_fieldnames=read_only_fieldnames,
						pick_list_options=pick_list_options,
					)
				else:
					raise  # Re-raise if it's a different error
		else:
			# Create new Custom DocType
			create_custom_doctype(
				doctype_name, schema, wp_table_doc, field_overrides, label_overrides, name_field_column,
				title_fieldname=title_fieldname,
				read_only_fieldnames=read_only_fieldnames,
				pick_list_options=pick_list_options,
			)

		# Build column mapping: WP column name -> mapping info
		# Frappe lowercases all fieldnames, so we need to track the original WP names
		# Also track if column is virtual/generated (for reverse sync)
		# When name_field_column is set, that column maps to "name" with is_name=True
		import json

		# Normalise auto_generated_columns to a set of lowercase column names for quick lookup
		auto_gen_set = set()
		if auto_generated_columns:
			auto_gen_set = {c.strip().lower() for c in auto_generated_columns if c.strip()}

		column_mapping = {}
		for col in schema["columns"]:
			wp_col_name = col["COLUMN_NAME"]
			extra = col.get("EXTRA", "") or ""
			is_virtual = "VIRTUAL" in extra.upper() or "GENERATED" in extra.upper()
			is_auto_increment = "AUTO_INCREMENT" in extra.upper()
			is_auto_generated = wp_col_name.lower() in auto_gen_set or is_auto_increment
			frappe_fieldname = resolve_fieldname(wp_col_name, label_overrides)
			is_read_only = frappe_fieldname in read_only_fieldnames if read_only_fieldnames else False
			is_pick_list = frappe_fieldname in pick_list_options if pick_list_options else False
			if name_field_column and wp_col_name == name_field_column:
				column_mapping[wp_col_name] = {
					"fieldname": "name",
					"is_virtual": is_virtual,
					"is_auto_generated": is_auto_generated,
					"is_read_only": is_read_only,
					"is_pick_list": is_pick_list,
					"is_name": True,
				}
			else:
				column_mapping[wp_col_name] = {
					"fieldname": frappe_fieldname,
					"is_virtual": is_virtual,
					"is_auto_generated": is_auto_generated,
					"is_read_only": is_read_only,
					"is_pick_list": is_pick_list,
				}

		# Derive auto_generated_columns string from mapping for storage
		stored_auto_gen = ",".join(
			wp_col for wp_col, info in column_mapping.items() if info.get("is_auto_generated")
		)

		# Update WP Tables record
		wp_table_doc.frappe_doctype = doctype_name
		wp_table_doc.mirror_status = "Mirrored"
		wp_table_doc.error_log = None
		wp_table_doc.column_mapping = json.dumps(column_mapping)
		wp_table_doc.name_field_column = name_field_column or None
		wp_table_doc.title_field_column = title_field_column or None
		wp_table_doc.auto_generated_columns = stored_auto_gen or None
		# Timestamp fields: user selection from the mirror dialog takes precedence
		if modified_ts_field:
			wp_table_doc.modified_timestamp_field = modified_ts_field
		if created_ts_field:
			wp_table_doc.created_timestamp_field = created_ts_field
		wp_table_doc.save()

		# Add to workspace
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
	title_fieldname=None, read_only_fieldnames=None, pick_list_options=None,
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
		field = build_frappe_field(col, schema, wp_table_doc, field_overrides, label_overrides, idx, read_only_fieldnames=read_only_fieldnames, pick_list_options=pick_list_options)
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
	title_fieldname=None, read_only_fieldnames=None, pick_list_options=None,
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
			field = build_frappe_field(col, schema, wp_table_doc, field_overrides, label_overrides, idx, read_only_fieldnames=read_only_fieldnames, pick_list_options=pick_list_options)
			doctype_doc.append("fields", field)
			new_fields_added = True
			idx += 1

	if new_fields_added:
		doctype_doc.save(ignore_permissions=True)
		frappe.db.commit()
	else:
		frappe.msgprint(_("No new fields to add to {0}").format(doctype_name), indicator="blue")
