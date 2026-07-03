# Copyright (c) 2026, Oliver Reid and contributors

"""Unit tests for layout-preserving Remap helpers in schema_mirror."""

from types import SimpleNamespace

from nce_sync.utils.schema_mirror import (
	BREAK_TYPES,
	NEW_FIELDS_TAB_FIELDNAME,
	_get_new_fields_tab_insert_index,
	_prune_stale_fields_from_doctype_doc,
)


def _field(fieldname, fieldtype="Data", idx=1):
	return SimpleNamespace(fieldname=fieldname, fieldtype=fieldtype, idx=idx)


def _schema(*column_names):
	return {"columns": [{"COLUMN_NAME": name} for name in column_names]}


def test_prune_keeps_layout_breaks_and_expected_data_fields():
	doctype_doc = SimpleNamespace(
		fields=[
			_field("event_name", idx=1),
			_field("sku_tab", "Tab Break", idx=2),
			_field("sku", idx=3),
			_field("column_break_a", "Column Break", idx=4),
			_field("removed_col", idx=5),
		],
		title_field=None,
		show_title_field_in_link=0,
		search_fields=None,
	)

	removed = _prune_stale_fields_from_doctype_doc(
		doctype_doc, _schema("event_name", "sku"), label_overrides=None
	)

	assert removed == 1
	assert [f.fieldname for f in doctype_doc.fields] == [
		"event_name",
		"sku_tab",
		"sku",
		"column_break_a",
	]
	assert doctype_doc.fields[1].fieldtype == "Tab Break"
	assert doctype_doc.fields[0].idx == 1
	assert doctype_doc.fields[2].idx == 3


def test_new_fields_tab_insert_index_after_existing_tab_fields():
	fields = [
		_field("a", idx=1),
		_field(NEW_FIELDS_TAB_FIELDNAME, "Tab Break", idx=2),
		_field("old_new", idx=3),
		_field("other_tab", "Tab Break", idx=4),
	]
	assert _get_new_fields_tab_insert_index(fields) == 3


def test_new_fields_tab_insert_index_when_tab_missing():
	fields = [_field("a", idx=1)]
	assert _get_new_fields_tab_insert_index(fields) == 1


def test_break_types_include_tab_section_column():
	assert "Tab Break" in BREAK_TYPES
	assert "Section Break" in BREAK_TYPES
	assert "Column Break" in BREAK_TYPES
