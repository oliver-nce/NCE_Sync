# Copyright (c) 2026, Oliver Reid and contributors
# For license information, please see license.txt

"""Unit tests for derived (generated) column SQL mapping (no live DB)."""

import unittest

from nce_sync.utils.derived_column_sql import sql_generation_to_frappe_bare_sql
from nce_sync.utils.schema_mirror import _build_column_mapping


def _resolve_sample(c):
	"""Simulate Frappe renames: source columns → target fieldnames."""
	return {"post_year": "yr", "post_month": "mo", "wp_sku": "sku"}.get(c, c)


def _base_col(name, data_type, col_type, extra, gen_expr):
	return {
		"COLUMN_NAME": name,
		"DATA_TYPE": data_type,
		"CHARACTER_MAXIMUM_LENGTH": 100 if "varchar" in col_type else None,
		"NUMERIC_PRECISION": 10 if data_type == "int" else None,
		"NUMERIC_SCALE": 0 if data_type == "int" else None,
		"IS_NULLABLE": "YES",
		"COLUMN_DEFAULT": None,
		"EXTRA": extra,
		"COLUMN_TYPE": col_type,
		"GENERATION_EXPRESSION": gen_expr,
	}


class TestSqlGenerationFrappeSql(unittest.TestCase):
	def test_concat_rewrites_to_frappe_fieldnames(self):
		out = sql_generation_to_frappe_bare_sql(
			"concat(`post_year`, '-', lpad(`post_month`, 2, '0'))", _resolve_sample
		)
		self.assertIn("yr", out)
		self.assertIn("mo", out)
		self.assertIn("CONCAT", out.upper())
		self.assertIn("LPAD", out.upper())

	def test_year_month_style_expression(self):
		out = sql_generation_to_frappe_bare_sql("concat(`post_year`, `post_month`)", _resolve_sample)
		self.assertIn("yr", out)
		self.assertIn("mo", out)

	def test_empty_expression_returns_empty(self):
		self.assertEqual(sql_generation_to_frappe_bare_sql("", _resolve_sample), "")


class TestBuildColumnMappingDerived(unittest.TestCase):
	"""Mimics INFORMATION_SCHEMA rows for one base column + one generated column."""

	def test_emits_is_derived_and_sql_expression(self):
		schema = {
			"columns": [
				_base_col("raw_id", "int", "int(11)", "", None),
				_base_col(
					"generated_sku",
					"varchar",
					"varchar(100)",
					"VIRTUAL GENERATED",
					"concat(`raw_id`, '-', 'suffix')",
				),
			],
		}
		cm, _ = _build_column_mapping(schema, None, None, None, set(), None, set())
		self.assertIn("generated_sku", cm)
		entry = cm["generated_sku"]
		self.assertTrue(entry.get("is_derived"))
		self.assertTrue(entry.get("is_virtual"))
		sx = entry.get("sql_expression")
		self.assertIsNotNone(sx)
		self.assertIn("raw_id", sx)
		self.assertIn("CONCAT", sx.upper())
