# Copyright (c) 2026, Oliver Reid and contributors
# For license information, please see license.txt

"""
Workspace utilities for NCE_Sync (V15 compatible).

V15 Workspace Structure:
- Sidebar: Shows only Workspace documents (not DocTypes)
- Workspace page has two sections:
  1. content (JSON): Shortcut cards at top of page
  2. links array: Card sections below shortcuts

Core app DocTypes (WordPress Connection, WP Tables, Sync Manager):
- Defined in workspace JSON file (committed to git)
- Appear in both shortcuts (top) and links (card section)

Mirrored DocTypes (dynamically created):
- Added only to shortcuts (content JSON)
- Appear as shortcut cards under "Mirrored Tables" header
- NOT added to links (keeps them separate from core DocTypes)

code-index:begin
{
 "purpose": "Tables workspace utilities: add/remove mirrored DocType shortcuts, sync shortcuts, and cleanup orphans.",
 "read_when": "Changing Tables workspace shortcut layout, section headers, or mirrored DocType card management.",
 "gotchas": [
  "Core workspace items including Table Links page should never be removed."
 ],
 "keywords": ["workspace_utils", "Tables workspace", "shortcut", "add_to_workspace", "remove_from_workspace", "sync_tables_workspace_shortcuts"],
 "format": 1
}
code-index:end
"""

import json

import frappe

WORKSPACE_NAME = "Tables"
NCE_SYNC_MODULE = "NCE Sync"

# Section headers used in the workspace content JSON
_HEADER_DATA_TABLES = "Data Tables"
_HEADER_REPORTS = "Reports"
_HEADER_PAGES = "Pages"


def _find_header_index(content, header_text):
	"""Return the index of the first header whose text contains header_text, or None."""
	for i, item in enumerate(content):
		if item.get("type") == "header" and header_text in item.get("data", {}).get("text", ""):
			return i
	return None


def on_doctype_change(doc, method):
	"""
	Hook called when any DocType is created or deleted.
	Clears workspace cache only if the DocType belongs to NCE Sync module.
	
	This ensures the workspace UI updates in real-time when mirrored
	DocTypes are added or removed.
	"""
	# Only clear cache for NCE Sync module DocTypes
	if doc.module == NCE_SYNC_MODULE:
		frappe.clear_cache()
		# Also publish realtime event so open browsers refresh
		frappe.publish_realtime("workspace_update", {"doctype": doc.name})


def add_to_workspace(doctype_name, label=None):
	"""
	Add a mirrored DocType as a shortcut card to the NCE Sync workspace.

	Mirrored tables are added to the content (shortcuts) section only,
	keeping them visually separate from the core app DocTypes.

	Args:
		doctype_name: Name of the DocType
		label: Optional custom label (defaults to doctype_name)
	"""
	if not frappe.db.exists("Workspace", WORKSPACE_NAME):
		return  # Workspace not yet installed; will be created by bench migrate

	workspace = frappe.get_doc("Workspace", WORKSPACE_NAME)

	# Parse existing content
	try:
		content = json.loads(workspace.content) if workspace.content else []
	except json.JSONDecodeError:
		content = []

	# Check if shortcut already exists for this DocType
	for item in content:
		if item.get("type") == "shortcut" and item.get("data", {}).get("shortcut_name") == doctype_name:
			return  # Already exists

	shortcut_entry = {
		"id": frappe.generate_hash(length=10),
		"type": "shortcut",
		"data": {
			"shortcut_name": doctype_name,
			"col": 4,
		},
	}

	# Insert before the "Reports" header so it lands in the "Data Tables" section
	insert_idx = _find_header_index(content, "Reports")
	if insert_idx is not None:
		content.insert(insert_idx, shortcut_entry)
	else:
		content.append(shortcut_entry)

	# Also add to the shortcuts child table (needed for content shortcut references)
	workspace.append(
		"shortcuts",
		{
			"label": label or doctype_name,
			"link_to": doctype_name,
			"type": "DocType",
			"doc_view": "List",
		},
	)

	# Update workspace content
	workspace.content = json.dumps(content)
	workspace.save(ignore_permissions=True)
	frappe.db.commit()

	frappe.clear_cache()


def add_report_to_workspace(report_name, label=None):
	"""
	Add a Report shortcut to the workspace under the "Reports" section.
	Inserts before the "Pages" header so it stays in the Reports section.
	"""
	if not frappe.db.exists("Workspace", WORKSPACE_NAME):
		return

	workspace = frappe.get_doc("Workspace", WORKSPACE_NAME)

	try:
		content = json.loads(workspace.content) if workspace.content else []
	except json.JSONDecodeError:
		content = []

	for item in content:
		if item.get("type") == "shortcut" and item.get("data", {}).get("shortcut_name") == report_name:
			return  # Already exists

	shortcut_entry = {
		"id": frappe.generate_hash(length=10),
		"type": "shortcut",
		"data": {
			"shortcut_name": report_name,
			"col": 4,
		},
	}

	insert_idx = _find_header_index(content, _HEADER_PAGES)
	if insert_idx is not None:
		content.insert(insert_idx, shortcut_entry)
	else:
		content.append(shortcut_entry)

	workspace.append(
		"shortcuts",
		{
			"label": label or report_name,
			"link_to": report_name,
			"type": "Report",
		},
	)

	workspace.content = json.dumps(content)
	workspace.save(ignore_permissions=True)
	frappe.db.commit()
	frappe.clear_cache()


def remove_from_workspace(doctype_name, soft_deps=None):
	"""
	Remove a mirrored DocType and all its soft-dependency artifacts from the workspace.

	Must be called BEFORE any artifacts are deleted so that workspace.save()
	validation only ever sees documents that still exist at save time.

	Args:
		doctype_name: Name of the DocType being removed.
		soft_deps: Optional dict {doctype: [names]} of artifact documents to also
		           evict from workspace links (e.g. Reports, Charts, Scripts).
	"""
	if not frappe.db.exists("Workspace", WORKSPACE_NAME):
		return

	workspace = frappe.get_doc("Workspace", WORKSPACE_NAME)

	try:
		content = json.loads(workspace.content) if workspace.content else []
	except json.JSONDecodeError:
		content = []

	# Build the set of all artifact names to evict from workspace links
	artifact_names_to_remove = set()
	if soft_deps:
		for names in soft_deps.values():
			artifact_names_to_remove.update(names)

	# Remove the DocType shortcut from content JSON
	content = [
		item for item in content
		if not (item.get("type") == "shortcut" and item.get("data", {}).get("shortcut_name") == doctype_name)
	]

	# Remove the DocType from shortcuts child table
	workspace.shortcuts = [s for s in workspace.shortcuts if s.link_to != doctype_name]

	# Remove workspace links for all soft-dependency artifacts
	# Done while those docs still exist so Frappe's validation passes on save.
	if artifact_names_to_remove:
		workspace.links = [
			lnk for lnk in workspace.links
			if lnk.link_to not in artifact_names_to_remove
		]

	workspace.content = json.dumps(content)
	workspace.save(ignore_permissions=True)
	frappe.db.commit()
	frappe.clear_cache()


def sync_tables_workspace_shortcuts():
	"""
	Ensure mirrored table shortcuts are present in the Tables workspace.
	Called when /app/tables loads. Fixes Table Links Workspace Link if needed,
	then adds any missing mirrored DocType shortcuts.
	"""
	if not frappe.db.exists("Workspace", WORKSPACE_NAME):
		return

	# Fix Table Links Workspace Link: link_type must be "Page" not "Link"
	frappe.db.sql("""
		UPDATE `tabWorkspace Link`
		SET link_type = 'Page'
		WHERE parent = %s AND parenttype = 'Workspace' AND label = 'Table Links' AND link_type = 'Link'
	""", (WORKSPACE_NAME,))
	frappe.db.commit()

	# Add shortcuts for all mirrored WP Tables that don't have one yet
	wp_tables = frappe.get_all(
		"WP Tables",
		filters={"mirror_status": "Mirrored", "frappe_doctype": ["!=", ""]},
		fields=["frappe_doctype", "nce_name"],
	)
	for row in wp_tables:
		try:
			add_to_workspace(row.frappe_doctype, label=row.nce_name or row.frappe_doctype)
		except Exception:
			pass  # Skip if add fails (e.g. DocType deleted)


@frappe.whitelist()
def is_in_workspace(doctype_name):
	"""Check if a DocType already has a shortcut in the Tables workspace."""
	if not frappe.db.exists("Workspace", WORKSPACE_NAME):
		return False
	workspace = frappe.get_doc("Workspace", WORKSPACE_NAME)
	for s in workspace.shortcuts:
		if s.link_to == doctype_name:
			return True
	return False


@frappe.whitelist()
def cleanup_orphaned_shortcuts():
	"""
	Remove workspace shortcuts for DocTypes that no longer exist.
	Useful for cleaning up after manual DocType deletions.

	Returns:
		int: Number of orphaned shortcuts removed
	"""
	if not frappe.db.exists("Workspace", WORKSPACE_NAME):
		return 0

	workspace = frappe.get_doc("Workspace", WORKSPACE_NAME)

	# Parse existing content
	try:
		content = json.loads(workspace.content) if workspace.content else []
	except json.JSONDecodeError:
		content = []

	# Core items that should never be removed (DocTypes + Table Links page)
	core_doctypes = {"WordPress Connection", "WP Tables", "Sync Manager", "Sync Log", "Table Links"}

	# Find orphaned shortcuts (DocType no longer exists, excluding core)
	cleaned_content = []
	removed_count = 0

	for item in content:
		if item.get("type") == "shortcut":
			shortcut_name = item.get("data", {}).get("shortcut_name")
			# Keep if: it's a core doctype OR it exists in DB
			if shortcut_name in core_doctypes or (shortcut_name and frappe.db.exists("DocType", shortcut_name)):
				cleaned_content.append(item)
			else:
				removed_count += 1
				if shortcut_name:
					frappe.log_error(
						title="Removed Orphaned Workspace Shortcut",
						message=f"Removed shortcut for non-existent DocType: {shortcut_name}",
					)
		else:
			cleaned_content.append(item)  # Keep non-shortcut items (headers, spacers)

	# Clean shortcuts child table (keep core doctypes)
	workspace.shortcuts = [
		s
		for s in workspace.shortcuts
		if s.link_to in core_doctypes or (s.link_to and frappe.db.exists("DocType", s.link_to))
	]

	# Save if changes were made
	if removed_count > 0:
		workspace.content = json.dumps(cleaned_content)
		workspace.save(ignore_permissions=True)
		frappe.db.commit()
		frappe.clear_cache()

		frappe.msgprint(f"Cleaned up {removed_count} orphaned workspace shortcut(s)", indicator="green")

	return removed_count
