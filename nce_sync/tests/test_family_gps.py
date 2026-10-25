# Copyright (c) 2026, Oliver Reid and contributors
# For license information, please see license.txt

"""Unit tests for family GPS population and Family GPS family_id link setup.

code-index:begin
{
 "purpose": "Unit tests for populate_family_gps permissions, family_id link ensure, geocode batching, and enqueue path.",
 "read_when": "Changing family_gps population, geocode behaviour, or Family GPS DocType family_id field.",
 "gotchas": [],
 "keywords": ["family_gps", "populate_family_gps", "geocode", "unittest", "Family GPS"],
 "format": 1
}
code-index:end
"""

import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

_APP_ROOT = Path(__file__).resolve().parent.parent


class PermissionError(Exception):
	pass


class ValidationError(Exception):
	pass


def _load_family_gps():
	spec = importlib.util.spec_from_file_location(
		"nce_sync.utils.family_gps",
		_APP_ROOT / "utils" / "family_gps.py",
	)
	mod = importlib.util.module_from_spec(spec)
	sys.modules["nce_sync.utils.family_gps"] = mod
	spec.loader.exec_module(mod)
	return mod


# Stub frappe before loading family_gps (no bench required).
if "frappe" not in sys.modules:
	_fr = ModuleType("frappe")
	_fr.PermissionError = PermissionError
	_fr.ValidationError = ValidationError
	_fr.session = SimpleNamespace(user="test@example.com")
	_fr.flags = SimpleNamespace()
	_fr.local = SimpleNamespace()
	_fr.log_error = MagicMock()
	_fr._ = lambda x: x
	_fr.cache = MagicMock(return_value=SimpleNamespace(get_value=MagicMock(), set_value=MagicMock()))
	_fr.get_all = MagicMock(return_value=[])
	_fr.get_doc = MagicMock()
	_fr.get_meta = MagicMock()
	_fr.has_permission = MagicMock()
	_fr.db = SimpleNamespace(
		exists=MagicMock(return_value=True),
		sql=MagicMock(return_value=[]),
		commit=MagicMock(),
		set_value=MagicMock(),
	)
	_fr.clear_cache = MagicMock()
	_fr.enqueue = MagicMock()
	_fr.publish_realtime = MagicMock()

	def _throw(msg, exc=ValidationError):
		raise exc(msg)

	_fr.throw = _throw
	_fr.whitelist = lambda *args, **kwargs: (lambda fn: fn)
	sys.modules["frappe"] = _fr

	_fr_utils = ModuleType("frappe.utils")
	_fr_utils.cint = int
	sys.modules["frappe.utils"] = _fr_utils

if "nce_sync" not in sys.modules:
	sys.modules["nce_sync"] = ModuleType("nce_sync")
	sys.modules["nce_sync.utils"] = ModuleType("nce_sync.utils")

if "requests" not in sys.modules:
	_requests = ModuleType("requests")
	_requests.get = MagicMock()
	sys.modules["requests"] = _requests

family_gps = _load_family_gps()
frappe = sys.modules["frappe"]


def _mock_geocode_response(lat: float = 40.0, lng: float = -75.0):
	resp = MagicMock()
	resp.raise_for_status = MagicMock()
	resp.json.return_value = {
		"status": "OK",
		"results": [{"geometry": {"location": {"lat": lat, "lng": lng}}}],
	}
	return resp


def _link_field_meta():
	return SimpleNamespace(fieldtype="Link", options="Families")


def _data_field_meta():
	return SimpleNamespace(fieldtype="Data", options="")


class TestPopulateFamilyGpsPermissions(unittest.TestCase):
	def setUp(self):
		frappe.session.user = "test@example.com"
		frappe.has_permission = MagicMock()
		frappe.db.exists = MagicMock(return_value=True)

	def test_guest_is_rejected(self):
		frappe.session.user = "Guest"
		with self.assertRaises(PermissionError):
			family_gps.populate_family_gps()

	def test_missing_families_read_permission_is_rejected(self):
		def _has_permission(doctype, ptype, throw=False):
			if doctype == "Families" and ptype == "read":
				if throw:
					raise PermissionError("no read")
				return False
			return True

		frappe.has_permission = MagicMock(side_effect=_has_permission)
		with self.assertRaises(PermissionError):
			family_gps.populate_family_gps()


class TestEnsureFamilyGpsLink(unittest.TestCase):
	def setUp(self):
		frappe.clear_cache = MagicMock()
		frappe.db.commit = MagicMock()

	def test_family_id_already_link_doctype_save_not_called(self):
		frappe.db.exists = MagicMock(return_value=True)
		frappe.get_meta = MagicMock(
			return_value=SimpleNamespace(get_field=MagicMock(return_value=_link_field_meta()))
		)
		with patch.object(frappe, "get_doc") as mock_get_doc:
			changed = family_gps.ensure_family_gps_link()
		self.assertFalse(changed)
		mock_get_doc.assert_not_called()

	def test_family_id_data_converted_to_link_and_saved(self):
		field_row = SimpleNamespace(fieldname="family_id", fieldtype="Data", options="")
		dt_doc = MagicMock()
		dt_doc.fields = [field_row]
		frappe.db.exists = MagicMock(return_value=True)
		frappe.get_meta = MagicMock(
			return_value=SimpleNamespace(get_field=MagicMock(return_value=_data_field_meta()))
		)
		frappe.get_doc = MagicMock(return_value=dt_doc)

		changed = family_gps.ensure_family_gps_link()

		self.assertTrue(changed)
		self.assertEqual(field_row.fieldtype, "Link")
		self.assertEqual(field_row.options, "Families")
		dt_doc.save.assert_called_once_with(ignore_permissions=True)
		frappe.db.commit.assert_called_once()

	def test_family_id_missing_field_appended_as_link(self):
		dt_doc = MagicMock()
		dt_doc.fields = []
		frappe.db.exists = MagicMock(return_value=True)
		frappe.get_meta = MagicMock(
			return_value=SimpleNamespace(get_field=MagicMock(return_value=None))
		)
		frappe.get_doc = MagicMock(return_value=dt_doc)

		changed = family_gps.ensure_family_gps_link()

		self.assertTrue(changed)
		dt_doc.append.assert_called_once()
		args = dt_doc.append.call_args[0]
		self.assertEqual(args[0], "fields")
		self.assertEqual(args[1]["fieldtype"], "Link")
		self.assertEqual(args[1]["options"], "Families")
		dt_doc.save.assert_called_once_with(ignore_permissions=True)


class TestPopulateFamilyGpsRun(unittest.TestCase):
	def setUp(self):
		frappe.session.user = "test@example.com"
		frappe.has_permission = MagicMock()
		frappe.db.exists = MagicMock(return_value=True)
		frappe.clear_cache = MagicMock()
		frappe.db.commit = MagicMock()
		frappe.db.set_value = MagicMock()
		frappe.enqueue = MagicMock()
		frappe.get_meta = MagicMock(
			return_value=SimpleNamespace(get_field=MagicMock(return_value=_link_field_meta()))
		)
		connector = MagicMock()
		connector.get_password = MagicMock(return_value="test-key")
		frappe.get_doc = MagicMock(return_value=connector)

	def test_populate_limit_50_is_passed_to_sql(self):
		frappe.db.sql = MagicMock(return_value=[])
		with patch.object(family_gps, "_geocode_addresses", return_value={}):
			family_gps._populate_family_gps(limit=50)
		args, kwargs = frappe.db.sql.call_args
		self.assertIn("LIMIT %s", args[0])
		self.assertEqual(args[1], (50,))
		self.assertTrue(kwargs.get("as_dict"))

	def test_normal_populate_enqueues_limit_50(self):
		frappe.db.sql = MagicMock(return_value=[])
		frappe.enqueue = MagicMock()
		out = family_gps.populate_family_gps(all_rows=0, batch_id="batch-50")
		self.assertEqual(out, {"queued": True, "batch_id": "batch-50"})
		frappe.db.sql.assert_not_called()
		frappe.enqueue.assert_called_once()
		call_kw = frappe.enqueue.call_args[1]
		self.assertEqual(call_kw["queue"], "long")
		self.assertEqual(call_kw["timeout"], 3600)
		self.assertEqual(call_kw["user"], "test@example.com")
		self.assertEqual(call_kw["limit"], 50)
		self.assertEqual(call_kw["batch_id"], "batch-50")
		self.assertIs(frappe.enqueue.call_args[0][0], family_gps.family_gps_populate_job)

	def test_all_rows_enqueues_without_limit(self):
		frappe.db.sql = MagicMock(return_value=[])
		frappe.enqueue = MagicMock()
		frappe.generate_hash = MagicMock(return_value="generated")
		out = family_gps.populate_family_gps(all_rows=1)
		self.assertEqual(out, {"queued": True, "batch_id": "generated"})
		frappe.db.sql.assert_not_called()
		frappe.enqueue.assert_called_once()
		call_kw = frappe.enqueue.call_args[1]
		self.assertEqual(call_kw["limit"], None)
		self.assertEqual(call_kw["batch_id"], "generated")
		self.assertIs(frappe.enqueue.call_args[0][0], family_gps.family_gps_populate_job)

	@patch.object(family_gps.requests, "get", side_effect=lambda *a, **k: _mock_geocode_response(41.0, -74.0))
	def test_same_address_geocoded_once_two_families_share_coords(self, mock_get):
		rows = [
			{
				"name": "F1",
				"address_1": "1 Shared Rd",
				"address_2": "",
				"city": "Town",
				"state": "ST",
				"postcode": "12345",
				"gps_name": None,
			},
			{
				"name": "F2",
				"address_1": "1 Shared Rd",
				"address_2": "",
				"city": "Town",
				"state": "ST",
				"postcode": "12345",
				"gps_name": None,
			},
		]
		frappe.db.sql = MagicMock(return_value=rows)
		inserted = []

		connector = MagicMock()
		connector.get_password = MagicMock(return_value="test-key")

		def _get_doc(*args, **kwargs):
			if args and args[0] == "API Connector":
				return connector
			data = args[0] if args else kwargs
			if isinstance(data, dict) and data.get("doctype") == "Family GPS":
				doc = MagicMock()
				doc.insert = MagicMock(side_effect=lambda **kw: inserted.append(data))
				return doc
			return MagicMock()

		frappe.get_doc = MagicMock(side_effect=_get_doc)

		out = family_gps._populate_family_gps(limit=50)

		self.assertEqual(mock_get.call_count, 1)
		self.assertEqual(out["created"], 2)
		self.assertEqual(len(inserted), 2)
		self.assertEqual(inserted[0]["latitude"], 41.0)
		self.assertEqual(inserted[1]["latitude"], 41.0)

	@patch.object(family_gps.requests, "get", side_effect=lambda *a, **k: _mock_geocode_response(39.0, -76.0))
	def test_successful_geocode_without_gps_name_inserts(self, _mock_get):
		rows = [
			{
				"name": "F99",
				"address_1": "9 Elm",
				"address_2": "",
				"city": "City",
				"state": "PA",
				"postcode": "19000",
				"gps_name": None,
			},
		]
		frappe.db.sql = MagicMock(return_value=rows)
		doc = MagicMock()
		connector = MagicMock(get_password=MagicMock(return_value="test-key"))
		frappe.get_doc = MagicMock(
			side_effect=lambda *a, **k: connector
			if a and a[0] == "API Connector"
			else doc
		)

		out = family_gps._populate_family_gps(limit=50)

		self.assertEqual(out["created"], 1)
		self.assertEqual(out["updated"], 0)
		doc.insert.assert_called_once_with(ignore_permissions=True)
		frappe.db.set_value.assert_not_called()

	@patch.object(family_gps.requests, "get", side_effect=lambda *a, **k: _mock_geocode_response(38.0, -77.0))
	def test_successful_geocode_with_gps_name_updates_no_insert(self, _mock_get):
		rows = [
			{
				"name": "F88",
				"address_1": "88 Oak",
				"address_2": "",
				"city": "City",
				"state": "PA",
				"postcode": "19001",
				"gps_name": "GPS-88",
			},
		]
		frappe.db.sql = MagicMock(return_value=rows)
		insert_doc = MagicMock()
		connector = MagicMock(get_password=MagicMock(return_value="test-key"))

		def _get_doc(*args, **kwargs):
			if args and args[0] == "API Connector":
				return connector
			if isinstance(args[0], dict) and args[0].get("doctype") == "Family GPS":
				return insert_doc
			return MagicMock()

		frappe.get_doc = MagicMock(side_effect=_get_doc)

		out = family_gps._populate_family_gps(limit=50)

		self.assertEqual(out["updated"], 1)
		self.assertEqual(out["created"], 0)
		frappe.db.set_value.assert_called_once_with(
			"Family GPS",
			"GPS-88",
			{"latitude": 38.0, "longitude": -77.0, "error_message": ""},
			update_modified=True,
		)
		insert_doc.insert.assert_not_called()

	@patch.object(family_gps.requests, "get")
	def test_geocode_failure_inserts_error_message(self, mock_get):
		resp = MagicMock()
		resp.raise_for_status = MagicMock()
		resp.json.return_value = {"status": "ZERO_RESULTS", "results": []}
		mock_get.return_value = resp
		rows = [
			{
				"name": "F77",
				"address_1": "77 Pine",
				"address_2": "",
				"city": "City",
				"state": "PA",
				"postcode": "19002",
				"gps_name": None,
			},
		]
		frappe.db.sql = MagicMock(return_value=rows)
		insert_doc = MagicMock()
		connector = MagicMock(get_password=MagicMock(return_value="test-key"))

		def _get_doc(*args, **kwargs):
			if args and args[0] == "API Connector":
				return connector
			if isinstance(args[0], dict) and args[0].get("doctype") == "Family GPS":
				return insert_doc
			return MagicMock()

		frappe.get_doc = MagicMock(side_effect=_get_doc)

		out = family_gps._populate_family_gps(limit=50)

		self.assertEqual(out["geocode_failed"], 1)
		self.assertEqual(out["created"], 0)
		self.assertEqual(out["updated"], 0)
		gps_calls = [
			c
			for c in frappe.get_doc.call_args_list
			if c[0] and isinstance(c[0][0], dict) and c[0][0].get("doctype") == "Family GPS"
		]
		self.assertEqual(len(gps_calls), 1)
		payload = gps_calls[0][0][0]
		self.assertEqual(payload["family_id"], "F77")
		self.assertEqual(payload["error_message"], "ZERO_RESULTS")
		self.assertNotIn("latitude", payload)
		self.assertNotIn("longitude", payload)
		insert_doc.insert.assert_called_once_with(ignore_permissions=True)
		frappe.db.set_value.assert_not_called()

	@patch.object(family_gps.requests, "get")
	def test_geocode_failure_updates_existing_row(self, mock_get):
		resp = MagicMock()
		resp.raise_for_status = MagicMock()
		resp.json.return_value = {"status": "ZERO_RESULTS", "results": []}
		mock_get.return_value = resp
		rows = [
			{
				"name": "F77",
				"address_1": "77 Pine",
				"address_2": "",
				"city": "City",
				"state": "PA",
				"postcode": "19002",
				"gps_name": "GPS-77",
			},
		]
		frappe.db.sql = MagicMock(return_value=rows)
		insert_doc = MagicMock()
		connector = MagicMock(get_password=MagicMock(return_value="test-key"))

		def _get_doc(*args, **kwargs):
			if args and args[0] == "API Connector":
				return connector
			if isinstance(args[0], dict) and args[0].get("doctype") == "Family GPS":
				return insert_doc
			return MagicMock()

		frappe.get_doc = MagicMock(side_effect=_get_doc)

		out = family_gps._populate_family_gps(limit=50)

		self.assertEqual(out["geocode_failed"], 1)
		self.assertEqual(out["created"], 0)
		self.assertEqual(out["updated"], 0)
		frappe.db.set_value.assert_called_once_with(
			"Family GPS",
			"GPS-77",
			{"error_message": "ZERO_RESULTS"},
			update_modified=True,
		)
		insert_doc.insert.assert_not_called()

	@patch.object(
		family_gps.requests,
		"get",
		side_effect=RuntimeError(
			"https://maps.googleapis.com/maps/api/geocode/json?key=SECRET-KEY"
		),
	)
	def test_geocode_exception_does_not_store_request_url(self, _mock_get):
		rows = [
			{
				"name": "F77",
				"address_1": "77 Pine",
				"address_2": "",
				"city": "City",
				"state": "PA",
				"postcode": "19002",
				"gps_name": None,
			},
		]
		frappe.db.sql = MagicMock(return_value=rows)
		insert_doc = MagicMock()
		connector = MagicMock(get_password=MagicMock(return_value="test-key"))
		captured_payload = {}

		def _get_doc(*args, **kwargs):
			if args and args[0] == "API Connector":
				return connector
			if isinstance(args[0], dict) and args[0].get("doctype") == "Family GPS":
				captured_payload.update(args[0])
				return insert_doc
			return MagicMock()

		frappe.get_doc = MagicMock(side_effect=_get_doc)

		family_gps._populate_family_gps(limit=50)

		self.assertEqual(captured_payload.get("error_message"), "Geocode request failed")
		self.assertNotIn("SECRET-KEY", captured_payload.get("error_message", ""))

	@patch.object(family_gps.requests, "get")
	def test_google_error_message_is_stored(self, mock_get):
		resp = MagicMock()
		resp.raise_for_status = MagicMock()
		resp.json.return_value = {
			"status": "OVER_QUERY_LIMIT",
			"error_message": "daily quota exceeded",
			"results": [],
		}
		mock_get.return_value = resp
		rows = [
			{
				"name": "F77",
				"address_1": "77 Pine",
				"address_2": "",
				"city": "City",
				"state": "PA",
				"postcode": "19002",
				"gps_name": None,
			},
		]
		frappe.db.sql = MagicMock(return_value=rows)
		insert_doc = MagicMock()
		connector = MagicMock(get_password=MagicMock(return_value="test-key"))
		captured_payload = {}

		def _get_doc(*args, **kwargs):
			if args and args[0] == "API Connector":
				return connector
			if isinstance(args[0], dict) and args[0].get("doctype") == "Family GPS":
				captured_payload.update(args[0])
				return insert_doc
			return MagicMock()

		frappe.get_doc = MagicMock(side_effect=_get_doc)

		family_gps._populate_family_gps(limit=50)

		self.assertEqual(
			captured_payload.get("error_message"),
			"OVER_QUERY_LIMIT: daily quota exceeded",
		)

	def test_blank_api_key_raises_validation_error_without_secret(self):
		connector = MagicMock()
		connector.get_password = MagicMock(return_value="")
		frappe.get_doc = MagicMock(return_value=connector)
		frappe.db.sql = MagicMock(return_value=[])

		with self.assertRaises(ValidationError) as ctx:
			family_gps.populate_family_gps(all_rows=0)

		self.assertEqual(str(ctx.exception), "Google Maps API Connector has no API key.")
		connector.get_password.assert_called_once_with("api_key", raise_exception=False)


if __name__ == "__main__":
	unittest.main()
