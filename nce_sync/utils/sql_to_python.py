# Copyright (c) 2026, Oliver Reid and contributors
# For license information, please see license.txt

"""
Translate MySQL/MariaDB GENERATION_EXPRESSION SQL into Frappe virtual-field
Python expressions.

Frappe evaluates the ``options`` string of an ``is_virtual=1`` DocField using
``frappe.utils.safe_eval`` with ``doc`` in scope, so the output must be a
valid Python expression that references columns as ``doc.fieldname``.

IMPORTANT: Frappe's safe_eval sandbox (WHITELISTED_SAFE_EVAL_GLOBALS) restricts
available names to: ``int``, ``float``, ``round``, attribute access (``doc.x``),
subscript access (``x[0]``), Python keywords (``None``, ``True``, ``False``),
operators (``or``, ``and``, ``not``, ``if``/``else``, ``+``, ``-``, ``*``, ``/``),
string/object methods (``.upper()``, ``.join()``), and list literals (``[a, b]``).
Functions like ``str()``, ``cstr()``, ``getattr()``, ``dict()``, ``list()``,
``len()``, ``abs()`` are NOT available.
Use ``(x or '')`` for None-safe string coercion and ``.attr`` access directly.

The translator handles the most common SQL patterns found in WordPress schemas.
Unsupported expressions fall back to an empty string (the field will exist but
show blank until a custom expression is provided).
"""

import re

from nce_sync.utils.schema_mirror import resolve_fieldname


# Helper: wrap an expression so it's safe for string operations (None → '')
def _safe_str(expr):
	"""Wrap expr so None becomes '' — safe for concatenation and string methods."""
	if _is_string_literal(expr):
		return expr
	return f"({expr} or '')"


# ---------------------------------------------------------------------------
# SQL function → Python expression mapping
# ---------------------------------------------------------------------------

def _translate_concat(args_str, _resolve):
	"""CONCAT(a, b, ...) → (doc.a or '') + ' ' + (doc.b or '') + ..."""
	parts = _split_args(args_str)
	py_parts = [_translate_expr(p.strip(), _resolve) for p in parts]
	return " + ".join(_safe_str(p) for p in py_parts)


def _translate_concat_ws(args_str, _resolve):
	"""CONCAT_WS(sep, a, b, ...) → sep.join([(doc.a or ''), ...])"""
	parts = _split_args(args_str)
	if len(parts) < 2:
		return "''"
	sep = parts[0].strip()
	py_parts = [_translate_expr(p.strip(), _resolve) for p in parts[1:]]
	items = ", ".join(_safe_str(p) for p in py_parts)
	return f"{sep}.join([{items}])"


def _translate_ifnull(args_str, _resolve):
	"""IFNULL(a, b) / COALESCE(a, b) → (doc.a if doc.a is not None else b)"""
	parts = _split_args(args_str)
	if len(parts) < 2:
		return "''"
	a = _translate_expr(parts[0].strip(), _resolve)
	b = _translate_expr(parts[1].strip(), _resolve)
	return f"({a} if {a} is not None else {b})"


def _translate_upper(args_str, _resolve):
	"""UPPER(a) → (doc.a or '').upper()"""
	inner = _translate_expr(args_str.strip(), _resolve)
	return f"{_safe_str(inner)}.upper()"


def _translate_lower(args_str, _resolve):
	"""LOWER(a) → (doc.a or '').lower()"""
	inner = _translate_expr(args_str.strip(), _resolve)
	return f"{_safe_str(inner)}.lower()"


def _translate_trim(args_str, _resolve):
	"""TRIM(a) → (doc.a or '').strip()"""
	inner = _translate_expr(args_str.strip(), _resolve)
	return f"{_safe_str(inner)}.strip()"


def _translate_left(args_str, _resolve):
	"""LEFT(a, n) → (doc.a or '')[:n]"""
	parts = _split_args(args_str)
	if len(parts) < 2:
		return "''"
	a = _translate_expr(parts[0].strip(), _resolve)
	n = parts[1].strip()
	return f"{_safe_str(a)}[:{n}]"


def _translate_right(args_str, _resolve):
	"""RIGHT(a, n) → (doc.a or '')[-n:]"""
	parts = _split_args(args_str)
	if len(parts) < 2:
		return "''"
	a = _translate_expr(parts[0].strip(), _resolve)
	n = parts[1].strip()
	return f"{_safe_str(a)}[-{n}:]"


def _translate_year(args_str, _resolve):
	"""YEAR(a) → (doc.a.year if doc.a else None)"""
	inner = _translate_expr(args_str.strip(), _resolve)
	return f"({inner}.year if {inner} else None)"


def _translate_month(args_str, _resolve):
	"""MONTH(a) → (doc.a.month if doc.a else None)"""
	inner = _translate_expr(args_str.strip(), _resolve)
	return f"({inner}.month if {inner} else None)"


def _translate_round(args_str, _resolve):
	"""ROUND(a, n) → round(doc.a or 0, n)"""
	parts = _split_args(args_str)
	a = _translate_expr(parts[0].strip(), _resolve)
	if len(parts) >= 2:
		n = parts[1].strip()
		return f"round({a} or 0, {n})"
	return f"round({a} or 0)"


# Map of SQL function names (uppercase) → translator functions
_SQL_FUNCTIONS = {
	"CONCAT": _translate_concat,
	"CONCAT_WS": _translate_concat_ws,
	"IFNULL": _translate_ifnull,
	"COALESCE": _translate_ifnull,
	"ISNULL": _translate_ifnull,
	"UPPER": _translate_upper,
	"UCASE": _translate_upper,
	"LOWER": _translate_lower,
	"LCASE": _translate_lower,
	"TRIM": _translate_trim,
	"LEFT": _translate_left,
	"RIGHT": _translate_right,
	"YEAR": _translate_year,
	"MONTH": _translate_month,
	"ROUND": _translate_round,
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _split_args(args_str):
	"""
	Split a comma-separated argument string, respecting nested parentheses
	and quoted strings.

	>>> _split_args("a, 'hello, world', CONCAT(b, c)")
	['a', "'hello, world'", 'CONCAT(b, c)']
	"""
	parts = []
	depth = 0
	current = []
	in_quote = None

	for ch in args_str:
		if ch in ("'", '"') and not in_quote:
			in_quote = ch
			current.append(ch)
		elif ch == in_quote:
			in_quote = None
			current.append(ch)
		elif in_quote:
			current.append(ch)
		elif ch == "(":
			depth += 1
			current.append(ch)
		elif ch == ")":
			depth -= 1
			current.append(ch)
		elif ch == "," and depth == 0:
			parts.append("".join(current))
			current = []
		else:
			current.append(ch)

	if current:
		parts.append("".join(current))
	return parts


def _is_string_literal(expr):
	"""Check if an expression is a quoted string literal."""
	s = expr.strip()
	return (s.startswith("'") and s.endswith("'")) or (s.startswith('"') and s.endswith('"'))


def _is_numeric_literal(expr):
	"""Check if an expression is a numeric literal."""
	try:
		float(expr.strip())
		return True
	except (ValueError, TypeError):
		return False


def _translate_expr(sql_expr, _resolve):
	"""
	Recursively translate a SQL expression fragment into Python.

	Handles:
	- String literals ('hello') → 'hello'
	- Numeric literals → as-is
	- Column references (bare identifiers or `backtick`) → doc.fieldname
	- Function calls → delegated to _SQL_FUNCTIONS
	- Simple arithmetic (+, -, *, /) → preserved with doc. references
	"""
	sql_expr = sql_expr.strip()
	if not sql_expr:
		return "''"

	# String literal — pass through
	if _is_string_literal(sql_expr):
		return sql_expr

	# Numeric literal — pass through
	if _is_numeric_literal(sql_expr):
		return sql_expr

	# NULL → None
	if sql_expr.upper() == "NULL":
		return "None"

	# Function call: FUNCNAME(...)
	func_match = re.match(r"^(\w+)\s*\((.+)\)$", sql_expr, re.DOTALL)
	if func_match:
		func_name = func_match.group(1).upper()
		args_str = func_match.group(2)
		if func_name in _SQL_FUNCTIONS:
			return _SQL_FUNCTIONS[func_name](args_str, _resolve)
		# Unknown function — fall through to unsupported
		return "''"

	# Arithmetic expression: look for +, -, *, / outside parentheses/quotes
	# Split on operators while preserving them
	arith_match = re.match(r"^(.+?)\s*([+\-*/])\s*(.+)$", sql_expr)
	if arith_match:
		left = _translate_expr(arith_match.group(1), _resolve)
		op = arith_match.group(2)
		right = _translate_expr(arith_match.group(3), _resolve)
		return f"({left} {op} {right})"

	# Backtick-quoted column reference: `column_name`
	backtick_match = re.match(r"^`(.+)`$", sql_expr)
	if backtick_match:
		col_name = backtick_match.group(1)
		fieldname = _resolve(col_name)
		return f"doc.{fieldname}"

	# Bare identifier — treat as column reference
	if re.match(r"^[a-zA-Z_]\w*$", sql_expr):
		fieldname = _resolve(sql_expr)
		return f"doc.{fieldname}"

	# Unrecognised — return empty string (safe fallback)
	return "''"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def sql_generation_to_python(generation_expression, label_overrides=None):
	"""
	Convert a MySQL/MariaDB GENERATION_EXPRESSION into a Python expression
	suitable for a Frappe virtual DocField's ``options``.

	Args:
		generation_expression: The raw SQL from information_schema.COLUMNS
		label_overrides: Optional dict of {column_name: label} passed through
		                 to resolve_fieldname for Frappe-safe names

	Returns:
		A Python expression string, or empty string if translation fails.
		The expression references columns as ``doc.<fieldname>``.

	Examples:
		>>> sql_generation_to_python("concat(`first_name`,' ',`last_name`)")
		"(doc.first_name or '') + ' ' + (doc.last_name or '')"

		>>> sql_generation_to_python("ifnull(`nickname`,`first_name`)")
		"(doc.nickname if doc.nickname is not None else doc.first_name)"
	"""
	if not generation_expression:
		return ""

	expr = generation_expression.strip()
	# MariaDB sometimes wraps the whole expression in outer parens
	while expr.startswith("(") and expr.endswith(")"):
		# Only strip if the parens are balanced (i.e. wrapping the whole expr)
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

	def _resolve(col_name):
		return resolve_fieldname(col_name, label_overrides)

	try:
		python_expr = _translate_expr(expr, _resolve)
	except Exception:
		return ""

	# Validate the generated expression won't cause a runtime TypeError.
	# When the SQL contains tokens the translator can't handle (INTERVAL, CASE
	# WHEN, DAY, _utf8mb4, etc.) those fall back to '', producing expressions
	# like  '' - ''  which crash at safe_eval time.  Reject if an empty string
	# literal appears as an operand of any arithmetic operator.
	if re.search(r"''\s*[-+*/]\s*''", python_expr):
		return ""

	return python_expr
