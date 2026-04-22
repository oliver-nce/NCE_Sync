# Copyright (c) 2026, Oliver Reid and contributors
# For license information, please see license.txt

"""
Rewrite MySQL/MariaDB GENERATION_EXPRESSION to a scalar SQL string that uses
Frappe DocType fieldnames as identifiers (not source DB column names).

NCE_Events and similar consumers can evaluate with ``SELECT <expr> AS _v FROM DUAL``,
binding current row values by Frappe fieldname.

The translator mirrors the patterns supported by ``sql_to_python`` (same
function coverage) but outputs SQL, not ``doc.``-style Python.
"""

import re
from nce_sync.utils.sql_to_python import (  # noqa: PLC2701 re-use vetted expression parsing helpers
	_is_numeric_literal,
	_is_string_literal,
	_split_args,
)


# MariaDB / MySQL names that are unsafe as bare identifiers in some contexts
# (minimal set; add as needed for operator-facing docs).
_MYSQL_BARE_ID_SAFE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _emit_identifier(frappe_fieldname: str) -> str:
	"""Return SQL fragment: prefer bare Frappe fieldname, else backtick-quote."""
	if _MYSQL_BARE_ID_SAFE.match(frappe_fieldname):
		lower = frappe_fieldname.lower()
		# 'name' is a common Frappe field; quote for SQL DUAL context when literal `name` keyword risk
		if lower in (
			"order",
			"group",
			"select",
			"from",
			"where",
			"and",
			"or",
			"as",
			"key",
			"read",
			"name",
		):
			return f"`{frappe_fieldname}`"
		return frappe_fieldname
	return f"`{frappe_fieldname}`"


# --- SQL function handlers (return SQL, not Python) ---


def _f_concat(args_str, _resolve):
	parts = _split_args(args_str)
	sql_parts = [_translate_expr_frappe_sql(p.strip(), _resolve) for p in parts]
	return "CONCAT(" + ", ".join(sql_parts) + ")"


def _f_concat_ws(args_str, _resolve):
	parts = _split_args(args_str)
	if len(parts) < 2:
		return "CONCAT()"
	sep = _translate_expr_frappe_sql(parts[0].strip(), _resolve)
	rest = [_translate_expr_frappe_sql(p.strip(), _resolve) for p in parts[1:]]
	return "CONCAT_WS(" + ", ".join([sep] + rest) + ")"


def _f_ifnull(args_str, _resolve):
	parts = _split_args(args_str)
	if len(parts) < 2:
		return "NULL"
	a = _translate_expr_frappe_sql(parts[0].strip(), _resolve)
	b = _translate_expr_frappe_sql(parts[1].strip(), _resolve)
	return f"IFNULL({a}, {b})"


def _f_isnull(args_str, _resolve):
	inner = _translate_expr_frappe_sql(args_str.strip(), _resolve)
	return f"ISNULL({inner})"


def _f_coalesce(args_str, _resolve):
	"""2+ args → COALESCE(a,b,...)  (IFNULL is two-arg only)"""
	parts = _split_args(args_str)
	if len(parts) < 2:
		return "NULL"
	inner = [_translate_expr_frappe_sql(p.strip(), _resolve) for p in parts]
	return "COALESCE(" + ", ".join(inner) + ")"


def _f_upper(args_str, _resolve):
	inner = _translate_expr_frappe_sql(args_str.strip(), _resolve)
	return f"UPPER({inner})"


def _f_lower(args_str, _resolve):
	inner = _translate_expr_frappe_sql(args_str.strip(), _resolve)
	return f"LOWER({inner})"


def _f_trim(args_str, _resolve):
	inner = _translate_expr_frappe_sql(args_str.strip(), _resolve)
	return f"TRIM({inner})"


def _f_left(args_str, _resolve):
	parts = _split_args(args_str)
	if len(parts) < 2:
		return "NULL"
	a = _translate_expr_frappe_sql(parts[0].strip(), _resolve)
	n = parts[1].strip()
	return f"LEFT({a}, {n})"


def _f_right(args_str, _resolve):
	parts = _split_args(args_str)
	if len(parts) < 2:
		return "NULL"
	a = _translate_expr_frappe_sql(parts[0].strip(), _resolve)
	n = parts[1].strip()
	return f"RIGHT({a}, {n})"


def _f_year(args_str, _resolve):
	inner = _translate_expr_frappe_sql(args_str.strip(), _resolve)
	return f"YEAR({inner})"


def _f_month(args_str, _resolve):
	inner = _translate_expr_frappe_sql(args_str.strip(), _resolve)
	return f"MONTH({inner})"


def _f_round(args_str, _resolve):
	parts = _split_args(args_str)
	a = _translate_expr_frappe_sql(parts[0].strip(), _resolve)
	if len(parts) >= 2:
		n = parts[1].strip()
		return f"ROUND({a}, {n})"
	return f"ROUND({a})"


_SQL_FNS = {
	"CONCAT": _f_concat,
	"CONCAT_WS": _f_concat_ws,
	"IFNULL": _f_ifnull,
	"ISNULL": _f_isnull,
	"COALESCE": _f_coalesce,
	"UPPER": _f_upper,
	"UCASE": _f_upper,
	"LOWER": _f_lower,
	"LCASE": _f_lower,
	"TRIM": _f_trim,
	"LEFT": _f_left,
	"RIGHT": _f_right,
	"YEAR": _f_year,
	"MONTH": _f_month,
	"ROUND": _f_round,
}


def _translate_expr_frappe_sql(sql_expr, _resolve):
	"""
	Recursively translate a SQL fragment: column refs → Frappe field identifiers.
	"""
	sql_expr = sql_expr.strip()
	if not sql_expr:
		return "NULL"

	if _is_string_literal(sql_expr):
		return sql_expr

	if _is_numeric_literal(sql_expr):
		return sql_expr

	if sql_expr.upper() == "NULL":
		return "NULL"

	func_match = re.match(r"^(\w+)\s*\((.+)\)$", sql_expr, re.DOTALL)
	if func_match:
		func_name = func_match.group(1).upper()
		args_str = func_match.group(2)
		if func_name in _SQL_FNS:
			return _SQL_FNS[func_name](args_str, _resolve)
		raw_name = func_match.group(1)
		parts = _split_args(args_str) if args_str else []
		if parts:
			translated = [_translate_expr_frappe_sql(p.strip(), _resolve) for p in parts]
			return f"{raw_name}(" + ", ".join(translated) + ")"
		return f"{raw_name}()"

	arith_match = re.match(r"^(.+?)\s*([+\-*/])\s*(.+)$", sql_expr)
	if arith_match:
		left = _translate_expr_frappe_sql(arith_match.group(1), _resolve)
		op = arith_match.group(2)
		right = _translate_expr_frappe_sql(arith_match.group(3), _resolve)
		return f"({left} {op} {right})"

	backtick_match = re.match(r"^`(.+)`$", sql_expr)
	if backtick_match:
		return _emit_identifier(_resolve(backtick_match.group(1)))

	if re.match(r"^[a-zA-Z_]\w*$", sql_expr):
		return _emit_identifier(_resolve(sql_expr))

	# unrecognised — return as-is (admin-trusted; consumer may need to fix)
	return sql_expr


def _strip_wrapping_parens(expr):
	expr = expr.strip()
	while expr.startswith("(") and expr.endswith(")"):
		depth = 0
		balanced = True
		for i, ch in enumerate(expr):
			if ch == "(":
				depth += 1
			elif ch == ")":
				depth -= 1
			if depth == 0 and i < len(expr) - 1:
				balanced = False
				break
		if balanced:
			expr = expr[1:-1].strip()
		else:
			break
	return expr


def sql_generation_to_frappe_bare_sql(
	generation_expression,
	resolve_column,
):
	"""
	Convert information_schema.COLUMNS.GENERATION_EXPRESSION to SQL using
	Frappe fieldnames (via ``resolve_column``).

	Args:
		generation_expression: raw SQL from MariaDB
		resolve_column: ``callable(source_column_name: str) -> str`` returning
		                the Frappe fieldname for that source column

	Returns:
		SQL string, or "" if the expression is empty/unsupported in translator.
	"""
	if not generation_expression or not str(generation_expression).strip():
		return ""

	expr = _strip_wrapping_parens(str(generation_expression).strip())

	try:
		out = _translate_expr_frappe_sql(expr, resolve_column)
	except Exception:
		return ""

	if not (out and str(out).strip()):
		return ""
	return out
