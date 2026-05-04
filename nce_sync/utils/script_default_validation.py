# Copyright (c) 2026, Oliver Reid and contributors
# For license information, please see license.txt

"""Validate optional script default values stored in WP Tables ``column_mapping``."""

from __future__ import annotations

import json
import re
from datetime import date, datetime

import frappe
from frappe import _

_TIME_SQL_RE = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d:[0-5]\d(\.\d+)?$")

_CHECK_TRUE = frozenset({"1", "true", "yes", "y", "t"})
_CHECK_FALSE = frozenset({"0", "false", "no", "n", "f"})


def _strip_sql_quotes(s: str) -> str:
	s = s.strip()
	if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
		return s[1:-1].strip()
	return s


def validate_and_normalize_script_default(fieldtype: str, raw: str, wp_column: str) -> str:
	"""
	Validate a non-empty script default for the given Frappe fieldtype.
	Returns the string to store in ``column_mapping["default_value"]``.
	"""
	if raw is None:
		frappe.throw(_("Default value for column {0} is invalid").format(wp_column))
	raw_in = raw if isinstance(raw, str) else str(raw)
	if not raw_in.strip():
		frappe.throw(_("Default value for column {0} is invalid").format(wp_column))

	ft = (fieldtype or "Data").strip() or "Data"

	if ft == "Date":
		core = _strip_sql_quotes(raw_in.strip())
		try:
			date.fromisoformat(core)
		except ValueError:
			frappe.throw(
				_("Default for column {0} must be a SQL date (YYYY-MM-DD), got {1}").format(
					wp_column, frappe.bold(raw_in.strip())
				)
			)
		return core

	if ft == "Datetime":
		core = _strip_sql_quotes(raw_in.strip())
		parsed_ok = False
		for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
			try:
				datetime.strptime(core, fmt)
				parsed_ok = True
				break
			except ValueError:
				continue
		if not parsed_ok:
			try:
				datetime.fromisoformat(core.replace("Z", "+00:00"))
				parsed_ok = True
			except ValueError:
				parsed_ok = False
		if not parsed_ok:
			frappe.throw(
				_(
					"Default for column {0} must be SQL datetime (YYYY-MM-DD HH:MM:SS), got {1}"
				).format(wp_column, frappe.bold(raw_in.strip()))
			)
		return core

	if ft == "Time":
		core = _strip_sql_quotes(raw_in.strip())
		if not _TIME_SQL_RE.match(core):
			frappe.throw(
				_("Default for column {0} must be 24-hour SQL time (HH:MM:SS), got {1}").format(
					wp_column, frappe.bold(raw_in.strip())
				)
			)
		return core

	if ft == "JSON":
		stripped = raw_in.strip()
		try:
			json.loads(stripped)
		except json.JSONDecodeError as e:
			frappe.throw(
				_("Default for column {0} must be valid JSON: {1}").format(wp_column, str(e))
			)
		return stripped

	if ft == "Int":
		try:
			v = int(raw_in.strip(), 10)
		except ValueError:
			frappe.throw(
				_("Default for column {0} must be an integer, got {1}").format(
					wp_column, frappe.bold(raw_in.strip())
				)
			)
		return str(v)

	if ft in ("Float", "Currency", "Percent"):
		t = raw_in.strip()
		try:
			float(t.replace(",", ""))
		except ValueError:
			frappe.throw(
				_("Default for column {0} must be a number, got {1}").format(
					wp_column, frappe.bold(t)
				)
			)
		return t

	if ft == "Check":
		low = raw_in.strip().lower()
		if low not in _CHECK_TRUE and low not in _CHECK_FALSE:
			frappe.throw(
				_("Default for column {0} must be 0/1, true/false, or yes/no, got {1}").format(
					wp_column, frappe.bold(raw_in.strip())
				)
			)
		return "1" if low in _CHECK_TRUE else "0"

	if ft == "Rating":
		try:
			r = int(raw_in.strip(), 10)
			if r < 0 or r > 5:
				raise ValueError
		except ValueError:
			frappe.throw(
				_("Default for column {0} must be a whole number from 0 to 5, got {1}").format(
					wp_column, frappe.bold(raw_in.strip())
				)
			)
		return str(r)

	# Data, Small Text, Text, Long Text, Select, Password, etc.
	return raw_in
