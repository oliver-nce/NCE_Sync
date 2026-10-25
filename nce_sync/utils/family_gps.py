# Copyright (c) 2026, Oliver Reid and contributors
# For license information, please see license.txt

"""Populate Family GPS coordinates by geocoding family addresses via Google Maps.

code-index:begin
{
 "purpose": "Whitelisted populate_family_gps: ensure Family GPS family_id link, geocode family addresses, insert or update GPS rows.",
 "read_when": "Changing family GPS population, Family GPS DocType family_id field, or background geocode jobs.",
 "gotchas": [
  "Geocode failures still write a Family GPS row with error_message and no coordinates; the batch still completes.",
  "API key comes only from API Connector Google Maps api_key; never api_secret."
 ],
 "keywords": ["family_gps", "populate_family_gps", "geocode", "Google Maps", "Families", "Family GPS"],
 "format": 1
}
code-index:end
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

import frappe
import requests
from frappe.utils import cint

_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
_GEOCODE_TIMEOUT = 10

_FAMILIES_NEED_GPS_SQL = """
SELECT f.name, f.address_1, f.address_2, f.city, f.state, f.postcode,
       g.name AS gps_name
FROM `tabFamilies` f
LEFT JOIN `tabFamily GPS` g ON g.name = (
    SELECT g2.name FROM `tabFamily GPS` g2
    WHERE g2.family_id = f.name
    ORDER BY g2.creation DESC, g2.name DESC
    LIMIT 1
)
WHERE (
    g.name IS NULL
    OR (IFNULL(g.latitude, 0) = 0 AND IFNULL(g.longitude, 0) = 0)
)
AND (
    NULLIF(TRIM(IFNULL(f.address_1, '')), '') IS NOT NULL
    OR NULLIF(TRIM(IFNULL(f.address_2, '')), '') IS NOT NULL
    OR NULLIF(TRIM(IFNULL(f.city, '')), '') IS NOT NULL
    OR NULLIF(TRIM(IFNULL(f.state, '')), '') IS NOT NULL
    OR NULLIF(TRIM(IFNULL(f.postcode, '')), '') IS NOT NULL
)
ORDER BY f.modified DESC
"""


def ensure_family_gps_link() -> bool:
	"""Ensure Family GPS.family_id is a Link to Families. Returns True if meta was changed."""
	if not frappe.db.exists("DocType", "Family GPS"):
		frappe.throw("Family GPS DocType does not exist", frappe.ValidationError)

	frappe.clear_cache(doctype="Family GPS")
	meta = frappe.get_meta("Family GPS")
	field = meta.get_field("family_id")
	if field and field.fieldtype == "Link" and field.options == "Families":
		return False

	dt = frappe.get_doc("DocType", "Family GPS")
	if field:
		for row in dt.fields:
			if row.fieldname == "family_id":
				row.fieldtype = "Link"
				row.options = "Families"
				break
	else:
		dt.append(
			"fields",
			{
				"fieldname": "family_id",
				"fieldtype": "Link",
				"label": "Family Id",
				"options": "Families",
				"reqd": 1,
				"unique": 1,
				"in_list_view": 1,
			},
		)

	dt.save(ignore_permissions=True)
	frappe.db.commit()
	frappe.clear_cache(doctype="Family GPS")
	return True


def _family_address(row: dict[str, Any]) -> str:
	parts = [
		(row.get("address_1") or "").strip(),
		(row.get("address_2") or "").strip(),
		(row.get("city") or "").strip(),
		(row.get("state") or "").strip(),
		(row.get("postcode") or "").strip(),
	]
	return ", ".join(p for p in parts if p)


def _geocode_address(address: str, api_key: str) -> tuple[float, float] | str:
	try:
		resp = requests.get(
			_GEOCODE_URL,
			params={"address": address, "key": api_key},
			timeout=_GEOCODE_TIMEOUT,
		)
		resp.raise_for_status()
		data = resp.json()
	except Exception:
		return "Geocode request failed"
	status = data.get("status")
	if status != "OK":
		error_message = (data.get("error_message") or "").strip()
		if error_message:
			msg = f"{status}: {error_message}"
			return msg[:500]
		return status if status else "Geocode failed"
	results = data.get("results") or []
	if not results:
		return "No coordinates returned"
	loc = (results[0].get("geometry") or {}).get("location") or {}
	lat, lng = loc.get("lat"), loc.get("lng")
	if lat is None or lng is None:
		return "No coordinates returned"
	try:
		return float(lat), float(lng)
	except (TypeError, ValueError):
		return "No coordinates returned"


def _geocode_addresses(addresses: list[str], api_key: str) -> dict[str, tuple[float, float] | str]:
	unique = list(dict.fromkeys(a for a in addresses if a))
	if not unique:
		return {}
	workers = min(32, len(unique))
	found: dict[str, tuple[float, float] | str] = {}
	with ThreadPoolExecutor(max_workers=workers) as pool:
		futures = {pool.submit(_geocode_address, address, api_key): address for address in unique}
		for future, address in futures.items():
			found[address] = future.result()
	return found


def _google_maps_api_key() -> str:
	if not frappe.db.exists("API Connector", "Google Maps"):
		frappe.throw(
			"API Connector Google Maps was not found.",
			frappe.ValidationError,
		)
	connector = frappe.get_doc("API Connector", "Google Maps")
	key = connector.get_password("api_key", raise_exception=False) or ""
	key = key.strip()
	if not key:
		frappe.throw(
			"Google Maps API Connector has no API key.",
			frappe.ValidationError,
		)
	return key


def _populate_family_gps(limit: int | None = 50) -> dict[str, Any]:
	link_created = ensure_family_gps_link()
	api_key = _google_maps_api_key()

	sql = _FAMILIES_NEED_GPS_SQL
	if limit is not None:
		rows = frappe.db.sql(sql + " LIMIT %s", (limit,), as_dict=True)
	else:
		rows = frappe.db.sql(sql, as_dict=True)

	addresses = [_family_address(row) for row in rows]
	coord_by_address = _geocode_addresses(addresses, api_key)

	created = 0
	updated = 0
	geocode_failed = 0

	for row, address in zip(rows, addresses, strict=True):
		coords = coord_by_address.get(address)
		gps_name = row.get("gps_name")
		if isinstance(coords, tuple):
			latitude, longitude = coords
			if gps_name:
				frappe.db.set_value(
					"Family GPS",
					gps_name,
					{
						"latitude": latitude,
						"longitude": longitude,
						"error_message": "",
					},
					update_modified=True,
				)
				updated += 1
			else:
				doc = frappe.get_doc(
					{
						"doctype": "Family GPS",
						"family_id": row["name"],
						"latitude": latitude,
						"longitude": longitude,
						"error_message": "",
					}
				)
				try:
					doc.insert(ignore_permissions=True)
					created += 1
				except frappe.DuplicateEntryError:
					continue
		else:
			if isinstance(coords, str):
				message = coords.strip() or "Geocode request failed"
			else:
				message = "Geocode request failed"
			geocode_failed += 1
			if gps_name:
				frappe.db.set_value(
					"Family GPS",
					gps_name,
					{"error_message": message},
					update_modified=True,
				)
			else:
				doc = frappe.get_doc(
					{
						"doctype": "Family GPS",
						"family_id": row["name"],
						"error_message": message,
					}
				)
				try:
					doc.insert(ignore_permissions=True)
				except frappe.DuplicateEntryError:
					continue

	frappe.db.commit()
	processed = created + updated + geocode_failed
	return {
		"created": created,
		"updated": updated,
		"geocode_failed": geocode_failed,
		"processed": processed,
		"link_created": link_created,
	}


def family_gps_populate_job(user: str, limit: int | None = None, batch_id: str | None = None) -> None:
	result = _populate_family_gps(limit=limit)
	if batch_id:
		result["batch_id"] = batch_id
	frappe.publish_realtime("family_gps_populate_done", result, user=user)


@frappe.whitelist()
def populate_family_gps(all_rows=0, batch_id=None):
	"""Queue GPS population. A normal call fills 50 families; all_rows fills every family still missing coordinates."""
	if frappe.session.user == "Guest":
		frappe.throw("Not permitted", frappe.PermissionError)

	frappe.has_permission("Families", "read", throw=True)
	frappe.has_permission("Family GPS", "create", throw=True)

	ensure_family_gps_link()
	_google_maps_api_key()

	if batch_id:
		batch_id = str(batch_id).strip()[:40]
	else:
		batch_id = frappe.generate_hash(length=12)

	frappe.enqueue(
		family_gps_populate_job,
		queue="long",
		timeout=3600,
		user=frappe.session.user,
		limit=None if cint(all_rows) else 50,
		batch_id=batch_id,
	)
	return {"queued": True, "batch_id": batch_id}
