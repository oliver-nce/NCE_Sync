# Copyright (c) 2026, Oliver Reid and contributors
# For license information, please see license.txt

"""
Background job target for Frappe → WordPress SQL write-back.

Single path: when WP Tables has listen_for_changes and write_back_mode == SQL Direct,
runs push_record_to_wp. Custom API flows use Client Script / Server Script on the
specific DocType — not configured here.

code-index:begin
{
 "purpose": "Background job entry point for deferred Frappe-to-WordPress SQL write-back dispatched from live_sync hooks.",
 "read_when": "Changing write-back job enqueue targets or the run_write_back_for_doc background handler.",
 "gotchas": [],
 "keywords": ["write_back_dispatch", "run_write_back_for_doc", "write-back", "background job", "live_sync", "SQL push"],
 "format": 1
}
code-index:end
"""

import frappe


def run_write_back_for_doc(wp_table_name, doctype, docname):
	"""
	Enqueue target (see live_sync.on_record_change).

	Do not pass Frappe doc hook name as a kwarg named ``method`` into
	``frappe.enqueue`` — that collides with enqueue's own ``method`` parameter.
	INSERT and UPDATE both use the same SQL path (push_record_to_wp).
	"""
	try:
		frappe.get_doc(doctype, docname)
	except frappe.DoesNotExistError:
		frappe.log_error(
			title="Write-back skip: document not found",
			message=f"{doctype}:{docname}",
		)
		return

	wp_table_doc = frappe.get_doc("WP Tables", wp_table_name)

	if not wp_table_doc.listen_for_changes:
		return
	if wp_table_doc.write_back_mode != "SQL Direct":
		return

	from nce_sync.utils.live_sync import push_record_to_wp

	push_record_to_wp(wp_table_name, doctype, docname)
