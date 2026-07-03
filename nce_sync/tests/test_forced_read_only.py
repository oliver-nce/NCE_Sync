"""Tests for system-forced read-only column resolution during mirror/remap."""

from __future__ import annotations

import unittest

from nce_sync.utils.schema_mirror import (
	_forced_read_only_wp_columns,
	_resolve_read_only_fieldnames,
)


def _schema(*columns):
	return {
		"columns": [
			{
				"COLUMN_NAME": name,
				"EXTRA": extra,
			}
			for name, extra in columns
		],
	}


class TestForcedReadOnly(unittest.TestCase):
	def test_virtual_and_auto_increment_always_forced(self):
		schema = _schema(
			("id", "AUTO_INCREMENT"),
			("full_name", "VIRTUAL GENERATED ALWAYS AS (concat(a,b)) STORED"),
			("note", ""),
		)
		forced = _forced_read_only_wp_columns(schema)
		self.assertEqual(forced, {"id", "full_name"})

	def test_resolve_merges_user_selection_with_forced(self):
		schema = _schema(
			("id", "AUTO_INCREMENT"),
			("note", ""),
		)
		fieldnames = _resolve_read_only_fieldnames(
			"note",
			{},
			schema,
		)
		self.assertIn("note", fieldnames)
		self.assertIn("id", fieldnames)

	def test_name_and_timestamps_forced(self):
		schema = _schema(("a_pk", ""), ("updated_at", ""))
		forced = _forced_read_only_wp_columns(
			schema,
			name_field_column="a_pk",
			modified_ts_field="updated_at",
		)
		self.assertEqual(forced, {"a_pk", "updated_at"})

	def test_auto_generated_marked_column_forced(self):
		schema = _schema(("serial_no", ""))
		forced = _forced_read_only_wp_columns(schema, auto_generated_columns="serial_no")
		self.assertEqual(forced, {"serial_no"})


if __name__ == "__main__":
	unittest.main()
