# Copyright (c) 2026, Oliver Reid and contributors
# For license information, please see license.txt

"""Unit tests for live_sync delete propagation (on_trash → WordPress)."""

import importlib.util
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

_APP_ROOT = Path(__file__).resolve().parent.parent


def _load_module(name: str, rel_path: str):
	spec = importlib.util.spec_from_file_location(name, _APP_ROOT / rel_path)
	mod = importlib.util.module_from_spec(spec)
	sys.modules[name] = mod
	spec.loader.exec_module(mod)
	return mod


# Stub frappe and minimal nce_sync deps before loading live_sync (no bench required).
if "frappe" not in sys.modules:
	_fr = ModuleType("frappe")
	_fr.flags = SimpleNamespace()
	_fr.local = SimpleNamespace()
	_fr.log_error = MagicMock()
	_fr._ = lambda x: x
	_fr.cache = MagicMock(return_value=SimpleNamespace(get_value=MagicMock(return_value=None), set_value=MagicMock()))
	_fr.get_all = MagicMock(return_value=[])
	_fr.get_doc = MagicMock()
	_fr.get_single = MagicMock()
	sys.modules["frappe"] = _fr
	_fr_utils = ModuleType("frappe.utils")
	_fr_utils.cint = int
	sys.modules["frappe.utils"] = _fr_utils

for _stub_name in (
	"nce_sync.utils.constants",
	"nce_sync.utils.column_mapper",
	"nce_sync.utils.connections",
	"nce_sync.utils.write_back_dispatch",
):
	if _stub_name not in sys.modules:
		sys.modules[_stub_name] = ModuleType(_stub_name)

sys.modules["nce_sync.utils.constants"].CACHE_KEY_LISTEN_TABLES = "nce_sync:listen_tables"
sys.modules["nce_sync.utils.column_mapper"].build_wp_row = MagicMock()
sys.modules["nce_sync.utils.column_mapper"].load_column_mapping = MagicMock()
sys.modules["nce_sync.utils.connections"].wp_connection = MagicMock()
sys.modules["nce_sync.utils.write_back_dispatch"].run_write_back_for_doc = MagicMock()

live_sync = _load_module("nce_sync.utils.live_sync", "utils/live_sync.py")
_hooks_mod = _load_module("nce_sync_app_hooks", "hooks.py")
doc_events = _hooks_mod.doc_events

frappe = sys.modules["frappe"]


class TestOnTrashHookRegistered(unittest.TestCase):
	def test_hook_resolves_to_on_record_delete(self):
		self.assertEqual(
			doc_events["*"]["on_trash"],
			"nce_sync.utils.live_sync.on_record_delete",
		)


class TestDeleteRecordFromWp(unittest.TestCase):
	def _mock_wp_table_doc(self, table_name="wp_events", name_field_column="ID"):
		return SimpleNamespace(
			table_name=table_name,
			name_field_column=name_field_column,
		)

	def test_issues_delete_sql_and_commits(self):
		cursor = MagicMock()
		conn = MagicMock()
		conn.cursor.return_value = cursor
		wp_table_doc = self._mock_wp_table_doc()
		wp_conn_doc = SimpleNamespace()

		@contextmanager
		def _conn_ctx(_doc):
			yield conn

		with (
			patch.object(frappe, "get_doc", return_value=wp_table_doc),
			patch.object(frappe, "get_single", return_value=wp_conn_doc),
			patch.object(live_sync, "wp_connection", side_effect=_conn_ctx),
		):
			result = live_sync.delete_record_from_wp("WP Events", "Event Session", "42")

		self.assertTrue(result)
		cursor.execute.assert_called_once_with(
			"DELETE FROM `wp_events` WHERE `ID` = %s",
			["42"],
		)
		conn.commit.assert_called_once()
		cursor.close.assert_called_once()

	def test_temp_name_skips_sql(self):
		with patch.object(live_sync, "wp_connection") as mock_wp_conn:
			result = live_sync.delete_record_from_wp("WP Events", "Event Session", "-5")

		self.assertTrue(result)
		mock_wp_conn.assert_not_called()

	def test_missing_config_returns_false_and_logs(self):
		wp_table_doc = SimpleNamespace(table_name=None, name_field_column="ID")

		with (
			patch.object(frappe, "get_doc", return_value=wp_table_doc),
			patch.object(frappe, "log_error") as mock_log_error,
			patch.object(live_sync, "wp_connection") as mock_wp_conn,
		):
			result = live_sync.delete_record_from_wp("WP Events", "Event Session", "42")

		self.assertFalse(result)
		mock_log_error.assert_called_once()
		mock_wp_conn.assert_not_called()

	def test_sql_error_raises_after_rollback(self):
		cursor = MagicMock()
		cursor.execute.side_effect = RuntimeError("connection lost")
		wp_table_doc = self._mock_wp_table_doc()
		wp_conn_doc = SimpleNamespace()
		conn = MagicMock()
		conn.cursor.return_value = cursor

		@contextmanager
		def _conn_ctx(_doc):
			yield conn

		with (
			patch.object(frappe, "get_doc", return_value=wp_table_doc),
			patch.object(frappe, "get_single", return_value=wp_conn_doc),
			patch.object(live_sync, "wp_connection", side_effect=_conn_ctx),
			patch.object(frappe, "log_error"),
		):
			with self.assertRaises(RuntimeError):
				live_sync.delete_record_from_wp("WP Events", "Event Session", "42")

		conn.rollback.assert_called_once()
		cursor.close.assert_called_once()


class TestOnRecordDelete(unittest.TestCase):
	def _doc(self, doctype="Event Session", name="42"):
		return SimpleNamespace(doctype=doctype, name=name)

	@patch.object(live_sync, "delete_record_from_wp")
	@patch.object(live_sync, "_get_listen_map", return_value={"Event Session": "WP Events"})
	def test_calls_delete_when_in_listen_map(self, _mock_map, mock_delete):
		doc = self._doc()
		live_sync.on_record_delete(doc, "on_trash")
		mock_delete.assert_called_once_with("WP Events", "Event Session", "42")

	@patch.object(live_sync, "delete_record_from_wp")
	@patch.object(live_sync, "_get_listen_map", return_value={})
	def test_skips_when_not_in_listen_map(self, _mock_map, mock_delete):
		doc = self._doc()
		live_sync.on_record_delete(doc, "on_trash")
		mock_delete.assert_not_called()

	@patch.object(live_sync, "delete_record_from_wp")
	@patch.object(live_sync, "_get_listen_map", return_value={"Event Session": "WP Events"})
	def test_skips_when_in_sync(self, _mock_map, mock_delete):
		doc = self._doc()
		frappe.flags.in_sync = True
		try:
			live_sync.on_record_delete(doc, "on_trash")
		finally:
			frappe.flags.in_sync = False
		mock_delete.assert_not_called()

	@patch.object(live_sync, "delete_record_from_wp")
	@patch.object(live_sync, "_get_listen_map", return_value={"Event Session": "WP Events"})
	def test_skips_when_doctype_syncing(self, _mock_map, mock_delete):
		sync_gate = ModuleType("nce_sync.utils.sync_gate")
		sync_gate.is_doctype_syncing = MagicMock(return_value=True)
		with patch.dict(sys.modules, {"nce_sync.utils.sync_gate": sync_gate}):
			live_sync.on_record_delete(self._doc(), "on_trash")
		mock_delete.assert_not_called()

	@patch.object(live_sync, "delete_record_from_wp")
	@patch.object(live_sync, "_get_listen_map", return_value={"Event Session": "WP Events"})
	def test_skips_temp_name(self, _mock_map, mock_delete):
		doc = self._doc(name="-3")
		live_sync.on_record_delete(doc, "on_trash")
		mock_delete.assert_not_called()

	@patch.object(live_sync, "delete_record_from_wp", side_effect=RuntimeError("wp down"))
	@patch.object(live_sync, "_get_listen_map", return_value={"Event Session": "WP Events"})
	def test_propagates_delete_failure(self, _mock_map, _mock_delete):
		doc = self._doc()
		with self.assertRaises(RuntimeError):
			live_sync.on_record_delete(doc, "on_trash")
