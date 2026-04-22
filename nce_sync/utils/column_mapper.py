# Copyright (c) 2026, Oliver Reid and contributors
# For license information, please see license.txt

"""
Unified column-mapping utilities for NCE Sync.

This module is the **single source of truth** for:

* Loading / parsing the ``column_mapping`` JSON stored on WP Tables docs.
* Translating between WordPress column names and Frappe fieldnames
  (forward and reverse).
* Building a ``{wp_column: value}`` row from a Frappe document, ready for
  SQL INSERT / UPDATE back to WordPress.

Every module that needs to work with column mappings should import from
here rather than re-implementing parsing or lookup logic.

**Derived (generated) columns** — on schema mirror / “Regenerate column mapping”
the app fills ``is_derived`` and ``sql_expression`` (Frappe fieldnames in SQL) from
MariaDB ``INFORMATION_SCHEMA.COLUMNS.GENERATION_EXPRESSION``; a refresh overwrites
those two keys; see ``_merge_column_mapping_for_mirror`` in
``nce_sync.utils.schema_mirror``. Example fragment::

	"sku": {
		"fieldname": "sku",
		"is_virtual": true,
		"is_derived": true,
		"sql_expression": "CONCAT(`prefix_text`, UPPER(`raw_sku`))",
		"is_auto_generated": false
	}

Downstream: NCE_Events ``get_derived_sql_specs_api`` / ``evaluate_sql_expressions_api``.
"""

import json

import frappe

from nce_sync.utils.constants import FRAPPE_SYSTEM_FIELDS


# ---------------------------------------------------------------------------
# Loading & parsing
# ---------------------------------------------------------------------------


def load_column_mapping(wp_table_doc):
	"""
	Parse ``column_mapping`` from a WP Tables document into a Python dict.

	Handles all edge cases (None, empty string, already-a-dict) consistently
	so callers never need to repeat the defensive parsing.

	Args:
		wp_table_doc: WP Tables document (or any object with a
		              ``column_mapping`` attribute).

	Returns:
		dict — may be empty but never None.
	"""
	raw = getattr(wp_table_doc, "column_mapping", None) or "{}"
	if isinstance(raw, dict):
		return raw
	return json.loads(raw)


# ---------------------------------------------------------------------------
# Forward lookup: WP column → Frappe fieldname
# ---------------------------------------------------------------------------


def get_frappe_fieldname(wp_col, column_mapping):
	"""
	Get the Frappe fieldname for a WordPress column.

	Handles both the legacy format (``{wp_col: "fieldname"}``) and the
	current dict format (``{wp_col: {"fieldname": "...", ...}}``).

	Falls back to ``wp_col.lower()`` when the column is absent from the
	mapping.

	Args:
		wp_col: WordPress column name.
		column_mapping: Parsed column mapping dict (from
		                :func:`load_column_mapping`).

	Returns:
		str — the Frappe fieldname.
	"""
	if column_mapping and wp_col in column_mapping:
		info = column_mapping[wp_col]
		if isinstance(info, dict):
			return info["fieldname"]
		return info
	return wp_col.lower()


# ---------------------------------------------------------------------------
# Reverse lookup: Frappe fieldname → WP column
# ---------------------------------------------------------------------------


def build_reverse_mapping(column_mapping):
	"""
	Build ``{frappe_fieldname: wp_column_name}`` from a column mapping.

	Args:
		column_mapping: Parsed column mapping dict.

	Returns:
		dict mapping Frappe fieldnames to WP column names.
	"""
	reverse = {}
	for wp_col, info in (column_mapping or {}).items():
		if isinstance(info, dict):
			reverse[info["fieldname"]] = wp_col
		else:
			reverse[info] = wp_col
	return reverse


# ---------------------------------------------------------------------------
# Auto-generated column helpers
# ---------------------------------------------------------------------------


def get_auto_generated_columns(wp_table_doc):
	"""
	Return a ``set`` of WP column names that are auto-generated
	(AUTO_INCREMENT, VIRTUAL / GENERATED computed columns).

	These must **never** appear in INSERT or UPDATE statements sent to WP.

	Args:
		wp_table_doc: WP Tables document.

	Returns:
		set[str]
	"""
	raw = getattr(wp_table_doc, "auto_generated_columns", None) or ""
	return {c.strip() for c in raw.split(",") if c.strip()}


def get_name_wp_column(wp_table_doc, column_mapping=None):
	"""
	Return the WP column marked ``is_name`` (the primary-key column that
	maps to Frappe ``name``).

	Tries the ``name_field_column`` attribute first (fast path).  Falls back
	to scanning the mapping for ``is_name: True``.

	Args:
		wp_table_doc: WP Tables document.
		column_mapping: Optional pre-parsed mapping (avoids re-parsing).

	Returns:
		str or None.
	"""
	nfc = getattr(wp_table_doc, "name_field_column", None)
	if nfc:
		return nfc

	if column_mapping is None:
		column_mapping = load_column_mapping(wp_table_doc)
	for wp_col, info in column_mapping.items():
		if isinstance(info, dict) and info.get("is_name"):
			return wp_col
	return None


# ---------------------------------------------------------------------------
# Row builder: Frappe doc → WP row  (unified for live_sync & reverse_sync)
# ---------------------------------------------------------------------------


def build_wp_row(frappe_doc, wp_table_doc, column_mapping=None):
	"""
	Build a ``{wp_column: value}`` dict from a Frappe document, ready for
	SQL INSERT or UPDATE back to WordPress.

	Skips:

	* Frappe system fields (name, owner, creation, modified, …)
	* Auto-generated / computed WP columns
	* The WP primary-key column (WP owns that value)
	* Virtual columns (is_virtual)

	Boolean values are coerced to ``1`` / ``0`` for MySQL compatibility.

	This is the **single implementation** used by both ``live_sync`` and
	``reverse_sync``; there is no second copy.

	Args:
		frappe_doc: The Frappe document to convert.
		wp_table_doc: The WP Tables document (for auto-gen columns, PK).
		column_mapping: Pre-parsed mapping dict.  Loaded from
		                ``wp_table_doc`` if not supplied.

	Returns:
		dict — ``{wp_column_name: value}``.  May be empty if there are no
		writable columns.
	"""
	if column_mapping is None:
		column_mapping = load_column_mapping(wp_table_doc)

	reverse_mapping = build_reverse_mapping(column_mapping)
	auto_gen_cols = get_auto_generated_columns(wp_table_doc)
	name_wp_col = get_name_wp_column(wp_table_doc, column_mapping)

	row = {}

	# Walk the Frappe meta fields rather than the mapping so we only touch
	# fields that actually exist on the DocType.
	for df in frappe.get_meta(frappe_doc.doctype).fields:
		fieldname = df.fieldname
		if fieldname in FRAPPE_SYSTEM_FIELDS:
			continue

		wp_col = reverse_mapping.get(fieldname)
		if not wp_col:
			continue

		# Consult the mapping metadata for this WP column
		mapping_info = column_mapping.get(wp_col, {})
		if isinstance(mapping_info, dict):
			if mapping_info.get("is_virtual"):
				continue
			if mapping_info.get("is_auto_generated"):
				continue
			if mapping_info.get("is_name"):
				continue

		# Belt-and-suspenders: also check the flat auto_gen set and PK
		if wp_col in auto_gen_cols:
			continue
		if wp_col == name_wp_col:
			continue

		val = frappe_doc.get(fieldname)
		# Coerce Python booleans to MySQL-friendly ints
		if val is True:
			val = 1
		elif val is False:
			val = 0
		row[wp_col] = val

	return row
